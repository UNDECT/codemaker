"""브라우저 조작 계층. 엔진은 이 인터페이스만 사용하므로 테스트에선 가짜로 바꿀 수 있다."""
from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
from PIL import Image


class Driver:
    """엔진이 쓰는 최소 인터페이스."""

    def start(self): ...
    def close(self): ...
    def screenshot(self) -> np.ndarray: raise NotImplementedError
    def save_png(self, path: Path): raise NotImplementedError
    def url(self) -> str: raise NotImplementedError
    def has_text(self, text: str) -> bool: raise NotImplementedError
    def has_selector(self, selector: str) -> bool: raise NotImplementedError
    def click(self, x: int, y: int, button: str = "left", double: bool = False): raise NotImplementedError
    def click_selector(self, selector: str, timeout: float = 5): raise NotImplementedError
    def click_text(self, text: str, exact: bool = False, timeout: float = 5): raise NotImplementedError
    def type(self, text: str, selector: str | None = None, delay: float = 0): raise NotImplementedError
    def press(self, key: str): raise NotImplementedError
    def scroll(self, dx: int, dy: int): raise NotImplementedError
    def goto(self, url: str): raise NotImplementedError
    def reload(self): raise NotImplementedError
    def back(self): raise NotImplementedError
    def forward(self): raise NotImplementedError
    def save_session(self): ...
    def auto_step(self, opts: dict) -> dict: raise NotImplementedError


def _chromium_path() -> str | None:
    """WEBMACRO_CHROMIUM 환경변수 > 기본 설치 경로. None이면 Playwright 기본값."""
    p = os.environ.get("WEBMACRO_CHROMIUM")
    if p:
        return p
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent"))
    for cand in sorted(root.glob("chromium-*/chrome-linux*/chrome"), reverse=True):
        return str(cand)
    return None


class PlaywrightDriver(Driver):
    def __init__(self, cfg):
        self.cfg = cfg
        self._pw = self._browser = self._ctx = self.page = None
        self.accept_dialogs = False   # 자동 진행 중엔 '주문하시겠습니까?' 같은 확인창을 수락
        self.last_dialog = ""

    @property
    def session_path(self) -> Path | None:
        if not self.cfg.session_file:
            return None
        return (self.cfg.base_dir / self.cfg.session_file).resolve()

    def start(self, strict: bool = True):
        """strict=False면 첫 접속이 실패해도 브라우저는 띄워 둔다(패널 화면에서 주소를 고칠 수 있게)."""
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        kw = {"headless": self.cfg.headless}
        try:
            self._browser = self._pw.chromium.launch(**kw)
        except Exception:
            path = _chromium_path()
            if not path:
                raise
            self._browser = self._pw.chromium.launch(executable_path=path, **kw)
        w, h = self.cfg.viewport
        ctx_kw = {
            "viewport": {"width": w, "height": h},
            "device_scale_factor": 1,
            "locale": self.cfg.locale,
            "timezone_id": self.cfg.timezone,
        }
        if self.cfg.user_agent:
            ctx_kw["user_agent"] = self.cfg.user_agent
        sp = self.session_path
        if sp and sp.exists():
            ctx_kw["storage_state"] = str(sp)
        self._ctx = self._browser.new_context(**ctx_kw)
        self.page = self._ctx.new_page()
        self._ctx.on("page", self._on_popup)
        self.page.on("dialog", self._on_dialog)
        try:
            self.page.goto(self.cfg.url, wait_until="domcontentloaded")
        except Exception:
            if strict:
                raise

    def close(self):
        for obj in (self._ctx, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._ctx = self.page = None

    def save_session(self):
        sp = self.session_path
        if sp and self._ctx:
            sp.parent.mkdir(parents=True, exist_ok=True)
            self._ctx.storage_state(path=str(sp))

    def screenshot(self) -> np.ndarray:
        png = self.page.screenshot(type="png")
        return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))

    def save_png(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path))

    def url(self) -> str:
        return self.page.url

    def has_text(self, text: str) -> bool:
        loc = self.page.get_by_text(text)
        return loc.count() > 0 and loc.first.is_visible()

    def has_selector(self, selector: str) -> bool:
        loc = self.page.locator(selector)
        return loc.count() > 0 and loc.first.is_visible()

    def click(self, x, y, button="left", double=False):
        if double:
            self.page.mouse.dblclick(x, y, button=button)
        else:
            self.page.mouse.click(x, y, button=button)

    def click_selector(self, selector, timeout=5):
        self.page.locator(selector).first.click(timeout=timeout * 1000)

    def click_text(self, text, exact=False, timeout=5):
        self.page.get_by_text(text, exact=exact).first.click(timeout=timeout * 1000)

    def type(self, text, selector=None, delay=0):
        if selector:
            self.page.locator(selector).first.fill(text)
        else:
            self.page.keyboard.type(text, delay=delay * 1000)

    def press(self, key):
        self.page.keyboard.press(key)

    def scroll(self, dx, dy):
        self.page.mouse.wheel(dx, dy)

    def goto(self, url):
        self.page.goto(url, wait_until="domcontentloaded")

    def reload(self):
        self.page.reload(wait_until="domcontentloaded")

    def back(self):
        self.page.go_back(wait_until="domcontentloaded")

    def forward(self):
        self.page.go_forward(wait_until="domcontentloaded")

    # ---------- 자동 진행 ----------
    def _on_dialog(self, dlg):
        self.last_dialog = dlg.message
        try:
            dlg.accept() if self.accept_dialogs else dlg.dismiss()
        except Exception:
            pass

    def _on_popup(self, page):
        """새 창(주문·결제 창)이 열리면 그 창으로 옮겨 간다."""
        page.on("dialog", self._on_dialog)
        self.page = page

    def auto_step(self, opts: dict) -> dict:
        """화면을 읽어 할 일 하나를 하고 결과를 돌려준다 (autoflow.SCAN_JS 참고)."""
        from .autoflow import SCAN_JS

        if self.page.is_closed():  # 창이 닫히면 남은 창으로
            pages = [p for p in self._ctx.pages if not p.is_closed()]
            if not pages:
                return {"kind": "none"}
            self.page = pages[-1]
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        main = self.page.main_frame
        for fr in [main] + [f for f in self.page.frames if f != main]:
            try:
                r = fr.evaluate(SCAN_JS, opts)
            except Exception:  # 다른 도메인 iframe, 이동 중인 프레임
                continue
            if r["kind"] == "none":
                continue
            el = fr.locator("[data-wm-target]").first
            if r["kind"] in ("click", "pay", "agree"):
                el.click(timeout=5000)
            elif r["kind"] == "select":
                el.select_option(value=r["value"], timeout=5000)
            elif r["kind"] == "fill":
                el.fill(r["text"], timeout=5000)
            return r
        return {"kind": "none"}
