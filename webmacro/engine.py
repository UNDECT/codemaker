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

import re

from . import ocr
from .color import find_blobs, pick_blob
from .config import Config, Condition, Rule, expand_env
from .driver import Driver
from .notify import Notifier

log = logging.getLogger("webmacro")

COUNTED = {"click_match", "click", "click_selector", "click_text", "type", "press"}
SESSION_SAVE_SEC = 600


class StepFailed(Exception):
    pass


_VAR_RE = re.compile(r"\{var:([^}]+)\}")


class _Stop(Exception):
    def __init__(self, message=""):
        self.message = message


class Engine:
    def __init__(self, cfg: Config, driver: Driver, notifier: Notifier | None = None,
                 dry_run: bool = False, out_dir: Path | None = None,
                 clock=time.monotonic, sleep=time.sleep, on_event=None):
        self.cfg = cfg
        self.on_event = on_event  # 진행 상황을 관리 화면에 알리는 콜백 (kind, **정보)
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
        self.vars: dict[str, str] = {}    # OCR로 읽은 값 (규칙 실행마다 새로)
        self._pending_vars: dict[str, str] = {}
        self._ocr_cache: dict = {}
        self._ocr_warned = False
        self.history: list[str] = []  # 실행한 규칙 이름 (테스트·로그용)

    def emit(self, kind: str, **info):
        if self.on_event:
            try:
                self.on_event(kind, **info)
            except Exception:  # 화면 표시 오류로 매크로가 멈추면 안 된다
                log.exception("진행 상황 기록 실패")

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
        elif cond.kind == "ocr":
            try:
                text = self._ocr(img, cond.region, cond.lang, cond.psm, cond.chars)
            except ocr.OcrError as e:
                # 글자를 못 읽으면 '있다/없다' 어느 쪽도 확신할 수 없으니 규칙을 실행하지 않는다
                if not self._ocr_warned:
                    self._ocr_warned = True
                    log.error("OCR 실패: %s", e)
                    self.notifier.send(f"OCR 실패: {e}")
                return False
            val = ocr.find(text, cond.value, cond.regex)
            if val is None and _retry_eng(cond.lang, cond.value, cond.regex):
                try:
                    text = self._ocr(img, cond.region, "eng", cond.psm, cond.chars)
                    val = ocr.find(text, cond.value, cond.regex)
                except ocr.OcrError:
                    pass
            found = val is not None
            if found and cond.name:
                self._pending_vars[cond.name] = val if isinstance(val, str) else text
        else:  # pragma: no cover - config에서 걸러짐
            raise ValueError(cond.kind)
        return found != cond.absent

    def _ocr(self, img, region, lang, psm, chars=None) -> str:
        key = (region, lang, psm, chars)
        if key not in self._ocr_cache:  # 같은 화면·영역은 한 번만 읽는다
            self._ocr_cache[key] = ocr.read_text(img, region, lang=lang, psm=psm, chars=chars)
        return self._ocr_cache[key]

    def evaluate(self, img) -> tuple[Rule, dict] | None:
        """맞는 규칙과 색상 위치를 돌려준다. OCR로 뽑은 값은 self.vars 에 들어간다."""
        now = self.clock()
        self._ocr_cache = {}
        for rule in self.cfg.rules:
            last = self._last_fired.get(rule.name)
            if rule.cooldown and last is not None and now - last < rule.cooldown:
                continue
            matches: dict = {}
            self._pending_vars = {}
            if all(self._check(c, img, matches) for c in rule.when):
                self.vars = dict(self._pending_vars)
                return rule, matches
        return None

    def fill(self, text: str) -> str:
        """{env:이름} → 환경변수, {var:이름} → OCR로 읽은 값."""
        def sub(m):
            name = m.group(1)
            if name not in self.vars:
                raise StepFailed(f"변수 {name!r} 가 없습니다 (read 단계나 ocr 조건의 as로 먼저 읽어야 함)")
            return self.vars[name]
        return _VAR_RE.sub(sub, expand_env(text))

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
        steps = self.cfg.patterns[name]
        for i, step in enumerate(steps):
            act = step["do"]
            desc = f"{name}#{i + 1} {act}"
            if act != "run":
                self.emit("step", pattern=_label(name), index=i + 1, total=len(steps), action=act,
                          detail=self._human(step, matches))
            if self.dry_run and act not in ("run", "read"):  # read는 화면만 읽으므로 시험 실행에서도 수행
                log.info("[dry-run] %s %s", desc, self._brief(step, matches))
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
            d.click_text(self.fill(str(s["text"])), exact=bool(s.get("exact", False)),
                         timeout=float(s.get("timeout", 5)))
        elif act == "type":
            d.type(self.fill(str(s["text"])), selector=s.get("selector"),
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
        elif act == "read":
            img = d.screenshot()
            lang = s.get("lang", ocr.DEFAULT_LANG)
            text = ocr.read_text(img, s["region"], lang=lang, psm=int(s.get("psm", 6)), chars=s.get("chars"))
            if s.get("pattern"):
                val = ocr.find(text, str(s["pattern"]), regex=True)
                if val is None and _retry_eng(lang, str(s["pattern"]), True):
                    text2 = ocr.read_text(img, s["region"], lang="eng", psm=int(s.get("psm", 6)),
                                          chars=s.get("chars"))
                    val = ocr.find(text2, str(s["pattern"]), regex=True)
                if val is None:
                    raise StepFailed(f"읽은 글자에서 형식 {s['pattern']!r} 을 못 찾음: {text!r}")
            else:
                val = text
            if not val or not str(val).strip():
                raise StepFailed(f"영역 {list(s['region'])} 에서 글자를 못 읽음")
            self.vars[s["as"]] = str(val)
            log.info("읽음: %s = %r", s["as"], val)
            self.emit("read", name=s["as"], value=str(val))
        elif act == "wait_ocr":
            def seen():
                img = d.screenshot()
                lang, q, rx = s.get("lang", ocr.DEFAULT_LANG), str(s["text"]), bool(s.get("regex", False))
                langs = [lang] + (["eng"] if _retry_eng(lang, q, rx) else [])
                return any(ocr.find(ocr.read_text(img, s.get("region"), lang=lg, psm=int(s.get("psm", 6)),
                                                  chars=s.get("chars")), q, rx) is not None for lg in langs)
            self._wait_until(seen, float(s.get("timeout", 10)), "OCR 글자")
        elif act == "scroll":
            d.scroll(int(s.get("dx", 0)), int(s["dy"]))
        elif act == "goto":
            d.goto(self.fill(str(s["url"])))
        elif act == "reload":
            d.reload()
        elif act == "screenshot":
            self._shot(s.get("name", "shot"))
        elif act == "notify":
            self.notifier.send(self.fill(str(s["message"])))
        elif act == "run":
            if depth > 20:
                raise StepFailed("run 중첩이 너무 깊습니다")
            self.run_pattern(s["pattern"], matches, depth + 1)
        elif act == "stop":
            raise _Stop(s.get("message", ""))

    def _human(self, step: dict, matches: dict) -> str:
        """현황 화면용 짧은 설명 (비밀번호·환경변수 값은 숨김)."""
        act = step["do"]
        try:
            if act == "click_match":
                t = matches.get(step.get("target"))
                return f"({t[0] + int(step.get('dx', 0))}, {t[1] + int(step.get('dy', 0))})" if t else ""
            if act == "click":
                return f"({step['x']}, {step['y']})"
            if act in ("type", "click_text", "wait_text", "wait_ocr"):
                raw = str(step["text"])
                if "{env:" in raw:
                    return "(비공개 값)"
                return f"'{self.fill(raw) if '{var:' in raw else raw}'"[:60]
            if act == "wait":
                return f"{step['sec']:g}초"
            if act in ("press",):
                return str(step["key"])
            if act in ("click_selector",):
                return str(step["selector"])[:40]
            if act == "read":
                return f"→ {step['as']}"
            if act == "goto":
                return str(step["url"])[:60]
        except (StepFailed, KeyError, TypeError, ValueError):
            pass
        return ""

    def _brief(self, step: dict, matches: dict) -> str:
        if step["do"] == "click_match":
            t = matches.get(step.get("target"))
            if t:
                return f"→ ({t[0] + int(step.get('dx', 0))}, {t[1] + int(step.get('dy', 0))})"
        keys = {k: v for k, v in step.items() if k != "do"}
        if "text" in keys:
            raw = str(keys["text"])
            if "{env:" in raw:
                keys["text"] = "(환경변수)"
            elif "{var:" in raw:
                try:
                    keys["text"] = self.fill(raw)
                except StepFailed as e:
                    keys["text"] = f"(변수 없음: {e})"
        return str(keys) if keys else ""

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
            self.emit("idle", streak=self._idle)
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
        self.emit("rule", rule=rule.name, pattern=_label(rule.then))
        try:
            self.run_pattern(rule.then, matches)
        except _Stop as e:
            self.emit("done", rule=rule.name)
            self.emit("stop", reason=e.message or rule.name)
            self.notifier.send(f"업무 종료: {e.message or rule.name}")
            return "stop"
        except _Pause as e:
            return self._pause(str(e))
        except StepFailed as e:
            self._errors += 1
            self._fail_streak += 1
            log.error("단계 실패(%d회 연속): %s", self._fail_streak, e)
            shot = None
            if self._fail_streak == 1 or self._fail_streak % 20 == 0:  # 알림 폭주 방지
                shot = self._shot("error")
                self.notifier.send(f"단계 실패({self._fail_streak}회 연속): {e}"
                                   + (f" / {shot.name}" if shot else ""))
            self.emit("error", rule=rule.name, message=str(e), shot=shot.name if shot else None)
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
        self.emit("done", rule=rule.name)
        if rule.after == "stop":
            self.emit("stop", reason=rule.name)
            self.notifier.send(f"업무 종료: {rule.name}")
            return "stop"
        if rule.after == "pause":
            return self._pause(f"규칙 '{rule.name}' 실행 후 일시정지", notify=False)
        return f"ran:{rule.name}"

    def _pause(self, reason: str, notify: bool = True) -> str:
        shot = None
        if notify:
            shot = self._shot("pause")
            self.notifier.send(f"일시정지 {self.cfg.pause_minutes:g}분: {reason}")
        log.warning("일시정지: %s", reason)
        self.emit("pause", reason=reason, minutes=self.cfg.pause_minutes, shot=shot.name if shot else None)
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
                self.emit("crash", message=f"{type(e).__name__}: {e}", count=crashes)
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
                self.emit("waiting", minutes=self.cfg.recheck_minutes)
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


def _label(pattern: str) -> str:
    """규칙 안에 바로 적은 단계 목록(익명 패턴 __ruleN)은 화면에 '(규칙 내 단계)'로 보인다."""
    return "(규칙 내 단계)" if pattern.startswith("__rule") else pattern


def _retry_eng(lang: str, query: str, regex: bool) -> bool:
    """한글+영어 모드는 한글 옆 영문·코드(PT-4829 → 21-4829)를 자주 틀린다.
    못 찾았을 때 영어 전용으로 한 번 더 읽어 볼 가치가 있는지."""
    if lang == "eng" or "eng" not in lang.split("+"):
        return False
    return regex or query.isascii()
