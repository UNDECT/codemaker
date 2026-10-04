"""진행 상황 기록: 지금 무엇을 하는지, 오늘 몇 건 처리했는지, 최근 기록.

엔진의 on_event 콜백으로 들어온 사건을 모아 관리 화면(현황 탭)에 보여준다.
오늘 처리 건수는 state/stats.json 에 저장해서 재시작해도 유지한다.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

ACTION_KO = {
    "click_match": "색 위치 클릭", "click": "좌표 클릭", "click_selector": "요소 클릭",
    "click_text": "글자 클릭", "type": "입력", "press": "키 입력", "wait": "대기",
    "wait_color": "색 기다림", "wait_text": "글자 기다림", "read": "글자 읽기",
    "wait_ocr": "글자(OCR) 기다림", "scroll": "스크롤", "goto": "주소 이동", "reload": "새로고침",
    "screenshot": "화면 저장", "notify": "알림", "stop": "종료", "auto_checkout": "자동 진행",
}


class Monitor:
    def __init__(self, stats_file: Path | None = None, clock=time.time, keep: int = 200):
        self.stats_file = stats_file
        self.clock = clock
        self.lock = threading.Lock()
        self.events: deque[dict] = deque(maxlen=keep)
        self.activity = "대기 중"
        self.activity_at = None
        self.last_check_at = None
        self.started_at = None
        self.last_error = None
        self.current_rule = None
        self.dry_run = False
        self._seq = 0
        self.day = self._today()
        self.today = {"done": 0, "errors": 0, "checks": 0, "rules": {}}
        self.history: dict[str, dict] = {}  # 날짜별 처리·오류 건수 (최근 2주)
        self._load()

    # ---------- 저장 ----------
    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock()).strftime("%Y-%m-%d")

    def _load(self):
        if not self.stats_file:
            return
        try:
            data = json.loads(self.stats_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("day") == self.day:
            t = data.get("today") or {}
            self.today.update({k: t[k] for k in ("done", "errors", "checks", "rules") if k in t})
        self.history = data.get("history", {})

    def _save(self):
        if not self.stats_file:
            return
        hist = dict(self.history)
        hist[self.day] = {"done": self.today["done"], "errors": self.today["errors"]}
        hist = dict(sorted(hist.items())[-14:])  # 최근 2주
        self.history = hist
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.stats_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"day": self.day, "today": self.today, "history": hist},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.stats_file)
        except OSError:
            pass

    def _roll_day(self):
        d = self._today()
        if d != self.day:
            self._save()
            self.day = d
            self.today = {"done": 0, "errors": 0, "checks": 0, "rules": {}}

    # ---------- 사건 ----------
    def _add(self, kind: str, text: str, **extra):
        self._seq += 1
        self.events.append({"id": self._seq, "t": self.clock(), "kind": kind, "text": text, **extra})

    def _set(self, text: str):
        self.activity = text
        self.activity_at = self.clock()

    def started(self, url: str, dry_run: bool):
        with self.lock:
            self._roll_day()
            self.started_at = self.clock()
            self.current_rule = None
            self.dry_run = dry_run
            self._set("시작함" + (" (시험 실행: 클릭 안 함)" if dry_run else ""))
            self._add("start", f"시작: {url}" + (" (시험 실행)" if dry_run else ""))

    def finished(self, result: str):
        with self.lock:
            self.started_at = None
            self.current_rule = None
            self._set(f"멈춤 · {result}")
            self._add("end", f"멈춤: {result}")
            self._save()

    def note(self, kind: str, text: str):
        with self.lock:
            self._set(text)
            self._add(kind, text)

    def on_event(self, kind: str, **i):
        with self.lock:
            self._roll_day()
            now = self.clock()
            if kind == "idle":
                self.last_check_at = now
                self.today["checks"] += 1
                self.current_rule = None
                self._set("화면 확인 중 · 맞는 상황 없음 (대기)")
            elif kind == "auto":
                self._set(i["text"])
                self._add("rule", i["text"])
            elif kind == "refresh":
                self._set(f"맞는 상황 없음 → 새로고침 ({i['streak']}회째)")
            elif kind == "rule":
                self.last_check_at = now
                self.today["checks"] += 1
                self.current_rule = i["rule"]
                self._set(f"'{i['rule']}' 실행 시작")
                self._add("rule", f"'{i['rule']}' → {i['pattern']}", rule=i["rule"])
            elif kind == "step":
                act = ACTION_KO.get(i["action"], i["action"])
                detail = f" {i['detail']}" if i.get("detail") else ""
                who = f"'{self.current_rule}' · " if self.current_rule else ""
                self._set(f"{who}{i['pattern']} {i['index']}/{i['total']} 단계: {act}{detail}")
            elif kind == "read":
                self._add("read", f"읽음: {i['name']} = {i['value']}")
            elif kind == "done":
                if self.dry_run:  # 시험 실행은 실제로 누르지 않았으니 처리 건수에 넣지 않는다
                    self._set(f"(시험) '{i['rule']}' 판단 완료")
                    self._add("done", f"(시험) '{i['rule']}' — 실제 클릭 없음", rule=i["rule"])
                else:
                    self.today["done"] += 1
                    r = self.today["rules"]
                    r[i["rule"]] = r.get(i["rule"], 0) + 1
                    self._set(f"'{i['rule']}' 완료")
                    self._add("done", f"'{i['rule']}' 완료", rule=i["rule"])
                    self._save()
            elif kind == "nochange":
                self._add("pause", f"'{i['rule']}' 실행 후 {i['sec']:g}초 동안 화면이 안 바뀜 (사이트가 느리거나 클릭이 안 먹었을 수 있음)")
            elif kind == "restart":
                self._add("wait", "브라우저 정기 재시작 (메모리 정리)")
            elif kind == "error":
                if not self.dry_run:
                    self.today["errors"] += 1
                self.last_error = {"t": now, "text": i["message"], "shot": i.get("shot")}
                self._set(f"실패: {i['message']}")
                self._add("error", i["message"], rule=i.get("rule"), shot=i.get("shot"))
                self._save()
            elif kind == "pause":
                self._set(f"일시정지 {i['minutes']:g}분: {i['reason']}")
                self._add("pause", f"일시정지 {i['minutes']:g}분: {i['reason']}", shot=i.get("shot"))
            elif kind == "stop":
                self._set(f"업무 종료: {i['reason']}")
                self._add("stop", f"업무 종료: {i['reason']}")
            elif kind == "crash":
                self.last_error = {"t": now, "text": i["message"], "shot": None}
                self._set(f"오류 → 브라우저 재시작 중 ({i['count']}회)")
                self._add("error", f"오류({i['count']}회): {i['message']}")
            elif kind == "waiting":
                self._set(f"업무 종료 후 대기 · {i['minutes']:g}분 뒤 다시 확인")
                self._add("wait", f"{i['minutes']:g}분 뒤 다시 확인")

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            self._roll_day()
            return {
                "activity": self.activity,
                "activity_at": self.activity_at,
                "last_check_at": self.last_check_at,
                "started_at": self.started_at,
                "now": self.clock(),
                "today": json.loads(json.dumps(self.today)),
                "history": dict(self.history),
                "last_error": self.last_error,
                "events": [e for e in self.events if e["id"] > since][-60:],
            }
