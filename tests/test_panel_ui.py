"""휴대폰 화면(390px)에서 실제로 눌러 보는 시험: 템플릿 → 색 고르기 → 무통장입금 자동 진행 → 시작 → 주문 완료."""
import shutil
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from webmacro.notify import MemoryNotifier
from webmacro.web import Controller, LogBuffer, make_handler

from test_web import SHOP_DIR


def test_phone_app_buy_flow(tmp_path):
    sync = pytest.importorskip("playwright.sync_api")
    shutil.copytree(SHOP_DIR, tmp_path / "shop")
    (tmp_path / "config.yaml").write_text("url: " + (tmp_path / "shop" / "product.html").as_uri() + "\nrules: []\n",
                                          encoding="utf-8")
    ctl = Controller(tmp_path / "config.yaml", MemoryNotifier(), allow_file=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, LogBuffer(), None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    errors = []
    try:
        with sync.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("dialog", lambda d: (errors.append("native dialog: " + d.message), d.accept()))
            pg.goto(base + "/")
            pg.click("nav.tabs button[data-tab=settings]")
            pg.fill("#url", (tmp_path / "shop" / "item.html").as_uri())
            pg.click("#btnSaveSite")
            pg.wait_for_function("document.getElementById('siteMsg').textContent.includes('저장')")
            pg.click("nav.tabs button[data-tab=macros]")
            pg.click("[data-tpl=buy]")
            pg.wait_for_function("document.getElementById('shot').naturalWidth > 0", timeout=30000)
            pg.click("#navReload")   # 두 번째로 열면 아이템 등장
            time.sleep(2.5)
            pg.locator("#shot").scroll_into_view_if_needed()
            box = pg.locator("#shot").bounding_box()
            k = box["width"] / pg.evaluate("document.getElementById('shot').naturalWidth")
            pg.mouse.click(box["x"] + 140 * k, box["y"] + 120 * k)
            assert "#8E24AA" in pg.inner_text("#pickedTx")
            pg.click("#wNext")
            pg.fill(".sheet input >> nth=1", "홍길동")
            pg.click(".sheet .btn.pri")
            assert "무통장입금 자동 진행 · 입금자 홍길동" in pg.inner_text("#wSteps")
            pg.click("#wNext")
            assert pg.is_checked("#wRefresh")
            pg.click("#wNext")
            pg.wait_for_function("document.getElementById('ruleList').textContent.includes('아이템 구매')")
            pg.click("nav.tabs button[data-tab=home]")
            pg.click("#power")
            pg.click(".sheet .btn.pri")
            end = time.time() + 90
            while time.time() < end and not (not ctl.running and ctl.last_result):
                time.sleep(0.5)
            assert ctl.last_result == "업무 종료"
            assert "done.html?pay=bank" in ctl.call(lambda: ctl.driver.url())
            b.close()
    finally:
        httpd.shutdown()
        ctl.shutdown()
    assert errors == []
