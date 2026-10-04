import pytest

from webmacro.config import ConfigError, expand_env, load, parse

BASE = {"url": "https://x", "patterns": {"p": [{"do": "click", "x": 1, "y": 2}]},
        "rules": [{"name": "r", "when": [{"text": "hi"}], "then": "p"}]}


def mk(**over):
    d = {**BASE, **over}
    return parse(d)


def test_minimal():
    cfg = mk()
    assert cfg.rules[0].when[0].kind == "text"
    assert cfg.viewport == (1920, 1080)


def test_example_file_loads():
    from pathlib import Path
    cfg = load(Path(__file__).parents[1] / "examples" / "example.yaml")
    assert [r.after for r in cfg.rules][-1] == "stop"


def test_inline_steps_become_pattern():
    cfg = mk(rules=[{"when": {"url": "/a"}, "then": [{"do": "reload"}]}])
    assert cfg.patterns[cfg.rules[0].then] == [{"do": "reload"}]


@pytest.mark.parametrize("over,msg", [
    ({"rules": "x"}, "목록"),
    ({"rules": [{"when": [{"text": "a", "url": "b"}], "then": "p"}]}, "하나만"),
    ({"rules": [{"when": [{"color": "#12"}], "then": "p"}]}, "색상"),
    ({"rules": [{"when": [{"text": "a"}], "then": "없음"}]}, "patterns에 없"),
    ({"rules": [{"when": [{"text": "a"}], "then": "p", "after": "x"}]}, "after"),
    ({"patterns": {"p": [{"do": "fly"}]}}, "알 수 없는 동작"),
    ({"patterns": {"p": [{"do": "click", "x": 1}]}}, "필요"),
    ({"patterns": {"p": [{"do": "click", "x": 1, "y": 1, "z": 3}]}}, "알 수 없는 키"),
    ({"patterns": {"p": [{"do": "run", "pattern": "p"}]}}, "순환"),
    ({"patterns": {"p": [{"do": "click_match"}]}}, "color 조건"),
    ({"patterns": {"p": [{"do": "click_match", "target": "a"}]},
      "rules": [{"when": [{"color": "#FF0000", "as": "b"}], "then": "p"}]}, "target"),
    ({"rules": [{"when": [{"color": "#FF0000", "region": [10, 10, 5, 5]}], "then": "p"}]}, "region"),
])
def test_errors(over, msg):
    with pytest.raises(ConfigError, match=msg):
        mk(**over)


def test_click_match_target_via_wait_color():
    mk(patterns={"p": [{"do": "wait_color", "color": "#00FF00", "as": "ok"},
                       {"do": "click_match", "target": "ok"}]})


def test_expand_env(monkeypatch):
    monkeypatch.setenv("PW", "s3cret")
    assert expand_env("a{env:PW}b") == "as3cretb"
    monkeypatch.delenv("PW")
    with pytest.raises(ConfigError):
        expand_env("{env:PW}")
