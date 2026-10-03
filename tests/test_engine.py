import numpy as np
import pytest

from webmacro.config import parse
from webmacro.driver import Driver
from webmacro.engine import Engine
from webmacro.notify import MemoryNotifier


class FakeDriver(Driver):
    """화면(numpy 이미지)과 글자·주소를 흉내 낸다. on_click으로 화면 변화를 만든다."""

    def __init__(self, w=200, h=100):
        self.img = np.full((h, w, 3), 255, np.uint8)
        self.texts: set[str] = set()
        self.cur_url = "https://site/home"
        self.log: list[tuple] = []
        self.on_click = None
        self.reloads = 0

    def screenshot(self):
        return self.img.copy()

    def save_png(self, path):
        self.log.append(("png", path.name))

    def url(self):
        return self.cur_url

    def has_text(self, t):
        return any(t in x for x in self.texts)

    def has_selector(self, s):
        return False

    def click(self, x, y, button="left", double=False):
        self.log.append(("click", x, y))
        if self.on_click:
            self.on_click(x, y)

    def type(self, text, selector=None, delay=0):
        self.log.append(("type", text, selector))

    def press(self, key):
        self.log.append(("press", key))

    def reload(self):
        self.reloads += 1

    def clicks(self):
        return [e[1:] for e in self.log if e[0] == "click"]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def engine(data, drv=None, dry_run=False):
    cfg = parse({"url": "https://site", **data})
    clk = Clock()
    drv = drv or FakeDriver()
    n = MemoryNotifier()
    return Engine(cfg, drv, n, dry_run=dry_run, clock=clk, sleep=clk.sleep), drv, n, clk


RED_THEN_DONE = {
    "patterns": {"결재": [{"do": "click_match"}, {"do": "type", "text": "ok"}, {"do": "press", "key": "Enter"}]},
    "rules": [
        {"name": "끝", "when": [{"text": "없습니다"}], "then": [{"do": "notify", "message": "bye"}], "after": "stop"},
        {"name": "빨강", "when": [{"color": "#FF0000", "min_pixels": 10}], "then": "결재"},
    ],
}


def test_clicks_color_then_stops_when_work_done():
    eng, drv, n, _ = engine(RED_THEN_DONE)
    drv.img[20:40, 100:140] = (255, 0, 0)

    def handled(x, y):  # 빨간 버튼을 누르면 사라지고 '없습니다'가 뜬다
        drv.img[:] = 255
        drv.texts.add("처리할 항목이 없습니다")
    drv.on_click = handled

    assert eng.run(max_ticks=10) == "stop"
    assert drv.clicks() == [(120, 30)]
    assert ("type", "ok", None) in drv.log and ("press", "Enter") in drv.log
    assert eng.history == ["빨강", "끝"]
    assert "bye" in n.messages and any("업무 종료" in m for m in n.messages)


def test_situation_selects_different_pattern():
    data = {
        "patterns": {"A": [{"do": "click", "x": 1, "y": 1}], "B": [{"do": "click", "x": 2, "y": 2}]},
        "rules": [
            {"name": "초록+반려", "when": [{"color": "#00FF00"}, {"text": "반려"}], "then": "B"},
            {"name": "초록", "when": [{"color": "#00FF00"}], "then": "A"},
        ],
    }
    eng, drv, _, _ = engine(data)
    drv.img[0:10, 0:10] = (0, 255, 0)
    eng.tick()
    drv.texts.add("반려 요청")
    eng.tick()
    assert drv.clicks() == [(1, 1), (2, 2)]


def test_no_match_does_nothing_and_notifies_idle():
    eng, drv, n, _ = engine({**RED_THEN_DONE, "idle_notify": 3})
    for _ in range(5):
        assert eng.tick() == "idle"
    assert drv.clicks() == []
    assert len([m for m in n.messages if "연속" in m]) == 1


def test_absent_condition_and_url():
    data = {"patterns": {"p": [{"do": "click", "x": 5, "y": 5}]},
            "rules": [{"when": [{"url": "/login"}, {"color": "#FF0000", "absent": True}], "then": "p"}]}
    eng, drv, _, _ = engine(data)
    assert eng.tick() == "idle"
    drv.cur_url = "https://site/login"
    assert eng.tick().startswith("ran")
    drv.img[0:10, 0:10] = (255, 0, 0)
    assert eng.tick() == "idle"


def test_named_targets_and_offset():
    data = {"patterns": {"p": [{"do": "click_match", "target": "g", "dx": 5}, {"do": "click_match", "target": "b"}]},
            "rules": [{"when": [{"color": "#00FF00", "as": "g"}, {"color": "#0000FF", "as": "b"}], "then": "p"}]}
    eng, drv, _, _ = engine(data)
    drv.img[0:20, 0:20] = (0, 255, 0)
    drv.img[50:70, 100:120] = (0, 0, 255)
    eng.tick()
    assert drv.clicks() == [(15, 10), (110, 60)]


def test_wait_color_timeout_reports_error_and_reloads_after_3():
    data = {"patterns": {"p": [{"do": "wait_color", "color": "#00FF00", "timeout": 2}, {"do": "click_match"}]},
            "rules": [{"when": [{"text": "go"}], "then": "p"}]}
    eng, drv, n, _ = engine(data)
    drv.texts.add("go")
    for _ in range(3):
        assert eng.tick() == "error"
    assert drv.reloads == 1
    assert len([m for m in n.messages if "대기 시간 초과" in m]) == 1  # 첫 실패만 알림
    assert any(e[0] == "png" for e in drv.log)


def test_wait_color_found_then_clicked():
    data = {"patterns": {"p": [{"do": "click", "x": 1, "y": 1},
                               {"do": "wait_color", "color": "#00FF00", "timeout": 5, "as": "ok"},
                               {"do": "click_match", "target": "ok"}]},
            "rules": [{"when": [{"text": "go"}], "then": "p"}]}
    eng, drv, _, _ = engine(data)
    drv.texts.add("go")

    def popup(x, y):
        if (x, y) == (1, 1):
            drv.img[80:90, 180:190] = (0, 255, 0)
    drv.on_click = popup
    assert eng.tick().startswith("ran")
    assert drv.clicks()[-1] == (185, 85)


def test_same_rule_limit_pauses():
    data = {"max_same_rule": 3, "pause_minutes": 1,
            "patterns": {"p": [{"do": "click", "x": 1, "y": 1}]},
            "rules": [{"when": [{"text": "x"}], "then": "p"}]}
    eng, drv, n, clk = engine(data)
    drv.texts.add("x")
    results = [eng.tick() for _ in range(4)]
    assert results[-1] == "pause" and len(drv.clicks()) == 3
    assert clk.t >= 60 and any("일시정지" in m for m in n.messages)


def test_rate_limit_pauses():
    data = {"max_actions_per_minute": 5,
            "patterns": {"p": [{"do": "click", "x": 1, "y": 1}] * 10},
            "rules": [{"when": [{"text": "x"}], "then": "p"}]}
    eng, drv, n, _ = engine(data)
    drv.texts.add("x")
    assert eng.tick() == "pause"
    assert len(drv.clicks()) == 5


def test_cooldown():
    data = {"patterns": {"p": [{"do": "click", "x": 1, "y": 1}]},
            "rules": [{"when": [{"text": "x"}], "then": "p", "cooldown": 10}]}
    eng, drv, _, clk = engine(data)
    drv.texts.add("x")
    assert eng.tick().startswith("ran")
    assert eng.tick() == "idle"
    clk.t += 11
    assert eng.tick().startswith("ran")


def test_dry_run_clicks_nothing():
    eng, drv, _, _ = engine(RED_THEN_DONE, dry_run=True)
    drv.img[20:40, 100:140] = (255, 0, 0)
    assert eng.tick() == "ran:빨강"
    assert drv.clicks() == [] and not any(e[0] == "type" for e in drv.log)


def test_env_secret_typed(monkeypatch):
    monkeypatch.setenv("SITE_PW", "pw!")
    data = {"patterns": {"p": [{"do": "type", "selector": "#pw", "text": "{env:SITE_PW}"}]},
            "rules": [{"when": [{"url": "/login"}], "then": "p", "after": "stop"}]}
    eng, drv, _, _ = engine(data)
    drv.cur_url = "/login"
    assert eng.tick() == "stop"
    assert ("type", "pw!", "#pw") in drv.log


def test_run_restarts_driver_after_crash():
    data = {"patterns": {"p": [{"do": "stop"}]}, "rules": [{"when": [{"text": "x"}], "then": "p"}]}
    eng, drv, n, _ = engine(data)
    calls = {"n": 0}
    orig = drv.screenshot

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("browser closed")
        drv.texts.add("x")
        return orig()
    drv.screenshot = flaky
    restarted = []
    assert eng.run(max_ticks=5, restart_driver=lambda: restarted.append(1)) == "stop"
    assert restarted == [1] and any("오류" in m for m in n.messages)


def test_run_without_restart_raises():
    eng, drv, _, _ = engine(RED_THEN_DONE)
    drv.screenshot = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    with pytest.raises(RuntimeError):
        eng.run(max_ticks=1)


def test_recheck_after_stop_keeps_running():
    data = {"recheck_minutes": 10,
            "patterns": {"p": [{"do": "click", "x": 1, "y": 1}]},
            "rules": [{"when": [{"text": "done"}], "then": "p", "after": "stop"}]}
    eng, drv, _, clk = engine(data)
    drv.texts.add("done")
    assert eng.run(max_ticks=3) == "max_ticks"
    assert len(drv.clicks()) == 3 and drv.reloads == 3 and clk.t >= 1800
