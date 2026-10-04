"""화면 글자 인식(OCR). Tesseract 실행 파일을 직접 호출한다(추가 파이썬 패키지 불필요).

설치: 우분투 `apt install tesseract-ocr tesseract-ocr-kor`, 윈도우는 UB-Mannheim 설치본 + 한국어 데이터.
경로가 PATH에 없으면 WEBMACRO_TESSERACT 환경변수로 지정.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageOps

DEFAULT_LANG = "kor+eng"


class OcrError(RuntimeError):
    pass


WINDOWS_PATHS = (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                 r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")


def tesseract_path() -> str | None:
    found = os.environ.get("WEBMACRO_TESSERACT") or shutil.which("tesseract")
    if found:
        return found
    for p in WINDOWS_PATHS:  # 윈도우 기본 설치 위치 (PATH에 안 넣어도 찾음)
        if os.path.isfile(p):
            return p
    return None


def available() -> bool:
    return tesseract_path() is not None


def crop(img: np.ndarray, region) -> np.ndarray:
    if region is None:
        return img
    h, w = img.shape[:2]
    x1, y1, x2, y2 = region
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        raise OcrError(f"영역이 화면 밖입니다: {region}")
    return img[y1:y2, x1:x2]


def read_text(img: np.ndarray, region=None, lang: str = DEFAULT_LANG, psm: int = 6,
              chars: str | None = None, scale: float = 3.0, pad: int = 20, timeout: float = 30) -> str:
    """img(H×W×3 RGB)의 region 글자를 읽어 돌려준다.

    psm 6 = 여러 줄 문단, 7 = 한 줄, 8 = 단어 하나.
    chars: 나올 수 있는 글자만 지정(예: "0123456789-") → 숫자·코드 오인식이 크게 준다.
    3배 확대 + 흰 여백이 웹 화면 글씨(12~24px)에서 가장 정확했다.
    """
    exe = tesseract_path()
    if not exe:
        raise OcrError("tesseract 가 설치되어 있지 않습니다 (README의 OCR 항목 참고)")
    part = Image.fromarray(np.ascontiguousarray(crop(img, region))).convert("L")
    if scale and scale != 1:
        part = part.resize((max(1, int(part.width * scale)), max(1, int(part.height * scale))),
                           Image.LANCZOS)
    if pad:
        corner = int(np.asarray(part)[0, 0])  # 배경색으로 여백을 채운다
        part = ImageOps.expand(part, border=pad, fill=corner)
    buf = io.BytesIO()
    part.save(buf, format="PNG")
    try:
        cmd = [exe, "stdin", "stdout", "-l", lang, "--psm", str(psm)]
        if chars:
            cmd += ["-c", f"tessedit_char_whitelist={chars}"]
        r = subprocess.run(cmd,
                           input=buf.getvalue(), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise OcrError("OCR 시간 초과") from None
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OcrError(err[-1] if err else "OCR 실패")
    return clean(r.stdout.decode("utf-8", "replace"))


def clean(text: str) -> str:
    lines = [" ".join(ln.split()) for ln in text.replace("\f", "").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def squash(text: str) -> str:
    """비교용: 공백 제거. OCR은 한글 사이에 공백을 넣거나 빼는 일이 잦다."""
    return re.sub(r"\s+", "", text)


def find(text: str, query: str, regex: bool = False):
    """text에서 query를 찾는다. 찾으면 (값 또는 True), 없으면 None.

    regex=True면 정규식 검색 후 첫 번째 괄호 그룹(없으면 전체 일치)을 값으로 돌려준다.
    regex=False면 공백을 무시하고 포함 여부만 본다.
    """
    if regex:
        m = re.search(query, text) or re.search(query, squash(text))
        if not m:
            return None
        return m.group(1) if m.groups() else m.group(0)
    return True if squash(query) in squash(text) else None


# ---------- 영역 점검: 글자를 자르고 있는지 ----------
EDGE_NAMES = ("위", "아래", "왼쪽", "오른쪽")


def _ink(gray: np.ndarray, bg: int, thresh: int = 50) -> np.ndarray:
    return np.abs(gray.astype(np.int16) - bg) > thresh


def _background(gray: np.ndarray) -> int:
    return int(np.bincount(gray.ravel(), minlength=256).argmax())


def _gray(img: np.ndarray) -> np.ndarray:
    return np.asarray(Image.fromarray(np.ascontiguousarray(img)).convert("L"))


def _line_height(ink: np.ndarray) -> int:
    """영역 안 글자 줄 높이(가장 긴 연속 잉크 행 묶음) 추정."""
    rows = ink.any(axis=1).astype(np.int8)
    best = run = 0
    for v in rows:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def _cuts(ink: np.ndarray, x1, y1, x2, y2, inside: int = 2, outside: int | None = None,
          min_ink: int = 2) -> list[str]:
    """테두리가 글자를 자르는 방향 목록.

    - 안쪽 띠(inside px)에 글자가 닿으면 잘림.
    - 안쪽은 비었어도, 바로 바깥(outside px)에 안쪽 글자와 같은 줄/열의 글자가 이어지면 잘림
      (글자 사이 빈틈에 테두리가 걸린 경우). 단, 바깥이 통째로 다른 색(버튼 밖 배경 등)이면 무시.
    """
    h, w = ink.shape
    body = ink[y1:y2, x1:x2]
    rows_in, cols_in = body.any(axis=1), body.any(axis=0)
    if outside is None:  # 좌우: 띄어쓰기(글자 크기의 약 절반)는 건너뛰고, 다른 칸·열의 간격은 넘지 않게
        outside = max(6, min(16, int(_line_height(body) * 0.6)))
    vout = 3              # 위아래: 바로 붙은 것만 (윗줄·아랫줄은 다른 줄)
    specs = {
        "위": (ink[y1:y1 + inside, x1:x2], ink[max(0, y1 - vout):y1, x1:x2], 0, cols_in),
        "아래": (ink[y2 - inside:y2, x1:x2], ink[y2:min(h, y2 + vout), x1:x2], 0, cols_in),
        "왼쪽": (ink[y1:y2, x1:x1 + inside], ink[y1:y2, max(0, x1 - outside):x1], 1, rows_in),
        "오른쪽": (ink[y1:y2, x2 - inside:x2], ink[y1:y2, x2:min(w, x2 + outside)], 1, rows_in),
    }
    out = []
    for name, (inner, outer, axis, lines_in) in specs.items():
        if int(inner.sum()) >= min_ink:
            out.append(name)
            continue
        if outer.size == 0 or not outer.any():
            continue
        # 테두리와 나란히 꽉 찬 줄(버튼 가장자리, 다른 배경)은 글자가 아니므로 뺀다
        full = outer.mean(axis=1 - axis) > 0.9   # 위·아래면 행, 왼·오른쪽이면 열 단위
        outer = np.compress(~full, outer, axis=axis)
        if outer.size == 0 or outer.mean() > 0.6:
            continue
        if int((outer.any(axis=axis) & lines_in).sum()) >= min_ink:
            out.append(name)
    return out


def edge_cuts(img: np.ndarray, region) -> list[str]:
    """영역 테두리가 글자를 자르고 있으면 그 방향을 돌려준다.

    글자 줄 중간을 자르면 OCR이 엉뚱한 글자를 만들어 내므로(예: '주 181 Stonl') 미리 알려 준다.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in region)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 - x1 < 5 or y2 - y1 < 5:
        return []
    gray = _gray(img)
    ink = _ink(gray, _background(gray[y1:y2, x1:x2]))
    return _cuts(ink, x1, y1, x2, y2)


def fit_region(img: np.ndarray, region, max_grow: int = 60, max_grow_x: int = 600, pad: int = 4):
    """글자를 자르지 않도록 영역을 넓힌 뒤, 글자 둘레에 딱 맞게 줄인다.

    돌려주는 값: (새 영역, 남은 잘림 방향 목록). 못 고치면 남은 방향이 있다.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in region)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    gray = _gray(img)
    ink = _ink(gray, _background(gray[y1:y2, x1:x2]))
    lim = (max(0, x1 - max_grow_x), max(0, y1 - max_grow), min(w, x2 + max_grow_x), min(h, y2 + max_grow))
    for _ in range(max_grow_x * 2 + max_grow * 2):
        c = _cuts(ink, x1, y1, x2, y2)
        if not c:
            break
        moved = False
        if "위" in c and y1 > lim[1]:
            y1 -= 1; moved = True
        if "아래" in c and y2 < lim[3]:
            y2 += 1; moved = True
        if "왼쪽" in c and x1 > lim[0]:
            x1 -= 1; moved = True
        if "오른쪽" in c and x2 < lim[2]:
            x2 += 1; moved = True
        if not moved:
            break
    left = _cuts(ink, x1, y1, x2, y2)
    ys, xs = np.nonzero(ink[y1:y2, x1:x2])
    if len(xs) and not left:  # 글자 둘레로 줄이기
        x1, y1, x2, y2 = (max(x1, x1 + int(xs.min()) - pad), max(y1, y1 + int(ys.min()) - pad),
                          min(x2, x1 + int(xs.max()) + 1 + pad), min(y2, y1 + int(ys.max()) + 1 + pad))
    return (x1, y1, x2, y2), left
