"""실행 엔진: 화면 확인 → 맞는 규칙 선택 → 액션패턴 실행을 반복한다.

안전장치
- 아무 규칙도 안 맞으면 아무것도 누르지 않는다(fail-closed).
- 1분당 동작 수, 같은 규칙 연속 실행 수가 한도를 넘으면 알림 후 일시정지.
- 단계 실패 시 스크린샷을 남기고 알림. 연속 실패하면 새로고침, 브라우저가 죽으면 재시작.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .color import find_blobs, pick_blob
from .config import Config, Condition, Rule, expand_env
from .driver import Driver
from .notify import Notifier

log = logging.getLogger("webmacro")

COUNTED = {"click_match", "click", "click_selector", "click_text", "type", "press"}
SESSION_SAVE_SEC = 600


class StepFailed(Exception):
    pass


class _Stop(Exception):
    def __init__(self, message=""):
        self.message = message


class Engine:
    def __init__(self, cfg: Config, driver: Driver, notifier: Notifier | None = None,
                 dry_run: bool = False, out_dir: Path | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        self.cfg = cfg
        self.driver = driver
        self.notifier = notifier or Notifier()
        self.dry_run = dry_run
        self.out_dir = out_dir or (cfg.base_dir / "output")
        self.clock = clock
        self.sleep = sleep
        self._actions: deque[float] = deque()
        self._last_fired: dict[str, float] = {}
        self._same_rule = (None, 0)
        self._idle = 0
        self._errors = 0           # 새로고침 판단용(3회마다 초기화)
        self._fail_streak = 0      # 알림용 연속 실패 수
        self._last_session_save = clock()
        self.history: list[str] = []  # 실행한 규칙 이름 (테스트·로그용)

    # ---------- 판단 ----------
    def _check(self, cond: Condition, img, matches: dict) -> bool:
        if cond.kind == "color":
            blobs = find_blobs(img, cond.value, cond.tolerance, cond.region, cond.min_pixels)
            found = bool(blobs)
            if found and not cond.absent:
                b = pick_blob(blobs, cond.pick)
                matches.setdefault(None, (b.x, b.y))
                if cond.name:
                    matches[cond.name] = (b.x, b.y)
        elif cond.kind == "text":
            found = self.driver.has_text(cond.value)
        elif cond.kind == "selector":
            found = self.driver.has_selector(cond.value)
        elif cond.kind == "url":
            found = cond.value in self.driver.url()
        else:  # pragma: no cover - config에서 걸러짐
            raise ValueError(cond.kind)
        return found != cond.absent

    def evaluate(self, img) -> tuple[Rule, dict] | None:
        now = self.clock()
        for rule in self.cfg.rules:
            last = self._last_fired.get(rule.name)
            if rule.cooldown and last is not None and now - last < rule.cooldown:
                continue
            matches: dict = {}
            if all(self._check(c, img, matches) for c in rule.when):
                return rule, matches
        return None

    # ---------- 실행 ----------
    def _count_action(self):
        now = self.clock()
        self._actions.append(now)
        while self._actions and now - self._actions[0] > 60:
            self._actions.popleft()
        if len(self._actions) > self.cfg.max_actions_per_minute:
            raise _Pause(f"1분 동작 한도({self.cfg.max_actions_per_minute}) 초과")

    def _wait_until(self, fn, timeout: float, what: str):
        end = self.clock() + timeout
        while True:
            r = fn()
            if r:
                return r
            if self.clock() >= end:
                raise StepFailed(f"{what} 대기 시간 초과({timeout}s)")
            self.sleep(0.3)

    def run_pattern(self, name: str, matches: dict, depth: int = 0):
        for i, step in enumerate(self.cfg.patterns[name]):
            act = step["do"]
            desc = f"{name}#{i + 1} {act}"
            if self.dry_run and act != "run":
                log.info("[dry-run] %s %s", desc, _brief(step, matches))
                continue
            if act in COUNTED:
                self._count_action()
            try:
                self._do(step, matches, depth)
            except (_Stop, _Pause):
                raise
            except StepFailed as e:
                raise StepFailed(f"{desc}: {e}") from None
            except Exception as e:
                raise StepFailed(f"{desc}: {type(e).__name__}: {e}") from e

    def _do(self, s: dict, matches: dict, depth: int):
        d = self.driver
        act = s["do"]
        if act == "click_match":
            target = s.get("target")
            if target not in matches:
                raise StepFailed(f"클릭할 색상 위치 {target or '(기본)'} 없음")
            x, y = matches[target]
            d.click(x + int(s.get("dx", 0)), y + int(s.get("dy", 0)),
                    button=s.get("button", "left"), double=bool(s.get("double", False)))
        elif act == "click":
            d.click(int(s["x"]), int(s["y"]), button=s.get("button", "left"),
                    double=bool(s.get("double", False)))
        elif act == "click_selector":
            d.click_selector(s["selector"], timeout=float(s.get("timeout", 5)))
        elif act == "click_text":
            d.click_text(expand_env(str(s["text"])), exact=bool(s.get("exact", False)),
                         timeout=float(s.get("timeout", 5)))
        elif act == "type":
            d.type(expand_env(str(s["text"])), selector=s.get("selector"),
                   delay=float(s.get("delay", 0)))
        elif act == "press":
            d.press(str(s["key"]))
        elif act == "wait":
            self.sleep(float(s["sec"]))
        elif act == "wait_color":
            def found():
                blobs = find_blobs(d.screenshot(), s["color"], int(s.get("tolerance", 10)),
                                   s.get("region"), int(s.get("min_pixels", 20)))
                return pick_blob(blobs)
            b = self._wait_until(found, float(s.get("timeout", 10)), "색상")
            matches[s.get("as")] = (b.x, b.y)
            if s.get("as"):
                matches.setdefault(None, (b.x, b.y))
        elif act == "wait_text":
            self._wait_until(lambda: d.has_text(s["text"]), float(s.get("timeout", 10)), "글자")
        elif act == "scroll":
            d.scroll(int(s.get("dx", 0)), int(s["dy"]))
        elif act == "goto":
            d.goto(s["url"])
        elif act == "reload":
            d.reload()
        elif act == "screenshot":
            self._shot(s.get("name", "shot"))
        elif act == "notify":
            self.notifier.send(str(s["message"]))
        elif act == "run":
            if depth > 20:
                raise StepFailed("run 중첩이 너무 깊습니다")
            self.run_pattern(s["pattern"], matches, depth + 1)
        elif act == "stop":
            raise _Stop(s.get("message", ""))

    def _shot(self, name: str) -> Path | None:
        path = self.out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{name}.png"
        try:
            self.driver.save_png(path)
            return path
        except Exception as e:
            log.error("스크린샷 저장 실패: %s", e)
            return None

    # ---------- 한 번 확인 ----------
    def tick(self) -> str:
        """반환: 'idle' | 'ran:<규칙>' | 'stop' | 'pause' | 'error'"""
        img = self.driver.screenshot()
        hit = self.evaluate(img)
        if hit is None:
            self._idle += 1
            self._same_rule = (None, 0)
            if self.cfg.idle_notify and self._idle == self.cfg.idle_notify:
                self._shot("idle")
                self.notifier.send(f"{self._idle}회 연속으로 맞는 상황이 없습니다. 화면을 확인하세요.")
            return "idle"

        rule, matches = hit
        self._idle = 0
        prev, n = self._same_rule
        n = n + 1 if prev == rule.name else 1
        self._same_rule = (rule.name, n)
        if n > self.cfg.max_same_rule:
            return self._pause(f"규칙 '{rule.name}' 이 {n}회 연속 실행됨(화면이 안 바뀌는 듯)")

        log.info("규칙 '%s' → 패턴 '%s' %s", rule.name, rule.then,
                 f"(위치 {matches.get(None)})" if matches.get(None) else "")
        self._last_fired[rule.name] = self.clock()
        self.history.append(rule.name)
        try:
            self.run_pattern(rule.then, matches)
        except _Stop as e:
            self.notifier.send(f"업무 종료: {e.message or rule.name}")
            return "stop"
        except _Pause as e:
            return self._pause(str(e))
        except StepFailed as e:
            self._errors += 1
            self._fail_streak += 1
            log.error("단계 실패(%d회 연속): %s", self._fail_streak, e)
            if self._fail_streak == 1 or self._fail_streak % 20 == 0:  # 알림 폭주 방지
                shot = self._shot("error")
                self.notifier.send(f"단계 실패({self._fail_streak}회 연속): {e}"
                                   + (f" / {shot.name}" if shot else ""))
            if self._errors >= 3:
                log.warning("연속 실패 → 페이지 새로고침")
                try:
                    self.driver.reload()
                except Exception as re_err:
                    log.error("새로고침 실패: %s", re_err)
                self._errors = 0
            return "error"
        self._errors = 0
        self._fail_streak = 0
        if rule.after == "stop":
            self.notifier.send(f"업무 종료: {rule.name}")
            return "stop"
        if rule.after == "pause":
            return self._pause(f"규칙 '{rule.name}' 실행 후 일시정지", notify=False)
        return f"ran:{rule.name}"

    def _pause(self, reason: str, notify: bool = True) -> str:
        if notify:
            self._shot("pause")
            self.notifier.send(f"일시정지 {self.cfg.pause_minutes:g}분: {reason}")
        log.warning("일시정지: %s", reason)
        self.sleep(self.cfg.pause_minutes * 60)
        self._same_rule = (None, 0)
        self._actions.clear()
        return "pause"

    # ---------- 반복 ----------
    def run(self, max_ticks: int | None = None, restart_driver=None) -> str:
        """stop이 나올 때까지 반복. restart_driver: 브라우저가 죽었을 때 호출할 함수."""
        ticks = 0
        crashes = 0
        while max_ticks is None or ticks < max_ticks:
            ticks += 1
            try:
                result = self.tick()
                crashes = 0
            except Exception as e:  # 브라우저 크래시·네트워크 끊김 등
                crashes += 1
                log.exception("확인 중 오류")
                if crashes == 1 or crashes % 10 == 0:
                    self.notifier.send(f"오류({crashes}회): {type(e).__name__}: {e}")
                if restart_driver is None:
                    raise
                self.sleep(min(300, 5 * 2 ** min(crashes, 6)))
                try:
                    restart_driver()
                except Exception:
                    log.exception("브라우저 재시작 실패")
                continue
            if result == "stop":
                self._save_session()
                if not self.cfg.recheck_minutes:
                    return "stop"
                log.info("%g분 후 다시 확인", self.cfg.recheck_minutes)
                self.sleep(self.cfg.recheck_minutes * 60)
                try:
                    self.driver.reload()
                except Exception:
                    log.exception("새로고침 실패")
                continue
            if self.clock() - self._last_session_save > SESSION_SAVE_SEC:
                self._save_session()
            if result.startswith("ran:"):
                continue  # 실행 직후엔 바로 다시 확인
            self.sleep(self.cfg.interval)
        return "max_ticks"

    def _save_session(self):
        self._last_session_save = self.clock()
        try:
            self.driver.save_session()
        except Exception as e:
            log.error("로그인 세션 저장 실패: %s", e)


class _Pause(Exception):
    pass


def _brief(step: dict, matches: dict) -> str:
    if step["do"] == "click_match":
        t = matches.get(step.get("target"))
        if t:
            return f"→ ({t[0] + int(step.get('dx', 0))}, {t[1] + int(step.get('dy', 0))})"
    keys = {k: v for k, v in step.items() if k != "do"}
    if "text" in keys and "{env:" in str(keys["text"]):
        keys["text"] = "(환경변수)"
    return str(keys) if keys else ""
