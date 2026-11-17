"""Face tracking: a smooth crop path that keeps the streamer's face in frame.

Faces come from YuNet (same model as facecam.py) at a few samples per second. The path is made
calm on purpose: outliers are removed with a median filter, missed samples hold the last spot,
small moves inside a deadband are ignored, and the crop follows with a time constant and a speed
limit. The result drives an ffmpeg crop through sendcmd, one command per output frame.
"""
import numpy as np
from . import facecam as FC


def faces_over_time(video: str, a: float, b: float, w: int, h: int, fps: float,
                    region=None) -> tuple[np.ndarray, list]:
    """Sample times (clip s) and the biggest face per sample (source px), inside region (x,y,w,h) or the frame.
    Frames are read at `fps` and half size (faces here are big), in one ffmpeg pass."""
    import subprocess
    from . import layout as L
    rx, ry, rw, rh = region or (0, 0, w, h)
    sw, sh = rw // 4 * 2, rh // 4 * 2
    raw = subprocess.run([L.tools()[0], "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video,
                          "-vf", f"fps={fps},crop={rw}:{rh}:{rx}:{ry},scale={sw}:{sh}", "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "-"], capture_output=True, check=True).stdout
    k = len(raw) // (sw * sh * 3)
    frames = np.frombuffer(raw[: k * sw * sh * 3], np.uint8).reshape(k, sh, sw, 3)
    fx, fy = rw / sw, rh / sh
    best = []
    for fs in _detect(frames):
        f = max(fs, key=lambda f: f[2] * f[3]) if fs else None
        best.append((rx + f[0] * fx, ry + f[1] * fy, f[2] * fx, f[3] * fy) if f else None)
    return np.arange(k) / fps, best


def _detect(frames: np.ndarray) -> list:
    """Like FC.find_faces but without its facecam size limits (a cam crop or a full-screen face is big)."""
    import cv2
    n, h, w, _ = frames.shape
    det = cv2.FaceDetectorYN.create(str(FC.MODEL), "", (w, h), FC.MIN_SCORE, 0.3, 20)
    out = []
    for f in frames:
        _, res = det.detect(f)
        out.append([tuple(int(v) for v in r[:4]) for r in (res if res is not None else []) if r[2] >= 0.03 * w])
    return out


def centres(best: list, axis: int) -> np.ndarray:
    """Face centre per sample along axis (0 = x, 1 = y); NaN where no face."""
    return np.array([f[axis] + f[axis + 2] / 2 if f else np.nan for f in best], dtype=float)


def smooth_path(times: np.ndarray, c: np.ndarray, crop: int, lo: float, hi: float, fps_out: float,
                duration: float, cfg: dict) -> np.ndarray:
    """Crop offset per output frame so that the face centre c (source px) sits in the crop middle.
    lo/hi: allowed offset range. Returns an array of offsets (px), one per output frame."""
    n_out = max(1, int(round(duration * fps_out)))
    if np.all(np.isnan(c)):
        return np.full(n_out, (lo + hi) / 2)
    # median filter (5 samples) over the seen points, then hold the last value over misses
    v = c.copy()
    seen = np.where(~np.isnan(v))[0]
    med = v.copy()
    for i in seen:
        win = v[max(0, i - 2): i + 3]
        med[i] = np.nanmedian(win)
    first = med[seen[0]]
    held, last = np.empty_like(med), first
    for i, x in enumerate(med):
        last = x if not np.isnan(x) else last
        held[i] = last
    target = np.clip(held - crop / 2, lo, hi)
    # per output frame: deadband + first-order follow with a speed limit
    tgt = np.interp(np.arange(n_out) / fps_out, times, target)
    dead, tau = cfg.get("deadband", 0.06) * crop, max(0.05, cfg.get("smooth_s", 0.7))
    vmax = cfg.get("max_speed", 0.5) * crop / fps_out
    alpha = 1 - np.exp(-1 / (fps_out * tau))
    out = np.empty(n_out)
    pos, goal = float(tgt[0]), float(tgt[0])
    for i in range(n_out):
        if abs(tgt[i] - goal) > dead:  # only re-aim when the face has really moved
            goal = float(tgt[i])
        step = np.clip((goal - pos) * alpha, -vmax, vmax)
        pos = float(np.clip(pos + step, lo, hi))
        out[i] = pos
    return out


def jitter(path: np.ndarray) -> dict:
    """How calm a path is: the largest per-frame step (px) and the number of direction reversals."""
    d = np.diff(np.round(path))
    nz = d[d != 0]
    rev = int(np.sum(np.sign(nz[1:]) != np.sign(nz[:-1]))) if len(nz) > 1 else 0
    return {"max_step_px": float(np.abs(d).max()) if len(d) else 0.0, "reversals": rev,
            "range_px": float(path.max() - path.min())}


def sendcmd(target: str, key: str, path: np.ndarray, fps_out: float) -> str:
    """sendcmd script: set `key` (x or y) on filter `target` each frame where the rounded value changes."""
    lines, last = [], None
    for i, v in enumerate(path):
        r = int(round(v)) // 2 * 2
        if r != last:
            lines.append(f"{i / fps_out:.3f} {target} {key} {r};")
            last = r
    return "\n".join(lines) + "\n"


def full_frame_face(times, best, w: int, cfg: dict) -> bool:
    """A big, steady face in most samples: a full-screen camera (no cam box)."""
    big = [f for f in best if f and f[2] >= cfg.get("min_face_w", 0.06) * w]
    return len(best) > 0 and len(big) / len(best) >= cfg.get("presence", 0.6)
