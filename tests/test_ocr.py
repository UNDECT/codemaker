import numpy as np
import pytest

from webmacro import ocr
from webmacro.config import ConfigError, parse
from webmacro.notify import MemoryNotifier

from test_engine import FakeDriver, engine


def test_find_and_squash():
    assert ocr.find("결재 대기 3건", "결재대기") is True        # 공백 무시
    assert ocr.find("결재 완료", "결재대기") is None
    assert ocr.find("주문번호: PT-4829 접수", r"PT-(\d+)", regex=True) == "4829"
    assert ocr.find("합계 12,000원", r"\d[\d,]*원", regex=True) == "12,000원"
    assert ocr.find("인 증 코 드 7351", r"인증코드(\d+)", regex=True) == "7351"  # 공백 섞여도 재시도


def test_config_validation():
    base = {"url": "https://x", "patterns": {"p": [{"do": "reload"}]}}
    with pytest.raises(ConfigError, match="정규식"):
        parse({**base, "rules": [{"when": {"ocr": "(", "regex": True}, "then": "p"}]})
    with pytest.raises(ConfigError, match="region"):
        parse({**base, "patterns": {"p": [{"do": "read", "as": "x"}]},
               "rules": [{"when": {"url": "a"}, "then": "p"}]})
    with pytest.raises(ConfigError, match="알 수 없는 키"):
        parse({**base, "rules": [{"when": {"ocr": "a", "tolerance": 3}, "then": "p"}]})
    with pytest.raises(ConfigError, match="따옴표"):
        parse({**base, "rules": [{"when": {"ocr": "a", "as": False}, "then": "p"}]})
    cfg = parse({**base, "rules": [{"when": {"ocr": "a", "region": [0, 0, 10, 10], "psm": 7}, "then": "p"}]})
    c = cfg.rules[0].when[0]
    assert (c.kind, c.region, c.psm, c.lang) == ("ocr", (0, 0, 10, 10), 7, "kor+eng")


def fake_ocr(monkeypatch, screen: dict):
    """영역별로 돌려줄 글자를 정해 둔 가짜 OCR."""
    calls = []

    def read_text(img, region=None, lang="", psm=6, **kw):
        calls.append(region)
        v = screen.get(tuple(region) if region else None, "")
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(ocr, "read_text", read_text)
    return calls


def test_ocr_condition_captures_value_and_types_it(monkeypatch):
    calls = fake_ocr(monkeypatch, {(0, 0, 100, 30): "주문번호: PT-4829"})
    data = {"patterns": {"p": [{"do": "type", "selector": "#q", "text": "{var:no} 처리"}]},
            "rules": [
                {"name": "없음", "when": {"ocr": "없는글자", "region": [0, 0, 100, 30]}, "then": "p"},
                {"name": "주문", "when": {"ocr": r"PT-(\d+)", "regex": True, "region": [0, 0, 100, 30], "as": "no"},
                 "then": "p"},
            ]}
    eng, drv, _, _ = engine(data)
    assert eng.tick() == "ran:주문"
    assert ("type", "4829 처리", "#q") in drv.log
    assert len(calls) == 1  # 같은 영역은 한 화면에 한 번만 OCR


def test_read_step_and_pattern(monkeypatch):
    fake_ocr(monkeypatch, {(10, 10, 200, 40): "고객명 홍길동 / 금액 12,000원"})
    data = {"patterns": {"p": [
        {"do": "read", "region": [10, 10, 200, 40], "as": "amt", "pattern": r"금액\s*([\d,]+)"},
        {"do": "read", "region": [10, 10, 200, 40], "as": "all"},
        {"do": "type", "text": "{var:amt}"},
        {"do": "notify", "message": "처리: {var:all}"}]},
        "rules": [{"when": {"text": "go"}, "then": "p", "after": "stop"}]}
    eng, drv, n, _ = engine(data)
    drv.texts.add("go")
    assert eng.tick() == "stop"
    assert ("type", "12,000", None) in drv.log
    assert "처리: 고객명 홍길동 / 금액 12,000원" in n.messages


def test_read_fails_closed_when_nothing_matches(monkeypatch):
    fake_ocr(monkeypatch, {(0, 0, 50, 50): "알 수 없는 화면"})
    data = {"patterns": {"p": [{"do": "read", "region": [0, 0, 50, 50], "as": "x", "pattern": r"(\d{4})"},
                               {"do": "type", "text": "{var:x}"}]},
            "rules": [{"when": {"text": "go"}, "then": "p"}]}
    eng, drv, n, _ = engine(data)
    drv.texts.add("go")
    assert eng.tick() == "error"
    assert not any(e[0] == "type" for e in drv.log)   # 엉뚱한 값을 입력하지 않음
    assert any("못 찾음" in m for m in n.messages)


def test_missing_variable_fails(monkeypatch):
    data = {"patterns": {"p": [{"do": "type", "text": "{var:nope}"}]},
            "rules": [{"when": {"text": "go"}, "then": "p"}]}
    eng, drv, n, _ = engine(data)
    drv.texts.add("go")
    assert eng.tick() == "error" and any("변수" in m for m in n.messages)


def test_ocr_error_means_no_action(monkeypatch):
    fake_ocr(monkeypatch, {(0, 0, 10, 10): ocr.OcrError("tesseract 없음")})
    data = {"patterns": {"p": [{"do": "click", "x": 1, "y": 1}]},
            "rules": [{"when": {"ocr": "x", "region": [0, 0, 10, 10], "absent": True}, "then": "p"}]}
    eng, drv, n, _ = engine(data)
    assert eng.tick() == "idle" and eng.tick() == "idle"
    assert drv.clicks() == []
    assert len([m for m in n.messages if "OCR 실패" in m]) == 1


def test_dry_run_reads_but_does_not_type(monkeypatch, caplog):
    fake_ocr(monkeypatch, {(0, 0, 50, 50): "코드 7351"})
    data = {"patterns": {"p": [{"do": "read", "region": [0, 0, 50, 50], "as": "c", "pattern": r"(\d+)"},
                               {"do": "type", "text": "{var:c}"}]},
            "rules": [{"when": {"text": "go"}, "then": "p"}]}
    eng, drv, _, _ = engine(data, dry_run=True)
    drv.texts.add("go")
    import logging
    with caplog.at_level(logging.INFO, logger="webmacro"):
        eng.tick()
    assert not any(e[0] == "type" for e in drv.log)
    assert "7351" in caplog.text


# ---------- 실제 Tesseract + 실제 브라우저 ----------
needs_tesseract = pytest.mark.skipif(not ocr.available(), reason="tesseract 미설치")

PAGE = """<!doctype html><meta charset=utf-8>
<body style="margin:0;font-family:'Noto Sans CJK KR','Malgun Gothic',sans-serif;background:#fff">
<div id=box style="position:absolute;left:20px;top:20px;font-size:22px">인증코드: 7351</div>
<div style="position:absolute;left:20px;top:80px;font-size:20px">결재 대기 중인 문서</div>
<input id=code style="position:absolute;left:20px;top:140px">
<div id=out style="position:absolute;left:20px;top:200px"></div>
<script>
document.getElementById('code').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('out').textContent =
    e.target.value === '7351' ? '확인됨' : '틀림:' + e.target.value;
});
</script>"""


@needs_tesseract
def test_real_ocr_on_browser_screen(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from webmacro.driver import PlaywrightDriver
    from webmacro.engine import Engine
    page = tmp_path / "p.html"
    page.write_text(PAGE, encoding="utf-8")
    cfg = parse({
        "url": page.as_uri(), "viewport": {"width": 800, "height": 400},
        "patterns": {"입력": [{"do": "click_selector", "selector": "#code"},
                              {"do": "type", "text": "{var:code}"}, {"do": "press", "key": "Enter"},
                              {"do": "wait", "sec": 0.3}]},
        "rules": [
            {"name": "끝", "when": {"text": "확인됨"}, "then": [{"do": "stop"}]},
            {"name": "코드 입력", "when": [
                {"ocr": "결재대기", "region": [0, 70, 400, 120]},
                {"ocr": r"인증코드\s*[:：]?\s*(\d{4})", "regex": True, "region": [0, 10, 400, 60], "as": "code"}],
             "then": "입력"},
        ],
    }, base_dir=tmp_path)
    d = PlaywrightDriver(cfg)
    try:
        d.start()
    except Exception as e:
        pytest.skip(f"Chromium 실행 불가: {e}")
    try:
        text = ocr.read_text(d.screenshot(), (0, 10, 400, 60))
        assert "7351" in text, text
        eng = Engine(cfg, d, MemoryNotifier(), out_dir=tmp_path)
        assert eng.run(max_ticks=5) == "stop"
        assert eng.history == ["코드 입력", "끝"]
        assert d.page.inner_text("#out") == "확인됨"
    finally:
        d.close()


def test_retry_in_english_when_code_not_found(monkeypatch):
    seen = []

    def read_text(img, region=None, lang="", psm=6, **kw):
        seen.append(lang)
        return "주문번호 21-4829" if lang != "eng" else "FSHS PT-4829"
    monkeypatch.setattr(ocr, "read_text", read_text)
    data = {"patterns": {"p": [{"do": "type", "text": "{var:no}"}]},
            "rules": [{"when": {"ocr": r"PT-(\d+)", "regex": True, "region": [0, 0, 9, 9], "as": "no"},
                       "then": "p"}]}
    eng, drv, _, _ = engine(data)
    assert eng.tick().startswith("ran")
    assert ("type", "4829", None) in drv.log and seen == ["kor+eng", "eng"]


@needs_tesseract
@pytest.mark.parametrize("family", ["serif", "sans-serif"])
@pytest.mark.parametrize("size", [14, 18])
def test_real_mixed_korean_and_code(tmp_path, family, size):
    """한글 라벨 옆 영문 코드: 실제 Tesseract로 값만 정확히 뽑히는지."""
    pytest.importorskip("playwright.sync_api")
    from webmacro.driver import PlaywrightDriver
    from webmacro.engine import Engine
    page = tmp_path / "m.html"
    page.write_text(f'<!doctype html><meta charset=utf-8><body style="margin:0;font-family:{family}">'
                    f'<div style="position:absolute;left:10px;top:10px;font-size:{size}px">주문번호 PT-4829 결재</div>',
                    encoding="utf-8")
    cfg = parse({"url": page.as_uri(), "viewport": {"width": 800, "height": 300},
                 "patterns": {"p": [{"do": "read", "region": [0, 0, 400, 60], "as": "no", "pattern": r"PT-(\d{4})"},
                                    {"do": "stop"}]},
                 "rules": [{"when": {"ocr": "결재", "region": [0, 0, 400, 60]}, "then": "p"}]}, base_dir=tmp_path)
    d = PlaywrightDriver(cfg)
    try:
        d.start()
    except Exception as e:
        pytest.skip(f"Chromium 실행 불가: {e}")
    try:
        eng = Engine(cfg, d, MemoryNotifier(), out_dir=tmp_path)
        assert eng.tick() == "stop"
        assert eng.vars["no"] == "4829"
    finally:
        d.close()
