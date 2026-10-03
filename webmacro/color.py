"""스크린샷에서 지정 색상 덩어리(blob)를 찾는다.

numpy만 사용한다. 화면을 CELL×CELL 칸으로 나눠 색이 있는 칸끼리 이어 붙이는
방식이라 1920×1080 전체를 훑어도 수십 ms 안에 끝난다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CELL = 4  # 덩어리 연결 단위(px). 이보다 가까운 같은 색 픽셀은 한 덩어리로 본다.


@dataclass(frozen=True)
class Blob:
    x: int          # 중심 x (화면 좌표)
    y: int          # 중심 y
    pixels: int     # 일치 픽셀 수
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 (x2/y2 포함 안 함)


def parse_color(value) -> tuple[int, int, int]:
    """'#FF0000', 'FF0000', [255,0,0] 모두 허용."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        rgb = tuple(int(v) for v in value)
    elif isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) != 6:
            raise ValueError(f"색상 형식 오류: {value!r} (예: '#FF0000')")
        rgb = tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    else:
        raise ValueError(f"색상 형식 오류: {value!r}")
    if not all(0 <= v <= 255 for v in rgb):
        raise ValueError(f"색상 값은 0~255: {value!r}")
    return rgb  # type: ignore[return-value]


def color_mask(img: np.ndarray, rgb, tolerance: int) -> np.ndarray:
    """채널별 차이가 모두 tolerance 이하인 픽셀 True."""
    target = np.asarray(rgb, dtype=np.int16)
    diff = np.abs(img[:, :, :3].astype(np.int16) - target)
    return (diff <= tolerance).all(axis=2)


def find_blobs(img: np.ndarray, rgb, tolerance: int = 10,
               region: tuple[int, int, int, int] | None = None,
               min_pixels: int = 1) -> list[Blob]:
    """img(H×W×3 RGB)에서 색 덩어리를 찾아 픽셀 수 내림차순으로 돌려준다."""
    h, w = img.shape[:2]
    ox, oy = 0, 0
    if region is not None:
        x1, y1, x2, y2 = region
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return []
        img = img[y1:y2, x1:x2]
        ox, oy = x1, y1

    mask = color_mask(img, rgb, tolerance)
    if int(mask.sum()) < min_pixels:
        return []

    stats = _label_cells(mask)
    blobs: list[Blob] = []
    for n, sx, sy, x1, y1, x2, y2 in stats:
        if n < min_pixels:
            continue
        blobs.append(Blob(
            x=int(sx / n + 0.5) + ox,
            y=int(sy / n + 0.5) + oy,
            pixels=int(n),
            bbox=(int(x1) + ox, int(y1) + oy, int(x2) + 1 + ox, int(y2) + 1 + oy),
        ))

    blobs.sort(key=lambda b: -b.pixels)
    return blobs


def pick_blob(blobs: list[Blob], how: str = "largest") -> Blob | None:
    if not blobs:
        return None
    if how == "largest":
        return blobs[0]
    if how == "topmost":
        return min(blobs, key=lambda b: (b.bbox[1], b.bbox[0]))
    if how == "leftmost":
        return min(blobs, key=lambda b: (b.bbox[0], b.bbox[1]))
    if how == "first":  # 읽는 순서(위→아래, 왼→오른)
        return min(blobs, key=lambda b: (b.bbox[1] // 10, b.bbox[0]))
    raise ValueError(f"pick 값 오류: {how!r}")


def _label_cells(mask: np.ndarray):
    """CELL 칸 단위 8방향 연결 라벨링(벡터화). 덩어리별 (픽셀수, Σx, Σy, x1, y1, x2, y2) 목록 반환."""
    mh, mw = mask.shape
    gh, gw = -(-mh // CELL), -(-mw // CELL)
    padded = np.zeros((gh * CELL, gw * CELL), dtype=bool)
    padded[:mh, :mw] = mask
    blocks = padded.reshape(gh, CELL, gw, CELL)
    occ = blocks.any(axis=(1, 3))

    # 각 칸의 라벨 = 자기 평탄 인덱스. 이웃 최소값 전파 + 포인터 점프로 수렴시킨다.
    big = gh * gw
    lab = np.where(occ, np.arange(big).reshape(gh, gw), big)
    while True:
        p = np.pad(lab, 1, constant_values=big)
        m = lab
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                m = np.minimum(m, p[dy:dy + gh, dx:dx + gw])
        m = np.where(occ, m, big)
        flat = np.append(m.ravel(), big)
        for _ in range(4):
            flat = flat[flat]
        m = flat[:-1].reshape(gh, gw)
        if np.array_equal(m, lab):
            break
        lab = m

    # 차 있는 칸만 골라 칸별 픽셀 통계
    cy, cx = np.nonzero(occ)
    sub = blocks.transpose(0, 2, 1, 3)[cy, cx]           # (칸수, CELL, CELL)
    off = np.arange(CELL)
    px = cx[:, None, None] * CELL + off[None, None, :]    # 각 픽셀의 x
    py = cy[:, None, None] * CELL + off[None, :, None]    # 각 픽셀의 y
    px = np.broadcast_to(px, sub.shape)
    py = np.broadcast_to(py, sub.shape)
    inf = np.iinfo(np.int64).max
    c_n = sub.sum(axis=(1, 2))
    c_sx = (sub * px).sum(axis=(1, 2))
    c_sy = (sub * py).sum(axis=(1, 2))
    c_x1 = np.where(sub, px, inf).min(axis=(1, 2))
    c_y1 = np.where(sub, py, inf).min(axis=(1, 2))
    c_x2 = np.where(sub, px, -1).max(axis=(1, 2))
    c_y2 = np.where(sub, py, -1).max(axis=(1, 2))

    keys = lab[cy, cx]
    uniq, inv = np.unique(keys, return_inverse=True)
    k = len(uniq)
    n = np.bincount(inv, c_n, k).astype(np.int64)
    ssx = np.bincount(inv, c_sx, k)
    ssy = np.bincount(inv, c_sy, k)
    bx1 = np.full(k, inf); np.minimum.at(bx1, inv, c_x1)
    by1 = np.full(k, inf); np.minimum.at(by1, inv, c_y1)
    bx2 = np.full(k, -1); np.maximum.at(bx2, inv, c_x2)
    by2 = np.full(k, -1); np.maximum.at(by2, inv, c_y2)
    return list(zip(n, ssx, ssy, bx1, by1, bx2, by2))
