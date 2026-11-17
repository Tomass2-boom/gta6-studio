"""Automatic facecam detection for streamers without a hand-entered box.

Method: sample frames across a clip, find faces with OpenCV YuNet, keep the face that stays in
one place (a facecam; faces inside the game move), then grow a rectangle around it using the
straight border lines of the cam overlay (same idea as layout._edge). The result is cached per
streamer in data/facecams.json so detection runs once, not once per clip.
"""
import json
import logging
import subprocess
import time
from pathlib import Path
import numpy as np
from ..config import DATA, ROOT
from . import layout as L

log = logging.getLogger("studio.facecam")
MODEL = ROOT / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
CACHE = DATA / "facecams.json"

N_FRAMES = 16          # frames sampled per clip
MIN_SCORE = 0.7        # YuNet confidence
FACE_W = (0.02, 0.22)  # face width as a share of frame width
STABLE_TOL = 0.035     # face centres within this share of frame width count as "same place"
MIN_PRESENCE = 0.4     # a cam face must be seen in this share of the samples
EDGE_PAD = 12          # px: an edge this close to the screen border is the border itself


def sample_frames(video: str, a: float, b: float, w: int, h: int, n: int = N_FRAMES) -> np.ndarray:
    """n BGR frames evenly spread over [a,b] in one ffmpeg pass: shape (n, h, w, 3)."""
    step = max(0.5, (b - a) / n)
    raw = subprocess.run([L.tools()[0], "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video,
                          "-vf", f"fps=1/{step:.3f}", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True, check=True).stdout
    k = len(raw) // (w * h * 3)
    return np.frombuffer(raw[: k * w * h * 3], np.uint8).reshape(k, h, w, 3)


def find_faces(frames: np.ndarray) -> list[list[tuple[int, int, int, int]]]:
    """Faces (x, y, w, h) per frame, using YuNet."""
    import cv2
    n, h, w, _ = frames.shape
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), MIN_SCORE, 0.3, 50)
    out = []
    for f in frames:
        _, res = det.detect(f)
        faces = []
        for r in (res if res is not None else []):
            x, y, fw, fh = (int(v) for v in r[:4])
            x, y = max(0, x), max(0, y)
            if FACE_W[0] * w <= fw <= FACE_W[1] * w:
                faces.append((x, y, min(fw, w - x), min(fh, h - y)))
        out.append(faces)
    return out


def stable_face(faces: list[list[tuple]], w: int):
    """The face position seen in most frames. Returns (median face box, share of frames seen in, frame indices)."""
    best, best_n, best_idx = None, 0, []
    pts = [(i, f) for i, fs in enumerate(faces) for f in fs]
    for _, f in pts:
        cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
        mem = {}
        for i, g in pts:
            if abs(g[0] + g[2] / 2 - cx) < STABLE_TOL * w and abs(g[1] + g[3] / 2 - cy) < STABLE_TOL * w:
                mem[i] = g  # one face per frame
        if len(mem) > best_n:
            best, best_n, best_idx = list(mem.values()), len(mem), sorted(mem)
    if not best:
        return None
    share = best_n / max(1, len(faces))
    if share < MIN_PRESENCE:
        return None
    return tuple(int(np.median([g[k] for g in best])) for k in range(4)), share, best_idx


def _peak(prof: np.ndarray, lo: int, hi: int) -> int | None:
    """Index of the strongest straight edge in prof[lo:hi], or None if it does not stand out."""
    lo, hi = max(lo, 14), min(hi, len(prof) - 14)
    if hi <= lo:
        return None
    i = lo + int(np.argmax(prof[lo:hi]))
    bg = np.median(np.concatenate([prof[max(0, i - 40): i - 8], prof[i + 8: i + 40]]))
    return i if prof[i] > 2.5 * bg + 3 else None


def find_box(gray: np.ndarray, face: tuple, reach: float = 3.5) -> tuple[int, int, int, int] | None:
    """Grow the cam rectangle around `face` from straight border lines. gray: (n, h, w) float32."""
    n, H, W = gray.shape
    fx, fy, fw, fh = face
    r0, r1 = max(0, fy), min(H, fy + fh)    # rows over the face
    c0, c1 = max(0, fx), min(W, fx + fw)    # columns over the face

    def cols():  # strength of a vertical line at each column; straight lines win (median over rows)
        d = np.abs(np.diff(gray[:, r0:r1, :], axis=2))
        return np.median(d, axis=1).mean(axis=0)  # (W-1,), index c = edge between c and c+1

    def rows():
        d = np.abs(np.diff(gray[:, :, c0:c1], axis=1))
        return np.median(d, axis=2).mean(axis=0)  # (H-1,)

    pc, pr = cols(), rows()
    reach_x, reach_y = int(reach * fw), int(reach * fw)
    out = []
    for prof, size, lo_side, hi_side, reach_ in ((pc, W, fx, fx + fw, reach_x), (pr, H, fy, fy + fh, reach_y)):
        # low edge: search toward 0; high edge: toward the far side
        lo = _peak(prof, lo_side - reach_, lo_side)
        lo_e = lo + 1 if lo is not None else (0 if lo_side <= reach_ else None)
        hi = _peak(prof, hi_side, hi_side + reach_)
        hi_e = size if (hi is None and size - hi_side <= reach_) else (hi + 1 if hi is not None else None)
        if hi_e is not None and size - hi_e <= EDGE_PAD:
            hi_e = size
        if lo_e is not None and lo_e <= EDGE_PAD:
            lo_e = 0
        if lo_e is None or hi_e is None:
            return None
        out.append((lo_e, hi_e))
    (x0, x1), (y0, y1) = out
    w, h = x1 - x0, y1 - y0
    if not (0.08 * W <= w <= 0.5 * W and 0.12 * H <= h <= 0.6 * H) or w * h > 0.3 * W * H:
        return None
    if not (x0 <= fx and x1 >= fx + fw and y0 <= fy and y1 >= fy + fh):
        return None
    return x0, y0, w, h


def _confirm(gray: np.ndarray, box) -> float:
    """Share of sampled frames in which the cam border lines are present (all four sides tested)."""
    return float(np.mean([L._edge(f, *box, all_sides=True) for f in gray]))


def detect(video: str, a: float, b: float, w: int, h: int) -> dict:
    """Find the facecam in [a,b] of `video`. Returns {'box': [x,y,w,h] | None, 'why': str, ...}."""
    import cv2
    t0 = time.time()
    frames = sample_frames(video, a, b, w, h)
    if len(frames) < 3:
        return {"box": None, "why": f"only {len(frames)} frames could be read"}
    faces = find_faces(frames)
    seen = sum(bool(f) for f in faces)
    st = stable_face(faces, w)
    if not st:
        return {"box": None, "why": f"no stable face ({seen}/{len(frames)} frames had any face)"}
    face, share, idx = st
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)
    box = find_box(gray[idx], face)  # only frames where the cam was on
    if not box:
        return {"box": None, "face": list(face), "why": "stable face but no cam border found around it"}
    conf = _confirm(gray[idx], box)
    if conf < 0.6:
        return {"box": None, "face": list(face), "box_tried": list(box),
                "why": f"border lines present in only {conf:.0%} of frames"}
    return {"box": list(box), "face": list(face), "face_share": round(share, 2), "border_share": round(conf, 2),
            "why": "ok", "secs": round(time.time() - t0, 1)}


# ---- per-streamer cache -------------------------------------------------------------------

def _load() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def cached_box(streamer: str, w: int, h: int, video: str | None = None):
    """(box scaled to w x h, or None, known) from the cache. A 'no cam' result is only trusted for
    the same video, since another stream by the same person may have a cam."""
    e = _load().get(str(streamer).lower())
    if not e:
        return None, False
    if e.get("box"):
        rw, rh = e["ref"]
        fx, fy = w / rw, h / rh
        x, y, bw, bh = e["box"]
        return (round(x * fx), round(y * fy), round(bw * fx), round(bh * fy)), True
    return None, e.get("video") == video


def auto_box(streamer: str, video: str, a: float, b: float, w: int, h: int):
    """Cached or freshly detected cam box for this streamer. Returns (box | None, note)."""
    box, known = cached_box(streamer, w, h, video)
    if known:
        return box, "auto facecam (cached)" if box else "no cam found earlier on this video"
    res = detect(video, a, b, w, h)
    cache = _load()
    cache[str(streamer).lower()] = {**res, "ref": [w, h], "video": video, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2))
    log.info("facecam %s: %s", streamer, res)
    if res["box"]:
        return tuple(res["box"]), f"auto facecam found {res['box']}"
    return None, f"no facecam found ({res['why']})"
