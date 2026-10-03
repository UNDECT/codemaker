"""명령줄 진입점.

  python -m webmacro check    설정.yaml               설정 검사
  python -m webmacro snapshot 설정.yaml [-o a.png]    사이트 화면 저장(색·좌표 고를 때)
  python -m webmacro color    a.png X Y               이미지의 (X,Y) 색상 출력
  python -m webmacro login    설정.yaml               브라우저 창을 띄워 직접 로그인 → 세션 저장
  python -m webmacro run      설정.yaml [--dry-run] [--once]
  python -m webmacro panel    설정.yaml [--host 0.0.0.0] [--port 8080]   웹 관리 화면
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config as config_mod
from .driver import PlaywrightDriver
from .engine import Engine
from .notify import Notifier

log = logging.getLogger("webmacro")


def _setup_logging(out_dir: Path | None):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    log.addHandler(h)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(out_dir / "webmacro.log", maxBytes=5_000_000, backupCount=3,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)


def cmd_check(a):
    cfg = config_mod.load(a.config)
    print(f"OK: 규칙 {len(cfg.rules)}개, 패턴 {len([p for p in cfg.patterns if not p.startswith('__')])}개")
    for r in cfg.rules:
        conds = ", ".join(f"{'없음:' if c.absent else ''}{c.kind}={c.value}" for c in r.when) or "(항상)"
        print(f"  - {r.name}: {conds} → {r.then} ({r.after})")


def cmd_snapshot(a):
    cfg = config_mod.load(a.config)
    d = PlaywrightDriver(cfg)
    d.start()
    try:
        if a.wait:
            time.sleep(a.wait)
        out = Path(a.output)
        d.save_png(out)
        print(f"저장: {out}  (현재 주소: {d.url()})")
    finally:
        d.close()


def cmd_color(a):
    import numpy as np
    from PIL import Image
    img = np.asarray(Image.open(a.image).convert("RGB"))
    r, g, b = (int(v) for v in img[a.y, a.x])
    print(f"({a.x}, {a.y}) = #{r:02X}{g:02X}{b:02X}  rgb({r}, {g}, {b})")


def cmd_login(a):
    cfg = config_mod.load(a.config)
    if not cfg.session_file:
        sys.exit("설정에 session_file 을 지정하세요 (예: session_file: state/session.json)")
    cfg.headless = False
    d = PlaywrightDriver(cfg)
    d.start()
    try:
        input("열린 브라우저에서 로그인한 뒤 여기서 Enter를 누르세요...")
        d.save_session()
        print(f"세션 저장: {d.session_path}")
    finally:
        d.close()


def cmd_run(a):
    cfg = config_mod.load(a.config)
    if a.headed:
        cfg.headless = False
    out = cfg.base_dir / "output"
    _setup_logging(out)
    notifier = Notifier()
    d = PlaywrightDriver(cfg)

    def restart():
        d.close()
        d.start()

    # 사이트·네트워크가 잠시 안 될 수 있으니 첫 접속도 재시도한다
    for attempt in range(1, 10**9):
        try:
            d.start()
            break
        except Exception as e:
            d.close()
            wait = min(300, 10 * 2 ** min(attempt, 5))
            log.error("사이트 접속 실패(%d회): %s → %d초 후 재시도", attempt, str(e).splitlines()[0], wait)
            if attempt == 1 or attempt % 10 == 0:
                notifier.send(f"사이트 접속 실패({attempt}회): {str(e).splitlines()[0]}")
            if a.once:
                sys.exit(1)
            time.sleep(wait)

    eng = Engine(cfg, d, notifier, dry_run=a.dry_run, out_dir=out)
    log.info("시작: %s (규칙 %d개%s)", cfg.url, len(cfg.rules), ", dry-run" if a.dry_run else "")
    if not a.dry_run:
        notifier.send(f"매크로 시작: {cfg.url}")
    try:
        result = eng.run(max_ticks=1 if a.once else None, restart_driver=restart)
        log.info("끝: %s", result)
    except KeyboardInterrupt:
        log.info("사용자 중지")
    finally:
        eng._save_session()
        d.close()


def cmd_panel(a):
    from .web import serve
    out = Path(a.config).resolve().parent / "output"
    _setup_logging(out)
    serve(a.config, host=a.host, port=a.port, autostart=not a.no_autostart)


def main(argv=None):
    p = argparse.ArgumentParser(prog="webmacro", description="웹사이트 색상 인식 자동화")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", help="설정 검사")
    s.add_argument("config")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("snapshot", help="사이트 화면 저장")
    s.add_argument("config")
    s.add_argument("-o", "--output", default="snapshot.png")
    s.add_argument("--wait", type=float, default=2.0, help="페이지 로딩 대기(초)")
    s.set_defaults(fn=cmd_snapshot)

    s = sub.add_parser("color", help="이미지 좌표 색상")
    s.add_argument("image")
    s.add_argument("x", type=int)
    s.add_argument("y", type=int)
    s.set_defaults(fn=cmd_color)

    s = sub.add_parser("login", help="직접 로그인해 세션 저장(화면 있는 PC에서)")
    s.add_argument("config")
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("run", help="실행")
    s.add_argument("config")
    s.add_argument("--dry-run", action="store_true", help="클릭하지 않고 판단만 기록")
    s.add_argument("--once", action="store_true", help="한 번만 확인")
    s.add_argument("--headed", action="store_true", help="브라우저 창 표시")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("panel", help="웹 관리 화면 (사이트 설정·화면 보기·시작/중지)")
    s.add_argument("config", help="설정 파일 (없으면 새로 만듦)")
    s.add_argument("--host", default="127.0.0.1", help="외부 접속 허용은 0.0.0.0 (비밀번호 필요)")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--no-autostart", action="store_true", help="재시작 시 이전 실행 상태를 이어가지 않음")
    s.set_defaults(fn=cmd_panel)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except config_mod.ConfigError as e:
        sys.exit(f"설정 오류: {e}")
