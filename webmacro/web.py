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
import socket
import threading
import time
from collections import deque
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

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
DEMO_HTML = Path(__file__).with_name("demo.html")

DEMO_CONFIG = """\
# 처음 실행하면 만들어지는 연습용 설정 — 내장 '연습용 결재함' 페이지를 자동으로 처리합니다.
# 현황 탭에서 ▶ 시작을 눌러 보세요. 실제 사이트는 '설정' 탭에서 주소를 바꾸고 규칙을 고치면 됩니다.
url: http://127.0.0.1:{port}/demo
viewport: {{width: 1280, height: 720}}
session_file: state/session.json
interval: 1
recheck_minutes: 0

patterns:
  결재하기:
    - {{do: click_match}}                                  # 빨간 '결재' 버튼 클릭
    - {{do: wait_color, color: "#2E7D32", timeout: 5, as: 확인}}   # 초록 '확인' 팝업이 뜰 때까지
    - {{do: click_match, target: 확인}}

rules:
  - name: 할 일 없음 → 업무 종료
    when: {{text: 처리할 항목이 없습니다}}
    then: [{{do: screenshot, name: done}}]
    after: stop

  - name: 빨간 결재 버튼
    when:
      - {{color: "#E53935", tolerance: 15, min_pixels: 100}}
    then: 결재하기
"""
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
    def __init__(self, config_path: Path, notifier: Notifier | None = None, allow_file: bool | None = None,
                 demo_port: int | None = None):
        self.config_path = Path(config_path).resolve()
        # 서버 파일을 화면에 띄우지 못하게 file:// 주소는 기본 금지 (로컬 시험용으로만 WEBMACRO_ALLOW_FILE=1)
        self.allow_file = os.environ.get("WEBMACRO_ALLOW_FILE") == "1" if allow_file is None else allow_file
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
            first = DEMO_CONFIG.format(port=demo_port) if demo_port else NEW_CONFIG
            self.config_path.write_text(first, encoding="utf-8")
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
        cfg = config_mod.load(self.config_path)
        config_mod.check_urls(cfg, self.allow_file)
        return cfg

    def save_text(self, text: str):
        data = yaml.safe_load(text)
        cfg = config_mod.parse(data, base_dir=self.config_path.parent)  # 검증 실패 시 ConfigError
        config_mod.check_urls(cfg, self.allow_file)
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
                d.start(strict=reload_config)  # 패널 화면은 접속이 안 돼도 띄워 둔다
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
    def screenshot_png(self) -> tuple[bytes, str]:
        """(PNG, 현재 주소)"""
        def fn():
            d = self._ensure_driver()
            return d.page.screenshot(type="png"), d.url()
        return self.call(fn)

    def current_url(self) -> str:
        return self.call(lambda: self.driver.url() if self.driver else "", timeout=10)

    def browser_action(self, a: dict):
        if self.running:
            raise Busy("매크로 실행 중에는 화면 조작을 할 수 없습니다. 먼저 중지하세요.")
        act = a.get("action")
        if act == "goto" and not self.allow_file and not config_mod.is_web_url(str(a.get("url", ""))):
            raise ValueError("http:// 또는 https:// 주소만 열 수 있습니다")
        if act == "goto":
            check_host(str(a.get("url", "")))

        def fn():
            d = self._ensure_driver()
            if act == "click":
                d.click(int(a["x"]), int(a["y"]))
            elif act == "type":
                d.type(str(a.get("text", "")))
            elif act == "press":
                d.press(str(a["key"]))
            elif act == "scroll":
                at = (int(a["x"]), int(a["y"])) if "x" in a and "y" in a else None
                d.scroll(0, int(a.get("dy", 400)), at=at)
            elif act == "goto":
                d.goto(str(a["url"]).strip())
            elif act == "reload":
                d.reload()
            elif act == "back":
                d.back()
            elif act == "forward":
                d.forward()
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
            eng = Engine(self.load(), d, Notifier(), dry_run=True, allow_file=self.allow_file)
            hit = eng.evaluate(d.screenshot())
            if not hit:
                return {"rule": None}
            rule, matches = hit
            pos = {("기본" if k is None else k): v for k, v in matches.items()}
            return {"rule": rule.name, "pattern": rule.then, "positions": pos, "vars": eng.vars}
        return self.call(fn)

    def ocr_region(self, region, lang: str = ocr.DEFAULT_LANG, psm: int = 6, chars: str | None = None) -> dict:
        """영역 글자를 읽어 본다 (설정 만들 때 확인용)."""
        if region is not None:
            region = _region_arg(region)

        def fn():
            img = self._ensure_driver().screenshot()
            text = ocr.read_text(img, region, lang=lang, psm=psm, chars=chars)
            out = {"text": text, "cuts": ocr.edge_cuts(img, region) if region else []}
            # 한글 모드는 한글 옆 영문·코드를 자주 틀린다(PT-4829 → ㅁ1-4829). 실제 실행처럼 영어 모드 결과도 보여준다
            if "eng" in lang.split("+") and lang != "eng":
                eng_text = ocr.read_text(img, region, lang="eng", psm=psm)
                if ocr.squash(eng_text) != ocr.squash(text):
                    out["text_eng"] = eng_text
            return out
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
        if not self.load().rules:  # 설정 오류면 여기서 ConfigError
            raise config_mod.ConfigError("규칙이 없습니다. '화면' 탭에서 녹화하거나 '설정' 탭에서 규칙을 먼저 만드세요.")
        self._stop.clear()
        self.last_result = ""
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
        outcome = ""
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
                    msg = _short(e)
                    log.error("사이트 접속 실패(%d회): %s", attempt, msg)
                    self.monitor.note("error", f"사이트 접속 실패({attempt}회): {msg}")
                    if attempt == 1 or attempt % 10 == 0:
                        self.notifier.send(f"사이트 접속 실패({attempt}회): {msg}")
                    self.status = f"접속 재시도 중({attempt}회)"
                    self._sleep(min(300, 10 * 2 ** min(attempt, 5)))
            d = self.driver
            # 화면 탭에서 다른 곳을 둘러봤더라도, 매크로는 항상 업무 사이트에서 시작한다
            try:
                if d.url().rstrip("/") != cfg.url.rstrip("/"):
                    d.goto(cfg.url)
            except Exception as e:
                log.error("업무 사이트로 이동 실패: %s", e)
            self.status = "실행 중" + (" (dry-run)" if dry_run else "")

            def restart():
                d.close()
                d.start()

            eng = Engine(cfg, d, self.notifier, dry_run=dry_run, out_dir=self.out_dir, allow_file=self.allow_file,
                         sleep=self._sleep, on_event=self.monitor.on_event)
            self.monitor.started(cfg.url, dry_run)
            log.info("시작: %s (규칙 %d개%s)", cfg.url, len(cfg.rules), ", dry-run" if dry_run else "")
            if not dry_run:
                self.notifier.send(f"매크로 시작: {cfg.url}")
            result = eng.run(restart_driver=restart)
            outcome = "업무 종료" if result == "stop" else result
            if not dry_run:
                self._remember(autostart=False)
        except StopRequested:
            outcome = "사용자 중지"
            log.info("사용자 중지")
        except Exception as e:
            outcome = f"오류: {e}"
            log.exception("매크로 오류")
            self.notifier.send(f"매크로 오류로 멈춤: {e}")
        finally:
            if eng:
                eng._save_session()
            self.monitor.finished(outcome or "멈춤")
            self._stop.clear()
            self.status = "대기"
            self.running = False
            # 마지막에 기록: 결과가 보이는 순간엔 기록·상태 정리가 모두 끝나 있다
            self.last_result = outcome or "멈춤"

    def _remember(self, **kw):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(kw), encoding="utf-8")
        except OSError as e:
            log.error("상태 저장 실패: %s", e)

    # --- 규칙 편집 (휴대폰에서 YAML을 직접 고치지 않도록) ---
    def edit_config(self, change) -> "config_mod.Config":
        """설정을 dict로 읽어 change(data)로 고친 뒤 검사·저장. (주석은 사라질 수 있음)"""
        data = yaml.safe_load(self.read_text()) or {}
        change(data)
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=120)
        return self.save_text(text)

    def rules_summary(self) -> dict:
        cfg = self.load()
        out = []
        for r in cfg.rules:
            conds = []
            for c in r.when:
                if c.kind == "color":
                    conds.append(("없을 때 " if c.absent else "") + "색 #%02X%02X%02X" % tuple(c.value))
                else:
                    label = {"text": "글자", "ocr": "화면 글자", "selector": "요소", "url": "주소"}[c.kind]
                    conds.append(f"{'없을 때 ' if c.absent else ''}{label} '{c.value}'")
            steps = cfg.patterns.get(r.then, [])
            first = r.when[0] if r.when else None
            out.append({"name": r.name, "when": conds or ["(항상)"], "pattern": r.then,
                        "steps": [_step_label(st) for st in steps], "after": r.after,
                        "kind": first.kind if first else None,
                        "color": "#%02X%02X%02X" % tuple(first.value) if first and first.kind == "color" else None})
        return {"url": cfg.url, "demo": _is_demo(cfg.url), "rules": out, "refresh": cfg.refresh_on_idle,
                "interval": cfg.interval}

    def add_rule(self, body: dict):
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("규칙 이름을 입력하세요")
        steps = body.get("steps") or []
        when = body.get("when") or []
        after = body.get("after") or "continue"
        if after == "stop" and not steps:
            steps = [{"do": "screenshot", "name": "done"}]   # 종료 조건: 마지막 화면만 남기고 멈춤
        if not isinstance(steps, list) or not steps:
            raise ValueError("녹화된 단계가 없습니다")
        if not isinstance(when, list) or not when:
            raise ValueError("언제 실행할지(조건)를 정하세요")
        self._check_color_conditions(when)

        def change(data):
            patterns = data.setdefault("patterns", {}) or {}
            data["patterns"] = patterns
            rules = data.setdefault("rules", []) or []
            data["rules"] = rules
            if any(isinstance(r, dict) and r.get("name") == name for r in rules):
                raise ValueError(f"같은 이름의 규칙이 이미 있습니다: {name}")
            pname = name
            n = 2
            while pname in patterns:
                pname = f"{name}_{n}"
                n += 1
            patterns[pname] = steps
            rule = {"name": name, "when": when, "then": pname}
            if after != "continue":
                rule["after"] = after
            # '업무 종료'(stop) 규칙은 먼저 검사되도록 맨 앞, 나머지는 뒤에 붙인다
            if after == "stop":
                rules.insert(0, rule)
            else:
                rules.append(rule)
        return self.edit_config(change)

    def _check_color_conditions(self, when: list):
        """색 조건이 화면 대부분(배경색)을 덮으면 거절 — 실수로 배경을 고르면 매크로가 아무 데나 계속 누른다."""
        colors = [c for c in when if isinstance(c, dict) and "color" in c and not c.get("absent")]
        if not colors:
            return
        from .color import color_mask, parse_color

        def fn():
            return self._ensure_driver().screenshot()
        try:
            img = self.call(fn, timeout=30)
        except Exception:
            return  # 화면을 못 가져오면 검사 생략 (저장은 허용)
        for c in colors:
            region = c.get("region")
            part = img if not region else img[region[1]:region[3], region[0]:region[2]]
            if part.size == 0:
                continue
            frac = float(color_mask(part, parse_color(c["color"]), int(c.get("tolerance", 10))).mean())
            if frac > 0.25:
                raise ValueError(f"고른 색 {c['color']} 이(가) 화면의 {frac:.0%}를 차지합니다 — 배경색으로 보입니다. "
                                 "'선택' 모드에서 버튼 위를 정확히 눌러 다시 고르세요 (확대하면 쉽습니다).")

    def move_rule(self, name: str, delta: int):
        """규칙 순서 바꾸기 (위에 있을수록 먼저 검사)."""
        def change(data):
            rules = data.get("rules") or []
            i = next((k for k, r in enumerate(rules) if isinstance(r, dict) and r.get("name") == name), None)
            if i is None:
                raise ValueError(f"없는 규칙: {name}")
            j = max(0, min(len(rules) - 1, i + delta))
            rules.insert(j, rules.pop(i))
        return self.edit_config(change)

    def delete_rule(self, name: str):
        def change(data):
            rules = data.get("rules") or []
            target = next((r for r in rules if isinstance(r, dict) and r.get("name") == name), None)
            if target is None:
                raise ValueError(f"없는 규칙: {name}")
            rules.remove(target)
            then = target.get("then")
            still_used = any(isinstance(r, dict) and r.get("then") == then for r in rules)
            pats = data.get("patterns") or {}
            used_by_run = any(isinstance(st, dict) and st.get("do") == "run" and st.get("pattern") == then
                              for steps in pats.values() for st in (steps or []))
            if isinstance(then, str) and not still_used and not used_by_run:
                pats.pop(then, None)
        return self.edit_config(change)

    def set_site(self, url: str, width=None, height=None, interval=None, refresh=None) -> str:
        """업무 사이트 주소 설정. 연습 사이트에서 바뀌면 연습용 규칙은 지운다(실제 사이트를 잘못 누르지 않게)."""
        check_host(url)
        text = self.read_text()
        try:
            old_url = (yaml.safe_load(text) or {}).get("url", "")
        except yaml.YAMLError:
            old_url = ""
        text = set_top_level(text, "url", url)
        if width and height:
            text = set_top_level(text, "viewport", {"width": int(width), "height": int(height)})
        if interval:
            text = set_top_level(text, "interval", float(interval))
        if refresh is not None:
            text = set_top_level(text, "refresh_on_idle", bool(refresh))
        cleared = False
        if _is_demo(str(old_url)) and not _is_demo(url):
            data = yaml.safe_load(text) or {}
            if data.get("rules") or data.get("patterns"):
                data["patterns"], data["rules"] = {}, []
                text = "# 업무 사이트 설정 (연습용 규칙은 지움)\n" + yaml.safe_dump(
                    data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=120)
                cleared = True
        self.save_text(text)
        return "사이트 설정 저장됨" + (" · 연습용 규칙은 지웠습니다" if cleared else "")

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
    # /demo: 연습용 결재함 (서버 안 브라우저가 로그인 없이 열 수 있어야 함, 민감한 내용 없음)
    public = {"/login", "/api/login", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/demo"}

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
                elif path == "/demo":
                    self._send(200, DEMO_HTML.read_bytes(), "text/html; charset=utf-8")
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
                                "interval": cfg.interval, "refresh": cfg.refresh_on_idle}
                    except Exception as e:
                        info = {"error": str(e)}
                    self._json({"yaml": text, "settings": info, "path": str(ctl.config_path)})
                elif path == "/api/screenshot":
                    png, page_url = ctl.screenshot_png()
                    self._send(200, png, "image/png", {"X-Page-Url": quote(page_url, safe=":/?&=#%+,;@~")})
                elif path == "/api/rules":
                    self._json(ctl.rules_summary())
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
                    url = str(body.get("url", "")).strip()
                    ok = config_mod.is_web_url(url) or (ctl.allow_file and url.startswith("file://"))
                    if not ok:
                        raise config_mod.ConfigError("주소는 http:// 또는 https:// 로 시작해야 합니다")
                    message = ctl.set_site(url, body.get("width"), body.get("height"), body.get("interval"),
                                           body.get("refresh"))
                    self._json({"ok": True, "yaml": ctl.read_text(), "message": message + _restart_note(ctl)})
                elif path == "/api/rule/add":
                    cfg = ctl.add_rule(body)
                    self._json({"ok": True, "message": f"규칙 '{body.get('name')}' 저장됨 (규칙 {len(cfg.rules)}개)"
                                + _restart_note(ctl)})
                elif path == "/api/rule/move":
                    ctl.move_rule(str(body.get("name", "")), int(body.get("delta", -1)))
                    self._json({"ok": True, "message": "순서 바꿈" + _restart_note(ctl)})
                elif path == "/api/rule/delete":
                    ctl.delete_rule(str(body.get("name", "")))
                    self._json({"ok": True, "message": "삭제됨" + _restart_note(ctl)})
                elif path == "/api/browser":
                    url = ctl.browser_action(body)
                    self._json({"ok": True, "url": url})
                elif path == "/api/test":
                    self._json(ctl.test_rules())
                elif path == "/api/ocr":
                    try:
                        r = ctl.ocr_region(body.get("region"), str(body.get("lang") or ocr.DEFAULT_LANG),
                                           int(body.get("psm") or 6), body.get("chars") or None)
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


def _is_demo(url: str) -> bool:
    u = urlparse(url)
    return u.hostname in ("127.0.0.1", "localhost") and u.path.rstrip("/") == "/demo"


STEP_KO = {"click_match": "찾은 색 클릭", "click": "클릭", "click_selector": "요소 클릭", "click_text": "글자 클릭",
           "type": "입력", "press": "키", "wait": "대기", "wait_color": "색 기다림", "wait_text": "글자 기다림",
           "read": "글자 읽기", "wait_ocr": "화면 글자 기다림", "scroll": "스크롤", "goto": "이동",
           "reload": "새로고침", "screenshot": "화면 저장", "notify": "알림", "run": "패턴 실행", "stop": "종료",
           "auto_checkout": "자동 진행"}


def _step_label(st: dict) -> str:
    act = st.get("do")
    k = STEP_KO.get(act, act)
    if act == "click":
        return f"{k} ({st.get('x')}, {st.get('y')})"
    if act == "read":
        return f"{k} → {st.get('as')}"
    if act in ("type", "click_text", "wait_text"):
        t = str(st.get("text", ""))
        m = re.fullmatch(r"\{var:(.+)\}", t)
        if act == "type" and m:
            return f"읽은 값 입력 ({m.group(1)})"
        return f"{k} '{'(비공개)' if '{env:' in t else t[:30]}'"
    if act == "press":
        return f"{k} {st.get('key')}"
    if act == "wait":
        return f"{k} {st.get('sec')}초"
    if act == "goto":
        return f"{k} {str(st.get('url'))[:40]}"
    if act == "scroll":
        return f"{k} {'아래' if int(st.get('dy', 0)) > 0 else '위'}"
    if act == "auto_checkout":
        return f"{k} ({st.get('pay', '무통장입금')} → 주문 완료까지)"
    return k


def _region_arg(region) -> tuple[int, int, int, int]:
    try:
        r = tuple(int(v) for v in region)
    except (TypeError, ValueError):
        raise ValueError("영역은 [x1, y1, x2, y2]") from None
    if len(r) != 4 or r[2] <= r[0] or r[3] <= r[1]:
        raise ValueError("영역은 [x1, y1, x2, y2]")
    return r


NET_ERRORS = {
    "ERR_NAME_NOT_RESOLVED": "주소를 찾을 수 없습니다 (오타이거나 없는 사이트)",
    "ERR_CONNECTION_REFUSED": "사이트가 접속을 거부했습니다 (서버가 꺼져 있거나 포트가 다름)",
    "ERR_CONNECTION_TIMED_OUT": "사이트 응답이 없습니다 (회사 안에서만 열리는 사이트일 수 있음)",
    "ERR_ADDRESS_UNREACHABLE": "사이트에 닿을 수 없습니다 (회사 안에서만 열리는 사이트일 수 있음)",
    "ERR_CONNECTION_RESET": "접속이 끊겼습니다 (사이트가 해외·서버 접속을 막았을 수 있음)",
    "ERR_CERT_": "사이트 보안 인증서 오류 (https 대신 http 로 시도해 보세요)",
    "ERR_SSL_": "사이트 보안 연결 오류 (https 대신 http 로 시도해 보세요)",
    "ERR_INTERNET_DISCONNECTED": "서버 인터넷이 끊겼습니다",
    "ERR_TOO_MANY_REDIRECTS": "사이트가 계속 다른 주소로 넘깁니다 (로그인 상태를 다시 저장해 보세요)",
}
TLD_TYPOS = {"con": "com", "cmo": "com", "ocm": "com", "comm": "com", "co": "com", "nte": "net",
             "nett": "net", "ogr": "org", "kt": "kr", "ke": "kr", "co.ke": "co.kr", "co.kt": "co.kr"}


def _short(e: Exception) -> str:
    s = str(e) or type(e).__name__
    for code, ko in NET_ERRORS.items():
        if "net::" + code in s:
            m = re.search(r" at (\S+)", s)
            return f"{ko}: {m.group(1) if m else ''}".rstrip(": ")
    if "Timeout" in s and "exceeded" in s:
        return "사이트가 너무 느립니다 (시간 초과)"
    return s.splitlines()[0][:300]


def _typo_hint(host: str) -> str:
    for bad, good in sorted(TLD_TYPOS.items(), key=lambda kv: -len(kv[0])):
        if host.endswith("." + bad):
            return f" — 혹시 {host[:-len(bad)]}{good} ?"
    return ""


def check_host(url: str):
    """주소의 사이트 이름이 실제로 있는지 미리 확인(오타로 저장되는 것 방지). IP·localhost 는 통과."""
    host = urlparse(url.strip()).hostname or ""
    if not host or host == "localhost" or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
        return
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f"'{host}' 주소를 찾을 수 없습니다{_typo_hint(host)} (오타 확인)") from None
    except OSError:
        pass  # 확인 자체를 못 하면 막지 않는다


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
    ctl = Controller(Path(config_path), demo_port=port)
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
