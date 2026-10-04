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
    ctl = Controller(cfg, MemoryNotifier(), allow_file=True)
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
    assert code == 200 and "<title>매크로</title>" in html.decode()


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
    assert req(base, "/api/config")[1]["settings"] == {"url": site, "width": 800, "height": 600, "interval": 0.2,
                                                            "refresh": False}

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
    assert code == 200 and "4829" in (r["text"] + r.get("text_eng", "")), r
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
    ctl = Controller(tmp_path / "c.yaml", MemoryNotifier(), allow_file=True)
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


def test_file_urls_blocked_by_default(tmp_path):
    """서버에서는 file:// 로 서버 안 파일(세션·로그인 정보)을 화면에 띄울 수 없어야 한다."""
    ctl = Controller(tmp_path / "c.yaml", MemoryNotifier())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, LogBuffer(), "pw"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert req(base, "/api/settings", {"url": "file:///etc/passwd"})[0] == 400
        text = req(base, "/api/config")[1]["yaml"]
        bad = text.replace("url: https://example.com", "url: file:///data/state/auth.json")
        code, r = req(base, "/api/config", {"yaml": bad})
        assert code == 400 and "https" in r["error"]
        bad2 = text.replace("    - {do: wait, sec: 1}", "    - {do: goto, url: 'file:///etc/passwd'}")
        assert req(base, "/api/config", {"yaml": bad2})[0] == 400
        code, r = req(base, "/api/browser", {"action": "goto", "url": "file:///etc/passwd"})
        assert code == 400 and "https" in r["error"]
        # 설정 파일을 직접 고쳐 넣어도 실행·화면 보기에서 막힌다
        (tmp_path / "c.yaml").write_text(bad, encoding="utf-8")
        code, r = req(base, "/api/screenshot")
        assert code in (400, 500) and "https" in r["error"]
        assert req(base, "/api/start", {})[0] == 400
    finally:
        httpd.shutdown()
        ctl.shutdown()


def test_dry_run_not_counted(panel):
    base, ctl, tmp = panel
    (tmp / "site.html").write_text(SITE, encoding="utf-8")
    req(base, "/api/settings", {"url": (tmp / "site.html").as_uri(), "width": 800, "height": 600, "interval": 0.2})
    assert req(base, "/api/start", {"dry_run": True})[0] == 200
    wait_for(lambda: any("(시험)" in e["text"] for e in req(base, "/api/state")[1]["monitor"]["events"]))
    req(base, "/api/stop", {})
    wait_for(lambda: not req(base, "/api/state")[1]["running"])
    m = req(base, "/api/state")[1]["monitor"]
    assert m["today"]["done"] == 0 and m["today"]["rules"] == {}


def test_first_run_demo(tmp_path):
    """처음 실행: 연습용 결재함 설정이 만들어지고, 그대로 시작하면 5건 처리 후 업무 종료."""
    pytest.importorskip("playwright.sync_api")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)  # 빈 포트 얻기
    port = httpd.server_address[1]
    httpd.server_close()
    ctl = Controller(tmp_path / "data" / "config.yaml", MemoryNotifier(), demo_port=port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctl, LogBuffer(), "pw"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        assert f"http://127.0.0.1:{port}/demo" in (tmp_path / "data" / "config.yaml").read_text(encoding="utf-8")
        assert raw(base, "/demo")[0] == 200                       # 로그인 없이 열림
        assert req(base, "/api/start", {})[0] == 200
        wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "업무 종료", timeout=60)
        m = req(base, "/api/state")[1]["monitor"]
        assert m["today"]["rules"] == {"빨간 결재 버튼": 5, "할 일 없음 → 업무 종료": 1}
    finally:
        httpd.shutdown()
        ctl.shutdown()


def test_switch_from_demo_to_real_site_and_build_rule(tmp_path):
    """연습 사이트 → 업무 사이트로 바꾸면 연습 규칙이 지워지고, 규칙 없이는 시작 불가,
    녹화한 단계로 규칙을 만들면 실제로 처리된다."""
    pytest.importorskip("playwright.sync_api")
    probe = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)
    port = probe.server_address[1]
    probe.server_close()
    ctl = Controller(tmp_path / "data" / "config.yaml", MemoryNotifier(), allow_file=True, demo_port=port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctl, LogBuffer(), "pw"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    site = tmp_path / "work.html"
    site.write_text(SITE, encoding="utf-8")
    try:
        assert req(base, "/api/rules")[1]["demo"] is True
        code, r = req(base, "/api/settings", {"url": site.as_uri(), "width": 800, "height": 600, "interval": 0.2})
        assert code == 200 and "연습용 규칙은 지웠습니다" in r["message"]
        info = req(base, "/api/rules")[1]
        assert info["demo"] is False and info["rules"] == []
        code, r = req(base, "/api/start", {})
        assert code == 400 and "규칙이 없습니다" in r["error"]

        # 주소창: 이동·뒤로·앞으로, 화면 응답에 현재 주소
        assert req(base, "/api/browser", {"action": "goto", "url": f"{base}/demo"})[0] == 200
        assert req(base, "/api/browser", {"action": "back"})[1]["url"].endswith("work.html")
        assert req(base, "/api/browser", {"action": "forward"})[1]["url"].endswith("/demo")
        _, headers, _ = raw(base, "/api/screenshot", headers={"Authorization": "Basic " + base64.b64encode(b"u:pw").decode()})
        assert headers["X-Page-Url"].endswith("/demo")

        # 녹화로 만든 규칙 저장 (빨간 버튼이 보이면 → 찾은 색 클릭)
        bad = {"name": "x", "when": [{"color": "#E53935"}], "steps": []}
        assert req(base, "/api/rule/add", bad)[0] == 400
        rule = {"name": "빨간 버튼 처리", "when": [{"color": "#E53935", "tolerance": 15, "min_pixels": 30}],
                "steps": [{"do": "click_match"}, {"do": "wait", "sec": 0.5}], "after": "continue"}
        code, r = req(base, "/api/rule/add", rule)
        assert code == 200, r
        assert req(base, "/api/rule/add", rule)[0] == 400                      # 같은 이름 거절
        end = {"name": "끝", "when": [{"text": "처리할 항목이 없습니다"}], "steps": [{"do": "wait", "sec": 0.1}],
               "after": "stop"}
        assert req(base, "/api/rule/add", end)[0] == 200
        info = req(base, "/api/rules")[1]
        assert [x["name"] for x in info["rules"]] == ["끝", "빨간 버튼 처리"]    # 종료 규칙이 먼저
        assert info["rules"][1]["steps"] == ["찾은 색 클릭", "대기 0.5초"]

        assert req(base, "/api/start", {})[0] == 200
        wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "업무 종료", timeout=60)
        assert req(base, "/api/state")[1]["monitor"]["today"]["rules"] == {"빨간 버튼 처리": 1, "끝": 1}

        assert req(base, "/api/rule/delete", {"name": "빨간 버튼 처리"})[0] == 200
        assert [x["name"] for x in req(base, "/api/rules")[1]["rules"]] == ["끝"]
        assert "빨간 버튼 처리" not in (tmp_path / "data" / "config.yaml").read_text(encoding="utf-8")
    finally:
        httpd.shutdown()
        ctl.shutdown()


def test_typo_site_rejected_and_unreachable_site_keeps_screen(panel, monkeypatch):
    import socket

    from webmacro import web

    real = socket.getaddrinfo

    def fake_resolve(host, *a, **k):
        if str(host).endswith(".con"):
            raise socket.gaierror("no such host")
        return real(host, *a, **k)
    monkeypatch.setattr(web.socket, "getaddrinfo", fake_resolve)
    base, ctl, _ = panel
    code, r = req(base, "/api/settings", {"url": "https://naver.con/"})
    assert code == 400 and "naver.con" in r["error"] and "naver.com" in r["error"]
    code, r = req(base, "/api/browser", {"action": "goto", "url": "https://naver.con/"})
    assert code == 400 and "naver.com" in r["error"]

    # 접속이 안 되는 사이트를 저장해도 화면 탭은 떠야 고칠 수 있다
    assert req(base, "/api/settings", {"url": "http://127.0.0.1:1/"})[0] == 200
    code, png = req(base, "/api/screenshot")
    assert code == 200 and png[:4] == b"\x89PNG"
    assert web._short(RuntimeError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://naver.con/\nCall log")) \
        == "주소를 찾을 수 없습니다 (오타이거나 없는 사이트): https://naver.con/"


SHOP = """<!doctype html><meta charset=utf-8><body style="margin:0;background:#fff">
<script>
const n = +(localStorage.n || 0) + 1; localStorage.n = n;   // 3번째 열었을 때 아이템 등장
if (n >= 3) document.write('<button style="position:absolute;left:100px;top:100px;width:80px;height:40px;'
  + 'background:#8E24AA;border:0" onclick="document.body.innerHTML=\\'선택됨\\'"></button>');
</script></body>"""


def test_refresh_until_color_appears(panel):
    base, ctl, tmp = panel
    (tmp / "shop.html").write_text(SHOP, encoding="utf-8")
    site = (tmp / "shop.html").as_uri()
    assert req(base, "/api/settings", {"url": site, "width": 400, "height": 300, "interval": 0.2,
                                       "refresh": True})[0] == 200
    assert req(base, "/api/config")[1]["settings"]["refresh"] is True
    text = ("url: " + json.dumps(site) + "\nviewport: {width: 400, height: 300}\ninterval: 0.2\n"
            "refresh_on_idle: true\nrules:\n"
            "  - name: 끝\n    when: {text: 선택됨}\n    then: [{do: screenshot}]\n    after: stop\n"
            "  - name: 아이템\n    when: {color: '#8E24AA'}\n    then: [{do: click_match}]\n")
    assert req(base, "/api/config", {"yaml": text})[0] == 200
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "업무 종료")
    assert int(ctl.call(lambda: ctl.driver.page.evaluate("localStorage.n"))) >= 3


FORM = """<!doctype html><meta charset=utf-8><body style="margin:0;background:#fff;font-family:sans-serif">
<div id=code style="position:absolute;left:20px;top:20px;font-size:32px;color:#000">AB-4829</div>
<input id=box style="position:absolute;left:20px;top:100px;width:200px;height:30px;font-size:18px">
<button id=ok style="position:absolute;left:20px;top:160px;width:120px;height:40px"
 onclick="document.body.innerHTML='완료:'+document.getElementById('box').value">확인</button>
</body>"""


def test_read_screen_text_and_type_it(panel):
    from webmacro import ocr
    if not ocr.available():
        pytest.skip("tesseract 없음")
    base, ctl, tmp = panel
    (tmp / "form.html").write_text(FORM, encoding="utf-8")
    site = (tmp / "form.html").as_uri()
    assert req(base, "/api/settings", {"url": site, "width": 400, "height": 300, "interval": 0.2})[0] == 200
    region = [10, 10, 230, 70]
    code, r = req(base, "/api/ocr", {"region": region, "lang": "eng", "psm": 7})
    assert code == 200 and r["text"] == "AB-4829", r
    # 녹화 버튼이 만드는 것과 같은 단계: 입력칸 클릭 → 읽기 → 읽은 값 입력 → 확인
    steps = [{"do": "click", "x": 100, "y": 115},
             {"do": "read", "region": region, "as": "값1", "lang": "eng", "psm": 7},
             {"do": "type", "text": "{var:값1}"}, {"do": "click", "x": 80, "y": 180}]
    assert req(base, "/api/rule/add", {"name": "코드 입력", "when": [{"selector": "#code"}], "steps": steps})[0] == 200
    assert req(base, "/api/rule/add", {"name": "끝", "when": [{"text": "완료:"}], "after": "stop"})[0] == 200
    labels = [x["steps"] for x in req(base, "/api/rules")[1]["rules"] if x["name"] == "코드 입력"][0]
    assert "읽은 값 입력 (값1)" in labels and "글자 읽기 → 값1" in labels
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "업무 종료")
    assert ctl.call(lambda: ctl.driver.page.inner_text("body")).strip() == "완료:AB-4829"


SHOP_DIR = __import__("pathlib").Path(__file__).parent / "shop"


def _shop_rules(site_item, extra=""):
    return ("url: " + json.dumps(site_item) + "\nviewport: {width: 800, height: 600}\ninterval: 0.2\n"
            "refresh_on_idle: true\nrules:\n"
            "  - name: 아이템 구매\n    when: {color: '#8E24AA'}\n"
            "    then: [{do: click_match}, {do: auto_checkout, pay: 무통장입금, depositor: 홍길동" + extra + "}]\n")


def test_auto_checkout_bank_transfer(panel):
    import shutil
    base, ctl, tmp = panel
    shutil.copytree(SHOP_DIR, tmp / "shop")
    item = (tmp / "shop" / "item.html").as_uri()
    assert req(base, "/api/config", {"yaml": _shop_rules(item)})[0] == 200
    labels = req(base, "/api/rules")[1]["rules"][0]["steps"]
    assert labels == ["찾은 색 클릭", "자동 진행 (무통장입금 → 주문 완료까지)"]
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: not req(base, "/api/state")[1]["running"] and req(base, "/api/state")[1]["last_result"], 90)
    st = req(base, "/api/state")[1]
    assert st["last_result"] == "업무 종료", st
    url = ctl.call(lambda: ctl.driver.url())
    assert "done.html?pay=bank&bank=kb&name=%ED%99%8D%EA%B8%B8%EB%8F%99" in url
    assert any("주문 완료(무통장입금) 1건" in m for m in ctl.notifier.messages)
    texts = [e["text"] for e in req(base, "/api/state")[1]["monitor"]["events"]]
    assert any("무통장입금" in t for t in texts) and any("동의" in t for t in texts)
    assert not any("신용카드" in t or "취소" in t or "장바구니" in t for t in texts)


def test_auto_checkout_stops_on_captcha_and_unknown_field(panel):
    import shutil
    base, ctl, tmp = panel
    shutil.copytree(SHOP_DIR, tmp / "shop")
    order = tmp / "shop" / "order.html"
    order.write_text(order.read_text(encoding="utf-8").replace("<h2>주문서</h2>", "<h2>주문서</h2><p>보안문자를 입력하세요</p>"),
                     encoding="utf-8")
    item = (tmp / "shop" / "item.html").as_uri()
    assert req(base, "/api/config", {"yaml": _shop_rules(item)})[0] == 200
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: not req(base, "/api/state")[1]["running"] and req(base, "/api/state")[1]["last_result"], 90)
    assert any("보안문자" in m for m in ctl.notifier.messages)
    assert "order.html" in ctl.call(lambda: ctl.driver.url())   # 결제하지 않고 그 화면에 멈춰 있음

    # 입금자명을 모르면 멈추고 알려 준다
    order.write_text(order.read_text(encoding="utf-8").replace("<p>보안문자를 입력하세요</p>", ""), encoding="utf-8")
    assert req(base, "/api/config", {"yaml": _shop_rules(item).replace(", depositor: 홍길동", "")})[0] == 200
    ctl.notifier.messages.clear()
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: not req(base, "/api/state")[1]["running"] and req(base, "/api/state")[1]["last_result"], 90)
    assert any("입금자명" in m for m in ctl.notifier.messages), ctl.notifier.messages


LONG = """<!doctype html><meta charset=utf-8><body style="margin:0;height:3000px">
<div id=box style="position:absolute;left:0;top:0;width:200px;height:150px;overflow:auto">
<div style="height:2000px">목록</div></div></body>"""


def test_scroll_page_and_inner_box(panel):
    base, ctl, tmp = panel
    (tmp / "long.html").write_text(LONG, encoding="utf-8")
    assert req(base, "/api/settings", {"url": (tmp / "long.html").as_uri(), "width": 400, "height": 300})[0] == 200
    req(base, "/api/screenshot")
    page_y = lambda: ctl.call(lambda: ctl.driver.page.evaluate("[scrollY, box.scrollTop]"))
    # 목록 위에서 휠 → 목록만 내려감
    assert req(base, "/api/browser", {"action": "scroll", "dy": 300, "x": 100, "y": 50})[0] == 200
    assert page_y()[0] == 0 and page_y()[1] > 0
    # 매크로 단계로도 (x, y 지정)
    from webmacro import config as config_mod
    from webmacro.engine import Engine
    cfg = config_mod.parse({"url": "x", "patterns": {"p": [{"do": "scroll", "dy": 400, "x": 100, "y": 50}]}, "rules": []})
    before = page_y()[1]
    ctl.call(lambda: Engine(cfg, ctl.driver).run_pattern("p", {}))
    wait_for(lambda: page_y()[1] > before, 5)   # 휠 스크롤은 조금 늦게 반영된다
    assert page_y()[0] == 0
    # 목록 밖에서 휠 → 페이지가 내려감
    assert req(base, "/api/browser", {"action": "scroll", "dy": 300, "x": 300, "y": 200})[0] == 200
    assert page_y()[0] > 0


def test_blocked_page_stops_macro(panel):
    base, ctl, tmp = panel
    (tmp / "blocked.html").write_text("<meta charset=utf-8><h1>비정상적인 접근입니다</h1>"
                                      "<button style='width:200px;height:80px;background:#E53935'></button>", encoding="utf-8")
    site = (tmp / "blocked.html").as_uri()
    text = ("url: " + json.dumps(site) + "\nrefresh_on_idle: true\nrules:\n"
            "  - name: 빨강\n    when: {color: '#E53935'}\n    then: [{do: click_match}]\n")
    assert req(base, "/api/config", {"yaml": text})[0] == 200
    assert req(base, "/api/start", {})[0] == 200
    wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "사이트 차단 감지로 멈춤")
    st = req(base, "/api/state")[1]
    assert not st["running"] and "접근을 막음" in st["monitor"]["last_error"]["text"]


def test_http_429_counts_as_blocked(panel):
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(429); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<h1>slow down</h1>")

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, ctl, tmp = panel
    try:
        text = f"url: http://127.0.0.1:{srv.server_address[1]}/\nrules:\n  - name: a\n    when: {{text: zzz}}\n    then: [{{do: reload}}]\n"
        assert req(base, "/api/config", {"yaml": text})[0] == 200
        assert req(base, "/api/start", {})[0] == 200
        wait_for(lambda: req(base, "/api/state")[1]["last_result"] == "사이트 차단 감지로 멈춤")
        assert any("HTTP 429" in m for m in ctl.notifier.messages)
    finally:
        srv.shutdown()
