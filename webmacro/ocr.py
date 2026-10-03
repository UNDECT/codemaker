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


def tesseract_path() -> str | None:
    return os.environ.get("WEBMACRO_TESSERACT") or shutil.which("tesseract")


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
