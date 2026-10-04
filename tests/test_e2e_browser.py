"""실제 Chromium으로 로컬 웹페이지를 띄워 색상 클릭 → 확인 팝업 → 업무 종료까지 확인."""
import pytest

from webmacro.config import parse
from webmacro.engine import Engine
from webmacro.notify import MemoryNotifier

PAGE = """<!doctype html><meta charset=utf-8>
<style>
 body{margin:0;font:16px sans-serif;background:#fff}
 .item{position:absolute;width:120px;height:40px;border:0;color:#fff}
 #ok{position:absolute;left:900px;top:500px;width:100px;height:40px;background:#2E7D32;border:0;display:none}
</style>
<div id=list></div><button id=ok>확인</button><p id=msg></p>
<input id=memo style="position:absolute;left:10px;top:600px">
<script>
 let left = 3, typed = [];
 const colors = ['#E53935', '#FBC02D', '#E53935'];
 function render(){
   const l = document.getElementById('list'); l.innerHTML='';
   if(left===0){ document.getElementById('msg').textContent='처리할 항목이 없습니다'; return; }
   const b=document.createElement('button'); b.className='item';
   b.style.left=(100+left*150)+'px'; b.style.top=(100+left*60)+'px';
   b.style.background=colors[left-1]; b.textContent= colors[left-1]==='#FBC02D'?'반려 요청':'';
   b.onclick=()=>{ document.getElementById('ok').style.display='block'; };
   l.appendChild(b);
 }
 document.getElementById('ok').onclick=()=>{
   document.getElementById('ok').style.display='none'; left--; render();
 };
 render();
</script>"""


@pytest.fixture
def driver_factory(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")  # noqa: F841
    from webmacro.driver import PlaywrightDriver
    page = tmp_path / "site.html"
    page.write_text(PAGE, encoding="utf-8")
    made = []

    def make(cfg):
        d = PlaywrightDriver(cfg)
        try:
            d.start()
        except Exception as e:
            pytest.skip(f"Chromium 실행 불가: {e}")
        made.append(d)
        return d
    yield page.as_uri(), make
    for d in made:
        d.close()


def test_color_click_flow_until_done(driver_factory, tmp_path):
    url, make = driver_factory
    cfg = parse({
        "url": url, "viewport": {"width": 1280, "height": 720}, "interval": 0.1,
        "session_file": "state/s.json",
        "patterns": {
            "결재": [{"do": "click_match"},
                     {"do": "wait_color", "color": "#2E7D32", "timeout": 5, "as": "ok"},
                     {"do": "click_match", "target": "ok"}],
            "반려": [{"do": "click_match", "target": "y"},
                     {"do": "wait_color", "color": "#2E7D32", "timeout": 5, "as": "ok"},
                     {"do": "click_match", "target": "ok"}],
        },
        "rules": [
            {"name": "끝", "when": {"text": "처리할 항목이 없습니다"}, "then": [{"do": "screenshot", "name": "done"}],
             "after": "stop"},
            {"name": "노랑+반려", "when": [{"color": "#FBC02D", "as": "y"}, {"text": "반려 요청"}], "then": "반려"},
            {"name": "빨강", "when": {"color": "#E53935", "tolerance": 15}, "then": "결재"},
        ],
    }, base_dir=tmp_path)
    d = make(cfg)
    n = MemoryNotifier()
    eng = Engine(cfg, d, n, out_dir=tmp_path / "out")
    assert eng.run(max_ticks=30) == "stop"
    assert eng.history == ["빨강", "노랑+반려", "빨강", "끝"]
    assert any(p.name.endswith("done.png") for p in (tmp_path / "out").iterdir())
    assert (tmp_path / "state" / "s.json").exists()  # 로그인 세션 저장됨


def test_type_into_selector_and_url_condition(driver_factory, tmp_path, monkeypatch):
    url, make = driver_factory
    monkeypatch.setenv("MEMO", "비밀값")
    cfg = parse({
        "url": url, "viewport": {"width": 1280, "height": 720},
        "patterns": {"p": [{"do": "type", "selector": "#memo", "text": "{env:MEMO}"}]},
        "rules": [{"when": {"url": "site.html"}, "then": "p", "after": "stop"}],
    }, base_dir=tmp_path)
    d = make(cfg)
    eng = Engine(cfg, d, MemoryNotifier(), out_dir=tmp_path)
    assert eng.tick() == "stop"
    assert d.page.input_value("#memo") == "비밀값"


SLOW = """<!doctype html><meta charset=utf-8><body style="margin:0">
<button id=b style="position:absolute;left:200px;top:100px;width:120px;height:40px;background:#E53935;border:0"></button>
<p id=n style="position:absolute;left:10px;top:300px">0</p>
<script>
 let left = 3, clicks = 0, busy = false;
 const b = document.getElementById('b');
 b.onclick = () => {
   clicks++; document.getElementById('n').textContent = clicks;
   if (busy) return; busy = true;
   setTimeout(() => {                       // 느린 사이트: 0.8초 뒤에야 버튼이 사라진다
     b.style.display = 'none'; left--;
     setTimeout(() => { busy = false;
       if (left > 0) b.style.display = 'block';
       else document.body.insertAdjacentHTML('beforeend', '<h1>처리할 항목이 없습니다</h1>');
     }, 700);
   }, 800);
 };
</script>"""


def test_slow_site_each_item_clicked_once(driver_factory, tmp_path):
    _, make = driver_factory
    page = tmp_path / "slow.html"
    page.write_text(SLOW, encoding="utf-8")
    cfg = parse({
        "url": page.as_uri(), "viewport": {"width": 800, "height": 400}, "interval": 0.2,
        "patterns": {"p": [{"do": "click_match"}]},          # 일부러 wait 없이
        "rules": [
            {"name": "끝", "when": {"text": "처리할 항목이 없습니다"}, "then": [{"do": "stop"}]},
            {"name": "빨강", "when": {"color": "#E53935"}, "then": "p"},
        ],
    }, base_dir=tmp_path)
    d = make(cfg)
    eng = Engine(cfg, d, MemoryNotifier(), out_dir=tmp_path / "out")
    assert eng.run(max_ticks=60) == "stop"
    assert d.page.inner_text("#n") == "3"                    # 항목 3개 → 정확히 3번 클릭
    assert eng.history == ["빨강", "빨강", "빨강", "끝"]
