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


def test_phone_app_read_and_type_flow(tmp_path):
    sync = pytest.importorskip("playwright.sync_api")
    from webmacro import ocr
    if not ocr.available():
        pytest.skip("tesseract 없음")
    from test_web import FORM
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    (tmp_path / "config.yaml").write_text("url: " + (tmp_path / "form.html").as_uri()
                                          + "\nviewport: {width: 400, height: 300}\nrules: []\n", encoding="utf-8")
    ctl = Controller(tmp_path / "config.yaml", MemoryNotifier(), allow_file=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, LogBuffer(), None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("dialog", lambda d: (errors.append("native dialog: " + d.message), d.accept()))
            pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/")
            pg.click("nav.tabs button[data-tab=macros]")
            pg.click("[data-tpl=ocr]")
            pg.fill("#wText", "확인")
            pg.click("#wNext")
            pg.select_option(".sheet select", "eng")
            pg.click(".sheet .btn.pri")
            pg.wait_for_function("document.getElementById('shot').naturalWidth > 0", timeout=30000)

            def at(x, y):
                pg.locator("#shot").scroll_into_view_if_needed()
                box = pg.locator("#shot").bounding_box()
                k = box["width"] / pg.evaluate("document.getElementById('shot').naturalWidth")
                return box["x"] + x * k, box["y"] + y * k
            c = at(230, 70)
            a = at(10, 10)
            pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move(*c, steps=6); pg.mouse.up()
            pg.wait_for_selector(".sheet >> text=AB-4829", timeout=30000)
            pg.click(".sheet .btn.pri")
            pg.mouse.click(*at(100, 115))                          # 넣을 입력칸
            pg.click(".sheet .item >> text=버튼 누르기")
            pg.mouse.click(*at(80, 180))                           # 확인 버튼
            pg.wait_for_function("document.getElementById('wSteps').children.length === 4")
            pg.click("#wNext")
            pg.click("[data-after=stop]")
            pg.click("#wNext")
            pg.wait_for_function("document.getElementById('ruleList').textContent.includes('글자 읽어 입력')")
            ctl.call(lambda: ctl.driver.goto((tmp_path / "form.html").as_uri()))   # 처음 화면으로
            pg.click("nav.tabs button[data-tab=home]")
            pg.click("#power")
            pg.click(".sheet .btn.pri")
            end = time.time() + 60
            while time.time() < end and not (not ctl.running and ctl.last_result):
                time.sleep(0.5)
            assert ctl.last_result == "업무 종료"
            assert ctl.call(lambda: ctl.driver.page.inner_text("body")).strip() == "완료:AB-4829"
            b.close()
    finally:
        httpd.shutdown()
        ctl.shutdown()
    assert errors == []


def test_phone_swipe_scrolls_remote_page(tmp_path):
    sync = pytest.importorskip("playwright.sync_api")
    from test_web import LONG
    (tmp_path / "long.html").write_text(LONG, encoding="utf-8")
    (tmp_path / "config.yaml").write_text("url: " + (tmp_path / "long.html").as_uri()
                                          + "\nviewport: {width: 400, height: 300}\nrules: []\n", encoding="utf-8")
    ctl = Controller(tmp_path / "config.yaml", MemoryNotifier(), allow_file=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, LogBuffer(), None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 390, "height": 844})
            pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/")
            pg.click("nav.tabs button[data-tab=browser]")
            pg.wait_for_function("document.getElementById('shot').naturalWidth > 0", timeout=30000)
            box = pg.locator("#shot").bounding_box()
            x = box["x"] + box["width"] * 0.8
            pg.mouse.move(x, box["y"] + box["height"] * 0.8); pg.mouse.down()
            pg.mouse.move(x, box["y"] + box["height"] * 0.2, steps=8); pg.mouse.up()   # 위로 쓸기 → 아래로 스크롤
            end = time.time() + 10
            while time.time() < end and ctl.call(lambda: ctl.driver.page.evaluate("scrollY")) == 0:
                time.sleep(0.3)
            assert ctl.call(lambda: ctl.driver.page.evaluate("scrollY")) > 0
            b.close()
    finally:
        httpd.shutdown()
        ctl.shutdown()
