import json
from pathlib import Path

import numpy as np
import pytest

from studio.render import facecam as F
from studio.render import layout as L

FIX = Path(__file__).parent / "fixtures" / "xqc_cam.mp4"  # xQc, 1280x720, cam top-left (hand-entered 0,0,250,188)


def close(box, truth, tol=0.04, W=1280, H=720):
    return all(abs(a - b) <= tol * s for a, b, s in zip(box, truth, (W, H, W, H)))


def test_detects_xqc_cam_top_left():
    res = F.detect(str(FIX), 0, 16, 1280, 720)
    assert res["box"], res
    assert close(res["box"], (0, 0, 250, 188)), res


def _synthetic(corner: str, n=6, W=640, H=360):
    """Noisy 'game' frames with a flat-bordered cam rectangle (textured inside) in the given corner."""
    rng = np.random.default_rng(1)
    w, h = 200, 150
    x = 0 if "l" in corner else W - w
    y = 0 if "t" in corner else H - h
    frames = []
    for _ in range(n):
        import cv2
        f = cv2.GaussianBlur(rng.normal(120, 60, (H, W)).astype(np.float32), (0, 0), 9)  # smooth game picture
        f[y:y + h, x:x + w] = 200  # cam background
        f[y + 40:y + 110, x + 70:x + 130] = 90  # "face" blob
        frames.append(f)
    return np.stack(frames), (x, y, w, h), (x + 70, y + 40, 60, 70)


@pytest.mark.parametrize("corner", ["tl", "tr", "bl", "br"])
def test_box_from_border_in_every_corner(corner):
    g, truth, face = _synthetic(corner)
    cands = F.find_boxes(g, face)
    assert cands and close(cands[0], truth, 0.03, 640, 360), cands


def test_no_cam_when_no_border():
    rng = np.random.default_rng(2)
    g = rng.normal(120, 30, (5, 360, 640)).astype(np.float32)
    assert F.find_boxes(g, (300, 150, 40, 50)) == []


def test_cache_roundtrip_scales_and_negative_is_per_video(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "CACHE", tmp_path / "facecams.json")
    (tmp_path / "facecams.json").write_text(json.dumps({
        "abc": {"box": [0, 0, 250, 188], "ref": [1280, 720]},
        "nocam": {"box": None, "video": "v1.mp4", "ref": [1280, 720]}}))
    assert F.cached_box("ABC", 1920, 1080) == ((0, 0, 375, 282), True)
    assert F.cached_box("nocam", 1920, 1080, "v1.mp4") == (None, True)
    assert F.cached_box("nocam", 1920, 1080, "v2.mp4") == (None, False)
    assert F.cached_box("new", 1920, 1080) == (None, False)


def test_edge_all_sides_needs_left_border():
    g, truth, _ = _synthetic("br")
    assert L._edge(g[0], *truth, all_sides=True)
