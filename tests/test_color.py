import time

import numpy as np
import pytest

from webmacro.color import find_blobs, parse_color, pick_blob


def canvas(w=400, h=300):
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_parse_color():
    assert parse_color("#FF0000") == (255, 0, 0)
    assert parse_color("00ff10") == (0, 255, 16)
    assert parse_color([1, 2, 3]) == (1, 2, 3)
    with pytest.raises(ValueError):
        parse_color("#FFF")
    with pytest.raises(ValueError):
        parse_color([300, 0, 0])


def test_finds_separate_blobs_with_centers():
    img = canvas()
    img[10:30, 20:60] = (255, 0, 0)     # 40x20, 중심 (39.5, 19.5)
    img[200:210, 300:310] = (250, 5, 5)  # 10x10, 허용오차 안
    blobs = find_blobs(img, (255, 0, 0), tolerance=10)
    assert len(blobs) == 2
    big, small = blobs
    assert big.pixels == 800 and small.pixels == 100
    assert abs(big.x - 40) <= 1 and abs(big.y - 20) <= 1
    assert big.bbox == (20, 10, 60, 30)
    assert abs(small.x - 305) <= 1 and abs(small.y - 205) <= 1


def test_tolerance_excludes():
    img = canvas()
    img[0:10, 0:10] = (200, 0, 0)
    assert find_blobs(img, (255, 0, 0), tolerance=10) == []
    assert len(find_blobs(img, (255, 0, 0), tolerance=60)) == 1


def test_region_and_min_pixels():
    img = canvas()
    img[10:20, 10:20] = (0, 0, 255)
    img[100:140, 100:140] = (0, 0, 255)
    only = find_blobs(img, (0, 0, 255), region=(50, 50, 400, 300))
    assert len(only) == 1 and abs(only[0].x - 120) <= 1  # 좌표는 화면 기준
    assert len(find_blobs(img, (0, 0, 255), min_pixels=500)) == 1
    assert find_blobs(img, (0, 0, 255), region=(500, 500, 600, 600)) == []


def test_nearby_bboxes_dont_mix_pixels():
    img = canvas()
    img[0:4, 0:40] = (0, 255, 0)     # 가로 막대
    img[30:34, 0:4] = (0, 255, 0)    # 멀리 떨어진 작은 점
    blobs = find_blobs(img, (0, 255, 0), tolerance=0)
    assert sorted(b.pixels for b in blobs) == [16, 160]


def test_pick():
    img = canvas()
    img[200:260, 10:70] = (9, 9, 9)    # 크고 아래
    img[10:20, 300:310] = (9, 9, 9)    # 작고 위
    blobs = find_blobs(img, (9, 9, 9), tolerance=0)
    assert pick_blob(blobs, "largest").pixels == 3600
    assert pick_blob(blobs, "topmost").y < 30
    assert pick_blob(blobs, "leftmost").x < 100
    assert pick_blob(blobs, "first").y < 30
    assert pick_blob([], "largest") is None


def test_full_hd_speed():
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    img[300:500, 400:700] = (255, 0, 0)  # 300x200 큰 덩어리
    t = time.perf_counter()
    blobs = find_blobs(img, (255, 0, 0))
    assert time.perf_counter() - t < 1.0
    assert blobs[0].pixels == 60000
