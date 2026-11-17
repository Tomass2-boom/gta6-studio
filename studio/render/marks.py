"""Circles and arrows that point at the thing that matters.

Automatic: at the clip's key moments (the sound-effect triggers, else the loudest point) look for
ONE small thing that moves on its own in the gameplay. Camera pans are removed first (phase
correlation), then the remaining motion must be a single compact blob that holds most of the
motion energy. Anything less clear gets no mark, so most clips get none.

By hand: data/marks.json, {clip name: [{t, x, y, kind?, r?, space?}]} with t in clip seconds and x, y, r
as shares of the source frame, or of the finished clip with space "out" (what a click on the clip gives). An entry (even an empty list) replaces the automatic choice.
`studio mark` writes it; the review page can later write the same file.
"""
import json
import math
import subprocess
import numpy as np
from ..config import DATA
from . import layout as L
from .captions import ass_color, ts

MANUAL = DATA / "marks.json"
SW = 320  # analysis width


def manual(name: str):
    """Hand-set marks for this clip, or None when there is no entry."""
    try:
        return json.loads(MANUAL.read_text()).get(name)
    except (OSError, ValueError):
        return None


def set_manual(name: str, marks: list | None):
    try:
        d = json.loads(MANUAL.read_text())
    except (OSError, ValueError):
        d = {}
    if marks is None:
        d.pop(name, None)
    else:
        d[name] = marks
    MANUAL.parent.mkdir(parents=True, exist_ok=True)
    MANUAL.write_text(json.dumps(d, indent=2))


def _frames(video: str, t0: float, t1: float, w: int, h: int, fps: float = 10) -> np.ndarray:
    sh = round(SW * h / w) // 2 * 2
    raw = subprocess.run([L.tools()[0], "-v", "error", "-ss", f"{max(0, t0):.3f}", "-t", f"{t1 - t0:.3f}", "-i", video,
                          "-vf", f"fps={fps},scale={SW}:{sh}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    k = len(raw) // (SW * sh)
    return np.frombuffer(raw[: k * SW * sh], np.uint8).reshape(k, sh, SW).astype(np.float32)


def motion_map(frames: np.ndarray) -> np.ndarray:
    """Summed frame difference after removing the global (camera) shift between frames."""
    import cv2
    acc = np.zeros(frames.shape[1:], np.float32)
    for p, c in zip(frames, frames[1:]):
        (dx, dy), _ = cv2.phaseCorrelate(p, c)
        m = np.float32([[1, 0, dx], [0, 1, dy]])
        pw = cv2.warpAffine(p, m, (p.shape[1], p.shape[0]), borderMode=cv2.BORDER_REPLICATE)
        d = np.abs(c - pw)
        b = int(math.ceil(max(abs(dx), abs(dy)))) + 2
        d[:b], d[-b:], d[:, :b], d[:, -b:] = 0, 0, 0, 0
        acc += d
    return acc


def find_blob(acc: np.ndarray, region, cfg: dict):
    """One compact moving blob in region (x,y,w,h in analysis px). Returns (cx, cy, r, why) or (None, why)."""
    import cv2
    x, y, w, h = region
    m = np.zeros_like(acc)
    m[y:y + h, x:x + w] = acc[y:y + h, x:x + w]
    total = float(m.sum())
    if total <= 0:
        return None, "no motion"
    thr = max(float(m[m > 0].mean() + 2 * m[m > 0].std()), 1.0)
    mask = (m > thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        return None, "no strong motion"
    energy = np.array([m[lab == i].sum() for i in range(1, n)])
    strong = float(m[mask > 0].sum())
    i = int(np.argmax(energy))
    area = stats[i + 1, cv2.CC_STAT_AREA] / float(w * h)
    share = energy[i] / max(strong, 1e-6)
    if not (cfg.get("min_area", 0.002) <= area <= cfg.get("max_area", 0.06)):
        return None, f"blob area {area:.3f} out of range"
    if share < cfg.get("min_share", 0.55):
        return None, f"motion spread out (biggest blob {share:.0%})"
    bw, bh = stats[i + 1, cv2.CC_STAT_WIDTH], stats[i + 1, cv2.CC_STAT_HEIGHT]
    return (float(cent[i + 1][0]), float(cent[i + 1][1]), 0.5 * max(bw, bh)), f"blob {area:.3f} of area, {share:.0%} of motion"


def auto(video: str, a: float, b: float, w: int, h: int, key_times: list[float], game_src, avoid, cfg: dict):
    """Automatic marks: [{t, x, y, r (shares of source), why}] (at most max_per_clip)."""
    out, notes = [], []
    f = SW / w
    gx, gy, gw, gh = game_src
    region = (int(gx * f), int(gy * f), int(gw * f), int(gh * f))
    for t in key_times:
        frames = _frames(video, a + t - 0.8, a + t + 0.4, w, h)
        if len(frames) < 4:
            continue
        acc = motion_map(frames)
        if avoid:  # never on the facecam
            ax, ay, aw, ah = (int(v * f) for v in avoid)
            acc[max(0, ay - 4): ay + ah + 4, max(0, ax - 4): ax + aw + 4] = 0
        res = find_blob(acc, region, cfg)
        if res[0] is None:
            notes.append(f"{t:.1f}s: {res[1]}")
            continue
        (cx, cy, r), why = res
        # it must travel: the blob in the first and second half of the moment are in different places
        mid = len(frames) // 2
        halves = []
        for part in (frames[: mid + 1], frames[mid:]):
            hm = motion_map(part)
            if avoid:
                hm[max(0, ay - 4): ay + ah + 4, max(0, ax - 4): ax + aw + 4] = 0
            halves.append(find_blob(hm, region, cfg)[0])
        if None in halves:
            notes.append(f"{t:.1f}s: not one moving thing in both halves")
            continue
        travel = math.dist(halves[0][:2], halves[1][:2]) / max(r, 1.0)
        if travel < cfg.get("min_travel", 1.0):
            notes.append(f"{t:.1f}s: blob stays in place (travel {travel:.1f}x its size)")
            continue
        cx, cy = halves[1][:2]  # where it is at the moment
        why += f", travels {travel:.1f}x its size"
        out.append({"t": round(t, 2), "x": cx / SW, "y": cy / (SW * h / w), "r": r / SW, "why": why})
        notes.append(f"{t:.1f}s: {why}")
        if len(out) >= cfg.get("max_per_clip", 1):
            break
    return out, notes


def _ellipse(r: float) -> str:
    k = 0.5523 * r
    f = lambda v: f"{v:.0f}"
    return (f"m {f(r)} 0 b {f(r + k)} 0 {f(2 * r)} {f(r - k)} {f(2 * r)} {f(r)} "
            f"b {f(2 * r)} {f(r + k)} {f(r + k)} {f(2 * r)} {f(r)} {f(2 * r)} "
            f"b {f(r - k)} {f(2 * r)} 0 {f(r + k)} 0 {f(r)} b 0 {f(r - k)} {f(r - k)} 0 {f(r)} 0")


def _arrow(length: float, width: float) -> str:
    """Arrow pointing right, tip at (length, width/2)."""
    hw, sh = width / 2, width * 0.22
    head = width * 0.9
    p = [(0, hw - sh), (length - head, hw - sh), (length - head, 0), (length, hw),
         (length - head, width), (length - head, hw + sh), (0, hw + sh)]
    return "m " + " l ".join(f"{x:.0f} {y:.0f}" for x, y in p)


def ass_events(marks_out: list[dict], cfg: dict, W: int, H: int) -> list[str]:
    """ASS dialogue lines for marks given in output pixels: [{t, x, y, r, kind}]."""
    ev = []
    col, ocol, th = ass_color(cfg.get("color", "FF2D2D")), ass_color(cfg.get("outline_color", "000000")), cfg.get("thickness", 12)
    show, pop = cfg.get("show_s", 1.4), cfg.get("pop_ms", 140)
    for m in marks_out:
        s, e = max(0.0, m["t"] - 0.15), m["t"] - 0.15 + show
        kind = m.get("kind") or cfg.get("kind", "circle")
        if kind == "arrow":
            ln, wd = 230, 90
            ang = 35  # comes in from the upper left, pointing down-right
            gap = m["r"] + 12
            tipx, tipy = m["x"] - gap * math.cos(math.radians(ang)), m["y"] - gap * math.sin(math.radians(ang))
            # drawing in \an7 at pos: tip sits at pos + (ln, wd/2); rotate around the tip
            px, py = tipx - ln, tipy - wd / 2
            shape = _arrow(ln, wd)
            tags = (f"\\an7\\pos({px:.0f},{py:.0f})\\org({tipx:.0f},{tipy:.0f})\\frz{-ang}\\p1"
                    f"\\fad(60,200)\\fscx70\\fscy70\\t(0,{pop},\\fscx100\\fscy100)")
            ev.append(f"Dialogue: 3,{ts(s)},{ts(e)},Box,,0,0,0,,{{{tags}\\1c{col}\\3c{ocol}\\bord{th // 2}}}{shape}")
        else:
            r = max(m["r"], 60)
            shape = _ellipse(r)
            tags = (f"\\an5\\pos({m['x']:.0f},{m['y']:.0f})\\p1\\1a&HFF&\\fad(60,200)"
                    f"\\fscx60\\fscy60\\t(0,{pop},\\fscx100\\fscy100)")
            ev.append(f"Dialogue: 3,{ts(s)},{ts(e)},Box,,0,0,0,,{{{tags}\\3c{ocol}\\bord{th + 6}}}{shape}")
            ev.append(f"Dialogue: 4,{ts(s)},{ts(e)},Box,,0,0,0,,{{{tags}\\3c{col}\\bord{th}}}{shape}")
    return ev


def to_output(m: dict, geo: dict, src_w: int, src_h: int, crop_x_at=None):
    """Map a mark (shares of the source frame, clip time) to output pixels via the gameplay crop.
    Returns None when the spot is not in the visible gameplay."""
    if m.get("space") == "out":  # set by hand on the finished clip: shares of the output frame
        W, H = geo["out_wh"]
        return {**m, "x": m["x"] * W, "y": m["y"] * H, "r": m.get("r", 0.08) * W}
    in_b = any(s0 <= m["t"] < e0 for s0, e0 in geo.get("b_ranges", []))
    (gx, gy, gw, gh), (ox, oy, ow, oh) = geo["game_b"] if in_b and "game_b" in geo else geo["game"]
    if crop_x_at is not None:
        gx = crop_x_at(m["t"])
    sx, sy, sr = m["x"] * src_w, m["y"] * src_h, m.get("r", 0.04) * src_w
    if not (gx <= sx <= gx + gw and gy <= sy <= gy + gh):
        return None
    k = ow / gw
    return {**m, "x": ox + (sx - gx) * k, "y": oy + (sy - gy) * k, "r": sr * k}
