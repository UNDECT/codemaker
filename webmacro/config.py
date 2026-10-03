"""설정 파일(YAML) 읽기·검증.

구조:
  url, viewport, interval ...    실행 환경
  patterns: {이름: [단계, ...]}   액션패턴 (클릭·입력 순서)
  rules:    [{name, when, then, after}, ...]  상황 → 액션패턴
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .color import parse_color

ACTIONS = {
    "click_match":    {"opt": {"target", "dx", "dy", "button", "double"}},
    "click":          {"req": {"x", "y"}, "opt": {"button", "double"}},
    "click_selector": {"req": {"selector"}, "opt": {"timeout"}},
    "click_text":     {"req": {"text"}, "opt": {"exact", "timeout"}},
    "type":           {"req": {"text"}, "opt": {"selector", "delay"}},
    "press":          {"req": {"key"}},
    "wait":           {"req": {"sec"}},
    "wait_color":     {"req": {"color"}, "opt": {"tolerance", "region", "min_pixels", "timeout", "as"}},
    "wait_text":      {"req": {"text"}, "opt": {"timeout"}},
    "read":           {"req": {"region", "as"}, "opt": {"pattern", "lang", "psm", "chars"}},
    "wait_ocr":       {"req": {"text"}, "opt": {"region", "timeout", "lang", "regex", "psm", "chars"}},
    "scroll":         {"req": {"dy"}, "opt": {"dx"}},
    "goto":           {"req": {"url"}},
    "reload":         {},
    "screenshot":     {"opt": {"name"}},
    "notify":         {"req": {"message"}},
    "run":            {"req": {"pattern"}},
    "stop":           {"opt": {"message"}},
}
CONDITIONS = {"color", "text", "selector", "url", "ocr"}
OCR_KEYS = {"region", "lang", "regex", "psm", "as", "chars"}
AFTER = {"continue", "stop", "pause"}
PICK = {"largest", "topmost", "leftmost", "first"}


class ConfigError(ValueError):
    pass


@dataclass
class Condition:
    kind: str                  # color | text | selector | url | ocr
    value: Any
    absent: bool = False       # True면 '없을 때' 만족
    tolerance: int = 10
    region: tuple[int, int, int, int] | None = None
    min_pixels: int = 20
    pick: str = "largest"
    name: str | None = None    # color: click_match target 이름 / ocr: 읽은 값을 담을 변수 이름
    lang: str = "kor+eng"      # ocr 언어
    regex: bool = False        # ocr: 정규식으로 찾기 (괄호 그룹 = 뽑을 값)
    psm: int = 6               # ocr: 6=여러 줄, 7=한 줄, 8=단어
    chars: str | None = None   # ocr: 허용 글자 (예: "0123456789")


@dataclass
class Rule:
    name: str
    when: list[Condition]
    then: str                  # 패턴 이름
    after: str = "continue"
    cooldown: float = 0.0      # 같은 규칙 재실행 최소 간격(초)


@dataclass
class Config:
    url: str
    rules: list[Rule]
    patterns: dict[str, list[dict]]
    viewport: tuple[int, int] = (1920, 1080)
    interval: float = 2.0
    idle_notify: int = 0       # 이 횟수만큼 연속으로 아무 규칙도 안 맞으면 알림(0=끔)
    max_actions_per_minute: int = 120
    max_same_rule: int = 50    # 같은 규칙 연속 실행 한도 → 넘으면 pause
    pause_minutes: float = 30  # pause 시 대기 시간(분)
    recheck_minutes: float = 0 # 업무 종료(stop) 후 이 시간 뒤 새로고침해 다시 시작(0=프로그램 종료)
    session_file: str | None = None
    headless: bool = True
    locale: str = "ko-KR"
    timezone: str = "Asia/Seoul"
    user_agent: str | None = None
    base_dir: Path = field(default_factory=Path.cwd)


_ENV_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(text: str) -> str:
    """'{env:SITE_PW}' → 환경변수 값. 비밀번호를 설정 파일에 적지 않기 위함."""
    def sub(m):
        key = m.group(1)
        if key not in os.environ:
            raise ConfigError(f"환경변수 {key} 가 설정되지 않았습니다")
        return os.environ[key]
    return _ENV_RE.sub(sub, text)


def _region(v, where):
    if v is None:
        return None
    if not (isinstance(v, (list, tuple)) and len(v) == 4):
        raise ConfigError(f"{where}: region은 [x1, y1, x2, y2] 형식")
    x1, y1, x2, y2 = (int(n) for n in v)
    if x2 <= x1 or y2 <= y1:
        raise ConfigError(f"{where}: region 좌표 순서 오류 {v}")
    return (x1, y1, x2, y2)


def _condition(raw: dict, where: str) -> Condition:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 조건은 dict여야 합니다")
    kinds = CONDITIONS & raw.keys()
    if len(kinds) != 1:
        raise ConfigError(f"{where}: color/text/selector/url/ocr 중 하나만 지정하세요 ({sorted(raw)})")
    kind = kinds.pop()
    allowed = {kind, "absent"}
    if kind == "color":
        allowed |= {"tolerance", "region", "min_pixels", "pick", "as"}
    elif kind == "ocr":
        allowed |= OCR_KEYS
    extra = set(raw) - allowed
    if extra:
        raise ConfigError(f"{where}: 알 수 없는 키 {sorted(extra)}")
    c = Condition(kind=kind, value=raw[kind], absent=bool(raw.get("absent", False)))
    if kind == "color":
        try:
            c.value = parse_color(raw["color"])
        except ValueError as e:
            raise ConfigError(f"{where}: {e}") from None
        c.tolerance = int(raw.get("tolerance", 10))
        c.region = _region(raw.get("region"), where)
        c.min_pixels = int(raw.get("min_pixels", 20))
        c.pick = raw.get("pick", "largest")
        if c.pick not in PICK:
            raise ConfigError(f"{where}: pick은 {sorted(PICK)} 중 하나")
        c.name = _name(raw.get("as"), where)
    elif kind == "ocr":
        c.value = str(raw["ocr"])
        c.region = _region(raw.get("region"), where)
        c.lang = str(raw.get("lang", "kor+eng"))
        c.regex = bool(raw.get("regex", False))
        c.psm = int(raw.get("psm", 6))
        c.chars = str(raw["chars"]) if raw.get("chars") is not None else None
        c.name = _name(raw.get("as"), where)
        if c.regex:
            _compile(c.value, where)
        if c.name and c.absent:
            raise ConfigError(f"{where}: absent 조건에는 as를 쓸 수 없습니다")
    else:
        c.value = str(raw[kind])
    return c


def _name(v, where: str):
    """as: 이름 검사. YAML에서 no/yes/on/off 는 참·거짓으로 읽히므로 막는다."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise ConfigError(f"{where}: as 이름은 글자여야 합니다 (yes/no/on/off 는 따옴표로 감싸세요: as: \"no\")")
    return str(v)


def _compile(pattern: str, where: str):
    try:
        re.compile(pattern)
    except re.error as e:
        raise ConfigError(f"{where}: 정규식 오류 {pattern!r}: {e}") from None


def _step(raw, where: str, pattern_names: set[str]) -> dict:
    if not isinstance(raw, dict) or "do" not in raw:
        raise ConfigError(f"{where}: 단계는 {{do: ...}} 형식")
    act = raw["do"]
    spec = ACTIONS.get(act)
    if spec is None:
        raise ConfigError(f"{where}: 알 수 없는 동작 {act!r} (가능: {sorted(ACTIONS)})")
    keys = set(raw) - {"do"}
    missing = spec.get("req", set()) - keys
    extra = keys - spec.get("req", set()) - spec.get("opt", set())
    if missing:
        raise ConfigError(f"{where}: {act}에 {sorted(missing)} 필요")
    if extra:
        raise ConfigError(f"{where}: {act}에 알 수 없는 키 {sorted(extra)}")
    step = dict(raw)
    if "as" in step:
        step["as"] = _name(step["as"], where)
    if act == "wait_color":
        try:
            step["color"] = parse_color(raw["color"])
        except ValueError as e:
            raise ConfigError(f"{where}: {e}") from None
        step["region"] = _region(raw.get("region"), where)
    if act in ("read", "wait_ocr"):
        step["region"] = _region(raw.get("region"), where)
        if act == "read" and step["region"] is None:
            raise ConfigError(f"{where}: read에는 region이 필요합니다")
        if act == "read" and raw.get("pattern"):
            _compile(str(raw["pattern"]), where)
        if act == "wait_ocr" and raw.get("regex"):
            _compile(str(raw["text"]), where)
    if act == "run" and raw["pattern"] not in pattern_names:
        raise ConfigError(f"{where}: 없는 패턴 {raw['pattern']!r}")
    return step


def parse(data: dict, base_dir: Path | None = None) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("설정 최상위는 dict여야 합니다")
    if "url" not in data:
        raise ConfigError("url 이 필요합니다")

    raw_patterns = data.get("patterns") or {}
    if not isinstance(raw_patterns, dict):
        raise ConfigError("patterns는 {이름: [단계...]} 형식")
    names = set(raw_patterns)
    patterns = {}
    for pname, steps in raw_patterns.items():
        if not isinstance(steps, list) or not steps:
            raise ConfigError(f"패턴 {pname!r}: 단계 목록이 비었습니다")
        patterns[pname] = [_step(s, f"패턴 {pname!r} {i + 1}단계", names) for i, s in enumerate(steps)]
    _check_run_cycles(patterns)

    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigError("rules 가 최소 1개 필요합니다")
    rules = []
    for i, r in enumerate(raw_rules):
        where = f"규칙 {i + 1}({r.get('name', '?') if isinstance(r, dict) else '?'})"
        if not isinstance(r, dict):
            raise ConfigError(f"{where}: dict여야 합니다")
        when = r.get("when") or []
        if isinstance(when, dict):
            when = [when]
        conds = [_condition(c, f"{where} 조건 {j + 1}") for j, c in enumerate(when)]
        then = r.get("then")
        if isinstance(then, list):  # 규칙 안에 바로 적은 단계 목록 → 익명 패턴
            anon = f"__rule{i + 1}"
            patterns[anon] = [_step(s, f"{where} {k + 1}단계", names) for k, s in enumerate(then)]
            then = anon
        if then not in patterns:
            raise ConfigError(f"{where}: then 패턴 {then!r} 이 patterns에 없습니다")
        after = r.get("after", "continue")
        if after not in AFTER:
            raise ConfigError(f"{where}: after는 {sorted(AFTER)} 중 하나")
        targets = {c.name for c in conds if c.kind == "color" and not c.absent}
        _check_targets(patterns[then], targets, conds, where, patterns)
        rules.append(Rule(name=str(r.get("name", f"규칙{i + 1}")), when=conds, then=then,
                          after=after, cooldown=float(r.get("cooldown", 0))))

    vp = data.get("viewport") or {}
    return Config(
        url=str(data["url"]),
        rules=rules,
        patterns=patterns,
        viewport=(int(vp.get("width", 1920)), int(vp.get("height", 1080))),
        interval=float(data.get("interval", 2.0)),
        idle_notify=int(data.get("idle_notify", 0)),
        max_actions_per_minute=int(data.get("max_actions_per_minute", 120)),
        max_same_rule=int(data.get("max_same_rule", 50)),
        pause_minutes=float(data.get("pause_minutes", 30)),
        recheck_minutes=float(data.get("recheck_minutes", 0)),
        session_file=data.get("session_file"),
        headless=bool(data.get("headless", True)),
        locale=str(data.get("locale", "ko-KR")),
        timezone=str(data.get("timezone", "Asia/Seoul")),
        user_agent=data.get("user_agent"),
        base_dir=base_dir or Path.cwd(),
    )


def _check_run_cycles(patterns):
    def visit(name, stack):
        if name in stack:
            raise ConfigError(f"패턴 순환 호출: {' → '.join(stack + [name])}")
        for s in patterns[name]:
            if s["do"] == "run":
                visit(s["pattern"], stack + [name])
    for n in patterns:
        visit(n, [])


def _check_targets(steps, targets, conds, where, patterns):
    has_color = any(c.kind == "color" and not c.absent for c in conds)
    local = set(targets)
    for s in steps:
        if s["do"] == "wait_color" and s.get("as"):
            local.add(s["as"])
        if s["do"] == "click_match":
            t = s.get("target")
            if t is None and not has_color and not any(x["do"] == "wait_color" for x in steps):
                raise ConfigError(f"{where}: click_match를 쓰려면 color 조건이 있어야 합니다")
            if t is not None and t not in local:
                raise ConfigError(f"{where}: click_match target {t!r} 를 정의한 color 조건(as)이 없습니다")
        if s["do"] == "run":
            _check_targets(patterns[s["pattern"]], local, conds, where, patterns)


def load(path: str | os.PathLike) -> Config:
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return parse(data, base_dir=p.resolve().parent)
