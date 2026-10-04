import json

from webmacro.auth import Auth
from webmacro.monitor import Monitor


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_monitor_tracks_progress_and_counts(tmp_path):
    clk = Clock()
    m = Monitor(tmp_path / "stats.json", clock=clk)
    m.started("https://site", dry_run=False)
    m.on_event("idle", streak=1)
    m.on_event("rule", rule="빨간 버튼", pattern="결재")
    m.on_event("step", pattern="결재", index=2, total=5, action="type", detail="'ok'")
    snap = m.snapshot()
    assert snap["activity"].startswith("'빨간 버튼' · 결재 2/5 단계: 입력")
    m.on_event("read", name="코드", value="7351")
    m.on_event("done", rule="빨간 버튼")
    m.on_event("rule", rule="빨간 버튼", pattern="결재")
    m.on_event("error", rule="빨간 버튼", message="결재#1 click_match: 없음", shot="x-error.png")
    snap = m.snapshot()
    assert snap["today"]["done"] == 1 and snap["today"]["errors"] == 1 and snap["today"]["checks"] == 3
    assert snap["today"]["rules"] == {"빨간 버튼": 1}
    assert snap["last_error"]["shot"] == "x-error.png"
    kinds = [e["kind"] for e in snap["events"]]
    assert kinds == ["start", "rule", "read", "done", "rule", "error"]
    # since: 새 사건만
    last = snap["events"][-1]["id"]
    m.on_event("pause", reason="폭주", minutes=30, shot=None)
    assert [e["kind"] for e in m.snapshot(since=last)["events"]] == ["pause"]
    m.finished("사용자 중지")
    assert m.snapshot()["started_at"] is None

    # 재시작해도 오늘 건수 유지, 날짜가 바뀌면 초기화
    m2 = Monitor(tmp_path / "stats.json", clock=clk)
    assert m2.snapshot()["today"]["done"] == 1
    clk.t += 86400
    s = m2.snapshot()
    assert s["today"]["done"] == 0 and list(s["history"].values())[0]["done"] == 1


def test_monitor_survives_broken_file(tmp_path):
    f = tmp_path / "stats.json"
    f.write_text("{not json", encoding="utf-8")
    assert Monitor(f).snapshot()["today"]["done"] == 0


def test_auth_tokens_persist_and_rate_limit(tmp_path):
    clk = Clock()
    a = Auth("secret", tmp_path / "auth.json", clock=clk)
    assert a.login("1.2.3.4", "wrong") is None
    tok = a.login("1.2.3.4", "secret")
    assert tok and a.valid(tok) and not a.valid("other")
    assert "secret" not in (tmp_path / "auth.json").read_text() and tok not in (tmp_path / "auth.json").read_text()
    b = Auth("secret", tmp_path / "auth.json", clock=clk)   # 재시작
    assert b.valid(tok)
    b.logout(tok)
    assert not b.valid(tok)
    for _ in range(5):
        assert b.login("9.9.9.9", "nope") is None
    assert b.blocked("9.9.9.9")
    assert b.login("9.9.9.9", "secret") is None              # 막힌 동안엔 맞아도 거절
    assert b.login("5.5.5.5", "secret")                     # 다른 기기는 영향 없음
    clk.t += 301
    assert b.login("9.9.9.9", "secret")
    tok2 = b.login("1.1.1.1", "secret")
    clk.t += 31 * 86400
    assert not b.valid(tok2)                                  # 30일 지나면 만료


def test_auth_disabled_without_password():
    a = Auth(None)
    assert not a.enabled and a.login("x", "") is None


def test_dry_run_events_not_counted(tmp_path):
    m = Monitor(tmp_path / "s.json", clock=Clock())
    m.started("u", dry_run=True)
    m.on_event("rule", rule="r", pattern="p")
    m.on_event("done", rule="r")
    m.on_event("error", rule="r", message="x", shot=None)
    s = m.snapshot()
    assert s["today"]["done"] == 0 and s["today"]["errors"] == 0 and s["today"]["rules"] == {}
    assert any("(시험)" in e["text"] for e in s["events"])
    m.finished("끝")
    m.started("u", dry_run=False)
    m.on_event("done", rule="r")
    assert m.snapshot()["today"]["done"] == 1
