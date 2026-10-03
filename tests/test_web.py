"""웹 관리 화면: 실제 Chromium으로 사이트 설정 → 화면 → 조작 → 시작/중지 흐름."""
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from webmacro.notify import MemoryNotifier
from webmacro.web import Controller, LogBuffer, make_handler, set_top_level

SITE = """<!doctype html><meta charset=utf-8>
<body style="margin:0">
<input id=q style="position:absolute;left:10px;top:10px;width:200px">
<button id=b style="position:absolute;left:300px;top:200px;width:100px;height:40px;background:#E53935;border:0"
 onclick="this.remove();document.body.append('처리할 항목이 없습니다')"></button>
</body>"""


def test_set_top_level_keeps_comments():
    text = "# 주석\nurl: https://a  # old\nviewport:\n  width: 10\n  height: 20\ninterval: 2\n# 끝\n"
    out = set_top_level(text, "url", "https://b")
    out = set_top_level(out, "viewport", {"width": 1280, "height": 720})
    assert out == ('# 주석\nurl: "https://b"\nviewport: {"width": 1280, "height": 720}\ninterval: 2\n# 끝\n')
    assert set_top_level("# c\n\nrules: []\n", "url", "x").startswith('# c\n\nurl: "x"\nrules')


@pytest.fixture
def panel(tmp_path):
    pytest.importorskip("playwright.sync_api")
    (tmp_path / "site.html").write_text(SITE, encoding="utf-8")
    cfg = tmp_path / "config.yaml"   # 없으면 Controller가 새로 만든다
    ctl = Controller(cfg, MemoryNotifier())
    logbuf = LogBuffer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, logbuf, "pw"))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, ctl, tmp_path
    httpd.shutdown()
    ctl.shutdown()


def req(base, path, body=None, pw="pw"):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    if pw is not None:
        r.add_header("Authorization", "Basic " + base64.b64encode(f"u:{pw}".encode()).decode())
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw, ctype, code = resp.read(), resp.headers["Content-Type"], resp.status
    except urllib.error.HTTPError as e:
        raw, ctype, code = e.read(), e.headers.get("Content-Type", ""), e.code
    return code, (json.loads(raw) if "json" in (ctype or "") else raw)


def wait_for(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("시간 초과")


def test_auth_required(panel):
    base, _, _ = panel
    assert req(base, "/api/state", pw=None)[0] == 401
    assert req(base, "/api/state", pw="wrong")[0] == 401
    code, html = req(base, "/")
    assert code == 200 and "webmacro" in html.decode()


def test_full_flow(panel):
    base, ctl, tmp = panel
    site = (tmp / "site.html").as_uri()

    # 새 설정 파일이 만들어져 있고, 사이트 주소를 패널에서 바꾼다
    code, c = req(base, "/api/config")
    assert code == 200 and "rules:" in c["yaml"]
    code, r = req(base, "/api/settings", {"url": "ftp://x"})
    assert code == 400
    code, r = req(base, "/api/settings", {"url": site, "width": 800, "height": 600, "interval": 0.2})
    assert code == 200, r
    assert req(base, "/api/config")[1]["settings"] == {"url": site, "width": 800, "height": 600, "interval": 0.2}

    # 서버 화면 (PNG, 설정한 크기)
    code, png = req(base, "/api/screenshot")
    assert code == 200 and png[:4] == b"\x89PNG"
    import io

    from PIL import Image
    assert Image.open(io.BytesIO(png)).size == (800, 600)

    # 조작 모드: 입력칸 클릭 → 글자 입력
    assert req(base, "/api/browser", {"action": "click", "x": 50, "y": 20})[0] == 200
    assert req(base, "/api/browser", {"action": "type", "text": "hello"})[0] == 200
    assert ctl.call(lambda: ctl.driver.page.input_value("#q")) == "hello"
    assert req(base, "/api/browser", {"action": "save_session"})[0] == 200
    assert (tmp / "state" / "session.json").exists()

    # 규칙 저장: 잘못된 건 거절, 맞는 건 저장
    bad = c["yaml"].replace("then: 예시클릭", "then: 없는패턴")
    assert req(base, "/api/config", {"yaml": bad})[0] == 400
    text = req(base, "/api/config")[1]["yaml"]
    text = text.replace("rules:\n", "rules:\n  - name: 끝\n    when: {text: 처리할 항목이 없습니다}\n"
                                    "    then: [{do: notify, message: bye}]\n    after: stop\n", 1)
    code, r = req(base, "/api/config", {"yaml": text})
    assert code == 200, r

    # 지금 화면 판정: 빨간 버튼 위치
    code, t = req(base, "/api/test", {})
    assert t["rule"] == "빨간색 클릭" and t["positions"]["기본"] == [350, 220]

    # 시작 → 빨간 버튼 클릭 → '없습니다' → 업무 종료
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "업무 종료")
    st = req(base, "/api/state")[1]
    assert not st["running"]
    assert "bye" in ctl.notifier.messages
    assert not ctl.should_autostart()  # 끝났으니 재부팅 후 자동 시작 안 함


def test_stop_and_busy_and_autostart(panel):
    base, ctl, tmp = panel
    site = (tmp / "site.html").as_uri()
    req(base, "/api/settings", {"url": site, "width": 800, "height": 600})
    text = req(base, "/api/config")[1]["yaml"]
    # 계속 아무것도 안 맞는 규칙 → 계속 감시 상태
    text = text.replace('#E53935', '#123456')
    assert req(base, "/api/config", {"yaml": text})[0] == 200
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: req(base, "/api/state")[1]["status"].startswith("실행 중"))
    assert ctl.should_autostart()
    assert req(base, "/api/start", {})[0] == 409                    # 중복 시작 거절
    assert req(base, "/api/browser", {"action": "reload"})[0] == 409  # 실행 중 조작 거절
    code, png = req(base, "/api/screenshot")                          # 실행 중에도 화면 보기는 됨
    assert code == 200 and png[:4] == b"\x89PNG"
    assert req(base, "/api/stop", {})[0] == 200
    wait_for(lambda: not req(base, "/api/state")[1]["running"])
    assert req(base, "/api/state")[1]["last_result"] == "사용자 중지"
    assert not ctl.should_autostart()


def test_ocr_endpoint(panel):
    from webmacro import ocr
    if not ocr.available():
        pytest.skip("tesseract 미설치")
    base, ctl, tmp = panel
    (tmp / "o.html").write_text('<!doctype html><meta charset=utf-8><body style="margin:0">'
                                '<div style="position:absolute;left:10px;top:10px;font-size:24px">주문번호 PT-4829</div>',
                                encoding="utf-8")
    req(base, "/api/settings", {"url": (tmp / "o.html").as_uri(), "width": 800, "height": 600})
    code, r = req(base, "/api/ocr", {"region": [0, 0, 400, 60]})
    assert code == 200 and "4829" in r["text"], r
    text = req(base, "/api/config")[1]["yaml"]
    text = text[:text.index("patterns:")] + (
        "patterns:\n  p: [{do: type, text: '{var:주문번호}'}]\n"
        "rules:\n  - name: 주문\n    when:\n"
        "      - {ocr: 'PT-(\\d+)', regex: true, region: [0, 0, 400, 60], as: 주문번호}\n    then: p\n")
    code, r = req(base, "/api/config", {"yaml": text})
    assert code == 200, r
    t = req(base, "/api/test", {})[1]
    assert t["rule"] == "주문" and t["vars"] == {"주문번호": "4829"}


def raw(base, path, body=None, headers=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})},
                               method=method)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(r, timeout=60)
    except urllib.error.HTTPError as e:
        resp = e
    return resp.status if hasattr(resp, "status") else resp.code, resp.headers, resp.read()


def test_cookie_login_flow(panel):
    base, _, tmp = panel
    code, h, _ = raw(base, "/")
    assert code == 302 and h["Location"] == "/login"
    assert raw(base, "/login")[0] == 200
    assert raw(base, "/manifest.webmanifest")[0] == 200            # 로그인 없이 열림(홈 화면 추가용)
    code, h, body = raw(base, "/icon-192.png")
    assert code == 200 and body[:4] == b"\x89PNG"
    code, _, body = raw(base, "/api/state")
    assert code == 401 and json.loads(body)["login"] is True
    assert raw(base, "/api/login", {"password": "bad"})[0] == 401
    code, h, _ = raw(base, "/api/login", {"password": "pw"})
    assert code == 200
    cookie = h["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" not in cookie
    token = cookie.split(";")[0]
    code, _, body = raw(base, "/api/state", headers={"Cookie": token})
    assert code == 200 and "monitor" in json.loads(body)
    assert raw(base, "/", headers={"Cookie": token})[0] == 200
    raw(base, "/api/logout", {}, headers={"Cookie": token})
    assert raw(base, "/api/state", headers={"Cookie": token})[0] == 401


def test_secure_cookie_behind_https_proxy(tmp_path):
    from webmacro.auth import Auth
    ctl = Controller(tmp_path / "c.yaml", MemoryNotifier())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, LogBuffer(), "pw", trust_proxy=True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        code, h, _ = raw(base, "/api/login", {"password": "pw"}, headers={"X-Forwarded-Proto": "https"})
        assert code == 200 and "Secure" in h["Set-Cookie"]
        # 프록시 뒤에서는 실제 접속자 IP 기준으로 막는다
        for _ in range(5):
            raw(base, "/api/login", {"password": "x"}, headers={"X-Forwarded-For": "6.6.6.6"})
        assert raw(base, "/api/login", {"password": "pw"}, headers={"X-Forwarded-For": "6.6.6.6"})[0] == 429
        assert raw(base, "/api/login", {"password": "pw"}, headers={"X-Forwarded-For": "7.7.7.7"})[0] == 200
    finally:
        httpd.shutdown()
        ctl.shutdown()


def test_progress_visible_while_running(panel):
    base, ctl, tmp = panel
    (tmp / "site.html").write_text(SITE, encoding="utf-8")
    req(base, "/api/settings", {"url": (tmp / "site.html").as_uri(), "width": 800, "height": 600, "interval": 0.2})
    text = req(base, "/api/config")[1]["yaml"].replace(
        "rules:\n", "rules:\n  - name: 끝\n    when: {text: 처리할 항목이 없습니다}\n"
                    "    then: [{do: screenshot, name: done}]\n    after: stop\n", 1)
    assert req(base, "/api/config", {"yaml": text})[0] == 200
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "업무 종료")
    m = req(base, "/api/state")[1]["monitor"]
    assert m["today"]["done"] == 2 and m["today"]["rules"] == {"빨간색 클릭": 1, "끝": 1}
    kinds = [e["kind"] for e in m["events"]]
    assert kinds[0] == "start" and "done" in kinds and kinds[-2:] == ["stop", "end"]
    assert m["activity"].startswith("멈춤")
    # 저장된 화면 목록과 파일
    shots = req(base, "/api/shots")[1]["shots"]
    assert shots and shots[0]["name"].endswith("done.png")
    code, png = req(base, "/shots/" + shots[0]["name"])
    assert code == 200 and png[:4] == b"\x89PNG"
    assert req(base, "/shots/..%2Fconfig.yaml")[0] == 404
    assert req(base, "/shots/../config.yaml")[0] in (404, 400)
    # 최근 사건만 받기
    last = m["events"][-1]["id"]
    assert req(base, f"/api/state?since={last}")[1]["monitor"]["events"] == []


def test_region_check_endpoint(panel):
    base, _, tmp = panel
    (tmp / "r.html").write_text('<!doctype html><meta charset=utf-8><body style="margin:0;font-family:sans-serif">'
                                '<div style="position:absolute;left:20px;top:60px;font-size:20px">처리할 항목이 없습니다</div>',
                                encoding="utf-8")
    req(base, "/api/settings", {"url": (tmp / "r.html").as_uri(), "width": 800, "height": 300})
    code, r = req(base, "/api/region", {"region": [10, 50, 215, 95]})
    assert code == 200 and r["cuts"] == ["오른쪽"] and r["unfixed"] == []
    code, r2 = req(base, "/api/region", {"region": r["suggested"]})
    assert r2["cuts"] == []
    assert req(base, "/api/region", {"region": [5, 5, 1, 1]})[0] == 400
