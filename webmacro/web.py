"""웹 관리 화면(패널).

서버에는 모니터가 없으니, 브라우저(PC·휴대폰)로 접속해서
사이트 주소 설정 · 서버 화면 보기 · 색/좌표 고르기 · 원격 로그인 · 규칙 편집 · 시작/중지를 한다.

브라우저(Playwright)는 스레드를 넘나들 수 없으므로 전용 작업 스레드 하나가 전부 처리한다.
매크로 실행 중에는 엔진의 대기 시간마다 들어온 요청(화면 보기 등)을 처리한다.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import queue
import re
import threading
import time
from collections import deque
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import config as config_mod
from . import ocr
from .auth import COOKIE, MAX_AGE, Auth
from .monitor import Monitor
from .driver import PlaywrightDriver
from .engine import Engine
from .notify import Notifier

log = logging.getLogger("webmacro")

PANEL_HTML = Path(__file__).with_name("panel.html")
LOGIN_HTML = Path(__file__).with_name("login.html")
SHOT_RE = re.compile(r"^[\w.\-]+\.png$")

NEW_CONFIG = """\
# 웹 관리 화면에서 만든 설정
url: https://example.com
viewport: {width: 1920, height: 1080}
session_file: state/session.json
interval: 2

patterns:
  예시클릭:
    - {do: click_match}
    - {do: wait, sec: 1}

rules:
  - name: 빨간색 클릭
    when:
      - {color: "#E53935", tolerance: 15}
    then: 예시클릭
"""


class StopRequested(BaseException):
    """사용자 중지. 엔진의 except Exception에 잡히지 않도록 BaseException."""


class Busy(Exception):
    pass


# ---------- 설정 텍스트 편집 (주석 보존) ----------
def set_top_level(text: str, key: str, value) -> str:
    """YAML 최상위 `key:` 줄만 바꾼다. 나머지 줄·주석은 그대로 둔다."""
    line = f"{key}: {json.dumps(value, ensure_ascii=False)}"
    lines = text.splitlines()
    pat = re.compile(rf"^{re.escape(key)}\s*:")
    for i, ln in enumerate(lines):
        if pat.match(ln):
            j = i + 1
            while j < len(lines) and lines[j][:1] in (" ", "\t"):  # 블록 형식 값의 이어지는 줄
                j += 1
            lines[i:j] = [line]
            break
    else:
        k = 0
        while k < len(lines) and (lines[k].startswith("#") or not lines[k].strip()):
            k += 1
        lines.insert(k, line)
    return "\n".join(lines) + "\n"


class LogBuffer(logging.Handler):
    def __init__(self, n=300):
        super().__init__()
        self.lines: deque[str] = deque(maxlen=n)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.lines.append(self.format(record))


# ---------- 브라우저 작업 스레드 ----------
class Controller:
    def __init__(self, config_path: Path, notifier: Notifier | None = None):
        self.config_path = Path(config_path).resolve()
        self.notifier = notifier or Notifier()
        self.q: queue.Queue = queue.Queue()
        self.driver: PlaywrightDriver | None = None
        self._driver_key = None
        self.running = False
        self.dry_run = False
        self.status = "대기"
        self.last_result = ""
        self._stop = threading.Event()
        self.state_file = self.config_path.parent / "state" / "panel.json"
        self.out_dir = self.config_path.parent / "output"
        self.monitor = Monitor(self.config_path.parent / "state" / "stats.json")
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(NEW_CONFIG, encoding="utf-8")
        self.thread = threading.Thread(target=self._loop, name="browser", daemon=True)
        self.thread.start()

    # --- 큐 ---
    def call(self, fn, timeout: float = 60):
        fut: Future = Future()
        self.q.put((fn, fut))
        return fut.result(timeout)

    def _loop(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            self._exec(*item)
        self._close_driver()

    @staticmethod
    def _exec(fn, fut):
        try:
            fut.set_result(fn())
        except BaseException as e:  # noqa: BLE001 - 요청자에게 그대로 전달
            fut.set_exception(e)

    def _sleep(self, sec: float):
        """엔진 대기. 기다리는 동안 패널 요청을 처리하고, 중지 요청이면 빠져나간다."""
        end = time.monotonic() + sec
        while True:
            if self._stop.is_set():
                raise StopRequested
            rem = end - time.monotonic()
            if rem <= 0:
                return
            try:
                item = self.q.get(timeout=min(rem, 0.2))
            except queue.Empty:
                continue
            if item is None:
                self.q.put(None)
                raise StopRequested
            self._exec(*item)

    def shutdown(self):
        self._stop.set()
        self.q.put(None)
        self.thread.join(timeout=15)

    # --- 설정 ---
    def read_text(self) -> str:
        return self.config_path.read_text(encoding="utf-8")

    def load(self):
        return config_mod.load(self.config_path)

    def save_text(self, text: str):
        data = yaml.safe_load(text)
        cfg = config_mod.parse(data, base_dir=self.config_path.parent)  # 검증 실패 시 ConfigError
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.config_path)
        return cfg

    # --- 브라우저 (작업 스레드 안에서만 호출) ---
    def _ensure_driver(self, reload_config: bool = False):
        """실행 중엔 매크로가 쓰는 브라우저를 그대로 쓴다. 아니면 설정이 바뀐 경우 새로 띄운다."""
        if self.running and self.driver and not reload_config:
            return self.driver
        cfg = self.load()
        key = (cfg.url, cfg.viewport, cfg.session_file, cfg.user_agent, cfg.locale, cfg.timezone)
        if self.driver is None or key != self._driver_key:
            self._close_driver()
            cfg.headless = True
            d = PlaywrightDriver(cfg)
            try:
                d.start()
            except Exception:
                d.close()
                raise
            self.driver, self._driver_key = d, key
        return self.driver

    def _close_driver(self):
        if self.driver:
            try:
                self.driver.save_session()
            except Exception:
                pass
            self.driver.close()
        self.driver = self._driver_key = None

    # --- 패널 기능 ---
    def screenshot_png(self) -> bytes:
        def fn():
            d = self._ensure_driver()
            return d.page.screenshot(type="png")
        return self.call(fn)

    def current_url(self) -> str:
        return self.call(lambda: self.driver.url() if self.driver else "", timeout=10)

    def browser_action(self, a: dict):
        if self.running:
            raise Busy("매크로 실행 중에는 화면 조작을 할 수 없습니다. 먼저 중지하세요.")
        act = a.get("action")

        def fn():
            d = self._ensure_driver()
            if act == "click":
                d.click(int(a["x"]), int(a["y"]))
            elif act == "type":
                d.type(str(a.get("text", "")))
            elif act == "press":
                d.press(str(a["key"]))
            elif act == "scroll":
                d.scroll(0, int(a.get("dy", 400)))
            elif act == "goto":
                d.goto(str(a["url"]))
            elif act == "reload":
                d.reload()
            elif act == "home":
                d.goto(self.load().url)
            elif act == "save_session":
                if not self.load().session_file:
                    raise ValueError("설정에 session_file 이 없습니다")
                d.save_session()
            else:
                raise ValueError(f"알 수 없는 동작: {act}")
            time.sleep(0.3)  # 화면 반영 대기
            return d.url()
        return self.call(fn)

    def test_rules(self) -> dict:
        """지금 화면에서 어떤 규칙이 맞는지 (아무것도 누르지 않음)."""
        def fn():
            d = self._ensure_driver()
            eng = Engine(self.load(), d, Notifier(), dry_run=True)
            hit = eng.evaluate(d.screenshot())
            if not hit:
                return {"rule": None}
            rule, matches = hit
            pos = {("기본" if k is None else k): v for k, v in matches.items()}
            return {"rule": rule.name, "pattern": rule.then, "positions": pos, "vars": eng.vars}
        return self.call(fn)

    def ocr_region(self, region, lang: str = ocr.DEFAULT_LANG, psm: int = 6) -> dict:
        """영역 글자를 읽어 본다 (설정 만들 때 확인용)."""
        if region is not None:
            region = _region_arg(region)

        def fn():
            img = self._ensure_driver().screenshot()
            text = ocr.read_text(img, region, lang=lang, psm=psm)
            return {"text": text, "cuts": ocr.edge_cuts(img, region) if region else []}
        return self.call(fn)

    def check_region(self, region) -> dict:
        """영역이 글자를 자르는지 + 자동으로 맞춘 영역."""
        region = _region_arg(region)

        def fn():
            img = self._ensure_driver().screenshot()
            cuts = ocr.edge_cuts(img, region)
            fit, left = ocr.fit_region(img, region)
            return {"cuts": cuts, "suggested": list(fit), "unfixed": left}
        return self.call(fn)

    def start(self, dry_run: bool = False):
        if self.running:
            raise Busy("이미 실행 중입니다")
        self.load()  # 설정 오류면 여기서 ConfigError
        self._stop.clear()
        self.running, self.dry_run = True, dry_run
        self.status = "시작 중"
        if not dry_run:
            self._remember(autostart=True)
        self.q.put((lambda: self._run_macro(dry_run), Future()))

    def stop(self):
        self._remember(autostart=False)
        if self.running:
            self.status = "중지 중"
            self._stop.set()

    def _run_macro(self, dry_run: bool):
        eng = None
        try:
            cfg = self.load()
            # 사이트 접속 재시도 (중지 가능)
            attempt = 0
            while True:
                try:
                    self._ensure_driver(reload_config=True)
                    break
                except Exception as e:
                    attempt += 1
                    msg = str(e).splitlines()[0]
                    log.error("사이트 접속 실패(%d회): %s", attempt, msg)
                    self.monitor.note("error", f"사이트 접속 실패({attempt}회): {msg}")
                    if attempt == 1 or attempt % 10 == 0:
                        self.notifier.send(f"사이트 접속 실패({attempt}회): {msg}")
                    self.status = f"접속 재시도 중({attempt}회)"
                    self._sleep(min(300, 10 * 2 ** min(attempt, 5)))
            self.status = "실행 중" + (" (dry-run)" if dry_run else "")
            d = self.driver

            def restart():
                d.close()
                d.start()

            eng = Engine(cfg, d, self.notifier, dry_run=dry_run, out_dir=self.out_dir,
                         sleep=self._sleep, on_event=self.monitor.on_event)
            self.monitor.started(cfg.url, dry_run)
            log.info("시작: %s (규칙 %d개%s)", cfg.url, len(cfg.rules), ", dry-run" if dry_run else "")
            if not dry_run:
                self.notifier.send(f"매크로 시작: {cfg.url}")
            result = eng.run(restart_driver=restart)
            self.last_result = "업무 종료" if result == "stop" else result
            if not dry_run:
                self._remember(autostart=False)
        except StopRequested:
            self.last_result = "사용자 중지"
            log.info("사용자 중지")
        except Exception as e:
            self.last_result = f"오류: {e}"
            log.exception("매크로 오류")
            self.notifier.send(f"매크로 오류로 멈춤: {e}")
        finally:
            if eng:
                eng._save_session()
            self.monitor.finished(self.last_result or "멈춤")
            self.running = False
            self.status = "대기"
            self._stop.clear()

    def _remember(self, **kw):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(kw), encoding="utf-8")
        except OSError as e:
            log.error("상태 저장 실패: %s", e)

    def list_shots(self, n: int = 40) -> list[dict]:
        try:
            files = sorted(self.out_dir.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)[:n]
        except OSError:
            return []
        return [{"name": f.name, "t": f.stat().st_mtime} for f in files]

    def should_autostart(self) -> bool:
        try:
            return bool(json.loads(self.state_file.read_text(encoding="utf-8")).get("autostart"))
        except (OSError, ValueError):
            return False


# ---------- HTTP ----------
def make_handler(ctl: Controller, logbuf: LogBuffer, password: str | None, auth: Auth | None = None,
                 trust_proxy: bool | None = None):
    auth = auth or Auth(password, ctl.config_path.parent / "state" / "auth.json")
    if trust_proxy is None:
        trust_proxy = os.environ.get("WEBMACRO_TRUST_PROXY") == "1"
    public = {"/login", "/api/login", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "webmacro"

        def log_message(self, fmt, *args):  # 접속 로그는 생략
            pass

        # --- 인증 ---
        def _client(self) -> str:
            if trust_proxy and self.headers.get("X-Forwarded-For"):
                return self.headers["X-Forwarded-For"].split(",")[0].strip()
            return self.client_address[0]

        def _https(self) -> bool:
            return trust_proxy and self.headers.get("X-Forwarded-Proto") == "https"

        def _cookie_token(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v
            return None

        def _auth_ok(self) -> bool:
            if not auth.enabled:
                return True
            if auth.valid(self._cookie_token()):
                return True
            h = self.headers.get("Authorization", "")  # 스크립트·curl용
            if h.startswith("Basic "):
                try:
                    pw = base64.b64decode(h[6:]).decode("utf-8").split(":", 1)[-1]
                except Exception:
                    return False
                if auth.blocked(self._client()):
                    return False
                if auth.check_password(pw):
                    return True
                auth.record_fail(self._client())
            return False

        def _guard(self, path: str) -> bool:
            if path in public or self._auth_ok():
                return True
            if path.startswith("/api/") or path.startswith("/shots/"):
                self._json({"error": "로그인이 필요합니다", "login": True}, 401)
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return False

        # --- 응답 ---
        def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200, headers=None):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                       headers)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ValueError("요청이 너무 큽니다")
            return json.loads(self.rfile.read(n) or b"{}")

        def _cookie(self, value: str, max_age: int) -> dict:
            c = f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict"
            if self._https():
                c += "; Secure"
            return {"Set-Cookie": c}

        # --- GET ---
        def do_GET(self):
            u = urlparse(self.path)
            path = u.path
            if not self._guard(path):
                return
            try:
                if path == "/":
                    self._send(200, PANEL_HTML.read_bytes(), "text/html; charset=utf-8")
                elif path == "/login":
                    self._send(200, LOGIN_HTML.read_bytes(), "text/html; charset=utf-8")
                elif path == "/manifest.webmanifest":
                    self._send(200, json.dumps(MANIFEST, ensure_ascii=False).encode(),
                               "application/manifest+json", cache="max-age=86400")
                elif path in ("/icon-192.png", "/icon-512.png"):
                    self._send(200, _icon(192 if "192" in path else 512), "image/png", cache="max-age=86400")
                elif path == "/api/state":
                    since = int((parse_qs(u.query).get("since") or ["0"])[0] or 0)
                    self._json({"running": ctl.running, "status": ctl.status, "last_result": ctl.last_result,
                                "dry_run": ctl.dry_run, "log": list(logbuf.lines)[-120:],
                                "monitor": ctl.monitor.snapshot(since), "auth": auth.enabled})
                elif path == "/api/config":
                    text = ctl.read_text()
                    try:
                        cfg = ctl.load()
                        info = {"url": cfg.url, "width": cfg.viewport[0], "height": cfg.viewport[1],
                                "interval": cfg.interval}
                    except Exception as e:
                        info = {"error": str(e)}
                    self._json({"yaml": text, "settings": info, "path": str(ctl.config_path)})
                elif path == "/api/screenshot":
                    self._send(200, ctl.screenshot_png(), "image/png")
                elif path == "/api/shots":
                    self._json({"shots": ctl.list_shots()})
                elif path.startswith("/shots/"):
                    name = path[len("/shots/"):]
                    f = ctl.out_dir / name
                    if not SHOT_RE.match(name) or not f.is_file():
                        self._json({"error": "없는 파일"}, 404)
                    else:
                        self._send(200, f.read_bytes(), "image/png", cache="private, max-age=3600")
                else:
                    self._json({"error": "없는 주소"}, 404)
            except Exception as e:
                log.error("요청 실패 %s: %s", path, e)
                self._json({"error": _short(e)}, 500)

        # --- POST ---
        def do_POST(self):
            path = urlparse(self.path).path
            if not self._guard(path):
                return
            try:
                body = self._body()
                if path == "/api/login":
                    who = self._client()
                    if auth.blocked(who):
                        self._json({"error": "너무 많이 틀렸습니다. 5분 뒤 다시 시도하세요."}, 429)
                        return
                    token = auth.login(who, str(body.get("password", "")))
                    if not token:
                        self._json({"error": "비밀번호가 틀렸습니다"}, 401)
                        return
                    log.info("관리 화면 로그인 (%s)", who)
                    self._json({"ok": True}, headers=self._cookie(token, MAX_AGE))
                elif path == "/api/logout":
                    auth.logout(self._cookie_token())
                    self._json({"ok": True}, headers=self._cookie("", 0))
                elif path == "/api/config":
                    cfg = ctl.save_text(body["yaml"])
                    self._json({"ok": True, "message": _summary(cfg) + _restart_note(ctl)})
                elif path == "/api/settings":
                    text = ctl.read_text()
                    url = str(body.get("url", "")).strip()
                    if not re.match(r"^(https?|file)://", url):
                        raise config_mod.ConfigError("주소는 http:// 또는 https:// 로 시작해야 합니다")
                    text = set_top_level(text, "url", url)
                    if body.get("width") and body.get("height"):
                        text = set_top_level(text, "viewport",
                                             {"width": int(body["width"]), "height": int(body["height"])})
                    if body.get("interval"):
                        text = set_top_level(text, "interval", float(body["interval"]))
                    ctl.save_text(text)
                    self._json({"ok": True, "yaml": text, "message": "사이트 설정 저장됨" + _restart_note(ctl)})
                elif path == "/api/browser":
                    url = ctl.browser_action(body)
                    self._json({"ok": True, "url": url})
                elif path == "/api/test":
                    self._json(ctl.test_rules())
                elif path == "/api/ocr":
                    try:
                        r = ctl.ocr_region(body.get("region"), str(body.get("lang") or ocr.DEFAULT_LANG),
                                           int(body.get("psm") or 6))
                    except ocr.OcrError as e:
                        self._json({"error": f"OCR 실패: {e}"}, 500)
                        return
                    self._json(r)
                elif path == "/api/region":
                    self._json(ctl.check_region(body.get("region")))
                elif path == "/api/start":
                    ctl.start(dry_run=bool(body.get("dry_run")))
                    self._json({"ok": True})
                elif path == "/api/stop":
                    ctl.stop()
                    self._json({"ok": True})
                else:
                    self._json({"error": "없는 주소"}, 404)
            except Busy as e:
                self._json({"error": str(e)}, 409)
            except (config_mod.ConfigError, yaml.YAMLError, ValueError, KeyError) as e:
                self._json({"error": f"설정 오류: {_short(e)}"}, 400)
            except Exception as e:
                log.error("요청 실패 %s: %s", path, e)
                self._json({"error": _short(e)}, 500)

    return Handler


MANIFEST = {
    "name": "webmacro 업무 매크로", "short_name": "매크로", "start_url": "/", "display": "standalone",
    "background_color": "#111418", "theme_color": "#2563eb", "lang": "ko",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}
_ICONS: dict[int, bytes] = {}


def _icon(size: int) -> bytes:
    """홈 화면 아이콘: 파란 바탕에 흰 재생(▶) 모양."""
    if size not in _ICONS:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (size, size), (37, 99, 235))
        d = ImageDraw.Draw(im)
        c, r = size / 2, size * 0.24
        d.polygon([(c - r * 0.7, c - r), (c - r * 0.7, c + r), (c + r, c)], fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        _ICONS[size] = buf.getvalue()
    return _ICONS[size]


def _region_arg(region) -> tuple[int, int, int, int]:
    try:
        r = tuple(int(v) for v in region)
    except (TypeError, ValueError):
        raise ValueError("영역은 [x1, y1, x2, y2]") from None
    if len(r) != 4 or r[2] <= r[0] or r[3] <= r[1]:
        raise ValueError("영역은 [x1, y1, x2, y2]")
    return r


def _short(e: Exception) -> str:
    s = str(e) or type(e).__name__
    return s.splitlines()[0][:300]


def _summary(cfg) -> str:
    n_pat = len([p for p in cfg.patterns if not p.startswith("__")])
    return f"저장됨: 규칙 {len(cfg.rules)}개, 패턴 {n_pat}개"


def _restart_note(ctl: Controller) -> str:
    return " (실행 중인 매크로에는 중지 후 다시 시작해야 적용)" if ctl.running else ""


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def serve(config_path: str, host: str = "127.0.0.1", port: int = 8080,
          password: str | None = None, autostart: bool = True):
    password = password if password is not None else os.environ.get("WEBMACRO_PANEL_PASSWORD")
    if not password and not is_loopback(host):
        raise SystemExit("외부 접속을 허용하려면 WEBMACRO_PANEL_PASSWORD 환경변수로 비밀번호를 정하세요.")
    logbuf = LogBuffer()
    log.addHandler(logbuf)
    log.setLevel(logging.INFO)
    ctl = Controller(Path(config_path))
    httpd = ThreadingHTTPServer((host, port), make_handler(ctl, logbuf, password))
    log.info("관리 화면: http://%s:%d  (설정: %s)", host, httpd.server_address[1], ctl.config_path)
    if autostart and ctl.should_autostart():
        log.info("이전에 실행 중이었으므로 자동 시작")
        try:
            ctl.start()
        except Exception as e:
            log.error("자동 시작 실패: %s", e)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        ctl.shutdown()
