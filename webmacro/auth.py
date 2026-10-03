"""관리 화면 로그인 (휴대폰에서 쓰기 편한 쿠키 방식).

- 비밀번호 1개(WEBMACRO_PANEL_PASSWORD). 로그인하면 30일 유지되는 쿠키를 준다.
- 쿠키 토큰은 해시만 state/auth.json 에 저장 → 서버 재시작해도 다시 로그인할 필요 없음.
- 5분 안에 5번 틀리면 잠시 막는다(무차별 대입 방지).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

COOKIE = "wm_session"
MAX_AGE = 30 * 24 * 3600
FAIL_WINDOW = 300
FAIL_LIMIT = 5


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, password: str | None, store: Path | None = None, clock=time.time):
        self.password = password or None
        self.store = store
        self.clock = clock
        self.lock = threading.Lock()
        self.tokens: dict[str, float] = {}   # 해시 → 만료 시각
        self.fails: dict[str, list[float]] = {}
        self._load()

    @property
    def enabled(self) -> bool:
        return self.password is not None

    def _load(self):
        if not self.store:
            return
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
            now = self.clock()
            self.tokens = {k: v for k, v in data.items() if v > now}
        except (OSError, ValueError, AttributeError):
            self.tokens = {}

    def _save(self):
        if not self.store:
            return
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps(self.tokens), encoding="utf-8")
            self.store.chmod(0o600)
        except OSError:
            pass

    def check_password(self, pw: str) -> bool:
        return bool(self.password) and hmac.compare_digest(pw.encode(), self.password.encode())

    def blocked(self, who: str) -> bool:
        with self.lock:
            now = self.clock()
            recent = [t for t in self.fails.get(who, []) if now - t < FAIL_WINDOW]
            self.fails[who] = recent
            return len(recent) >= FAIL_LIMIT

    def record_fail(self, who: str):
        with self.lock:
            self.fails.setdefault(who, []).append(self.clock())

    def login(self, who: str, pw: str) -> str | None:
        """맞으면 새 토큰, 틀리면 None."""
        if self.blocked(who):
            return None
        if not self.check_password(pw):
            self.record_fail(who)
            return None
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.fails.pop(who, None)
            now = self.clock()
            self.tokens = {k: v for k, v in self.tokens.items() if v > now}
            self.tokens[_h(token)] = now + MAX_AGE
            self._save()
        return token

    def logout(self, token: str | None):
        if token:
            with self.lock:
                self.tokens.pop(_h(token), None)
                self._save()

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            exp = self.tokens.get(_h(token))
            return exp is not None and exp > self.clock()
