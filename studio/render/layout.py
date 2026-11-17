"""Layouts A/B/C as ffmpeg filtergraphs, and the facecam check."""
import json
import subprocess
import numpy as np
from ..config import load


def tools() -> tuple[str, str]:
    r = load("settings").get("render", {})
    return r.get("ffmpeg", "ffmpeg"), r.get("ffprobe", "ffprobe")


def probe(video: str) -> tuple[int, int]:
    out = subprocess.run([tools()[1], "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", video], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return s["width"], s["height"]


def facecam_box(streamer: str | None, src_w: int, src_h: int) -> tuple[int, int, int, int] | None:
    """The streamer's facecam box from streamers.yaml, scaled to this video's size."""
    for s in load("streamers").get("streamers", []) or []:
        if str(s.get("name", "")).lower() == str(streamer or "").lower() and s.get("facecam"):
            rw, rh = s.get("ref", [src_w, src_h])
            x, y, w, h = s["facecam"]
            fx, fy = src_w / rw, src_h / rh
            return round(x * fx), round(y * fy), round(w * fx), round(h * fy)
    return None


def _edge(f, x, y, w, h, all_sides: bool = False) -> bool:
    """A cam overlay leaves a sharp straight line at its right/bottom edge that the game picture lacks."""
    src_h, src_w = f.shape
    ok = []
    if x + w + 14 < src_w:
        col = lambda c: np.abs(f[y + 4:y + h - 4, c + 1] - f[y + 4:y + h - 4, c - 1]).mean()
        ok.append(max(col(x + w + d) for d in (-2, -1, 0, 1)) > 2.5 * np.median([col(x + w + d) for d in (-12, -9, -6, 6, 9, 12)]) + 3)
    if y + h + 14 < src_h:
        row = lambda r: np.abs(f[r + 1, x + 4:x + w - 4] - f[r - 1, x + 4:x + w - 4]).mean()
        ok.append(max(row(y + h + d) for d in (-2, -1, 0, 1)) > 2.5 * np.median([row(y + h + d) for d in (-12, -9, -6, 6, 9, 12)]) + 3)
    if all_sides:  # auto-detected boxes: also test the left/top border when the cam floats away from the screen edge
        if x > 14:
            col = lambda c: np.abs(f[y + 4:y + h - 4, c + 1] - f[y + 4:y + h - 4, c - 1]).mean()
            ok.append(max(col(x + d) for d in (-2, -1, 0, 1)) > 2.5 * np.median([col(x + d) for d in (-12, -9, -6, 6, 9, 12)]) + 3)
        if y > 14:
            row = lambda r: np.abs(f[r + 1, x + 4:x + w - 4] - f[r - 1, x + 4:x + w - 4]).mean()
            ok.append(max(row(y + d) for d in (-2, -1, 0, 1)) > 2.5 * np.median([row(y + d) for d in (-12, -9, -6, 6, 9, 12)]) + 3)
    return bool(ok) and all(ok)


def cam_timeline(video: str, a: float, b: float, box, src_w: int, src_h: int, step: float = 1.0) -> list[bool]:
    """Is the facecam visible, one entry per `step` seconds of [a,b] (one ffmpeg pass)."""
    raw = subprocess.run([tools()[0], "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video,
                          "-vf", f"fps=1/{step}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    n = len(raw) // (src_w * src_h)
    frames = np.frombuffer(raw[: n * src_w * src_h], np.uint8).reshape(n, src_h, src_w).astype(np.float32)
    return [_edge(f, *box) for f in frames]


def off_ranges(timeline: list[bool], min_s: float, step: float = 1.0) -> list[tuple[float, float]]:
    """Time ranges (clip seconds) where the cam is off for at least min_s. One-sample blips are smoothed."""
    t = list(timeline)
    for i in range(1, len(t) - 1):  # fill single-sample flips
        if t[i - 1] == t[i + 1] != t[i]:
            t[i] = t[i - 1]
    out, i = [], 0
    while i < len(t):
        if not t[i]:
            j = i
            while j < len(t) and not t[j]:
                j += 1
            if (j - i) * step >= min_s:  # pad: a sample stands for roughly the second around it
                out.append((max(0.0, (i - 0.5) * step), (j + 0.5) * step))
            i = j
        else:
            i += 1
    return out


def _crop_aspect(x, y, w, h, aspect, focus_x=0.5, focus_y=0.5):
    """Largest crop of box (x,y,w,h) with width/height = aspect."""
    if w / h > aspect:
        cw, ch = round(h * aspect), h
    else:
        cw, ch = w, round(w / aspect)
    cw, ch = cw - cw % 2, ch - ch % 2
    return cw, ch, x + round((w - cw) * focus_x), y + round((h - ch) * focus_y)


def build_switching(style: dict, src_w: int, src_h: int, cam, ranges) -> tuple[str, dict]:
    """Layout A, switching to B during `ranges` (cam off). Captions move with it."""
    ga, geo = build("A", style, src_w, src_h, cam)
    gb, geo_b = build("B", style, src_w, src_h)
    ga = ga.replace("[0:v]", "[ia]", 1).replace("[base]", "[pa]")
    gb = gb.replace("[0:v]", "[ib]", 1).replace("[base]", "[pb]")
    expr = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in ranges)
    g = f"[0:v]split=2[ia][ib];{ga};{gb};[pa][pb]overlay=0:0:enable='{expr}'[base]"
    return g, {**geo, "caption_y_b": geo_b["caption_y"], "b_ranges": ranges}


def build(layout: str, style: dict, src_w: int, src_h: int, cam=None) -> tuple[str, dict]:
    """Return (filtergraph from [0:v] to [base], geometry for captions)."""
    W, H = style["output"]["width"], style["output"]["height"]
    if layout == "A":
        A = style["layout_a"]
        ch_out = round(H * A["cam_height"]) // 2 * 2
        gh_out = H - ch_out
        x, y, w, h = cam
        x, y, w, h = x + 2, y + 2, w - 4, h - 4  # stay inside the cam border
        cw, chh, cx, cy = _crop_aspect(x, y, w, h, W / ch_out, 0.5, A["cam_focus_y"])
        gw, gh, gx, gy = _crop_aspect(0, 0, src_w, src_h, W / gh_out, A["gameplay_focus_x"])
        g = (f"[0:v]split=2[s1][s2];[s1]crop={cw}:{chh}:{cx}:{cy},scale={W}:{ch_out}:flags=lanczos,setsar=1[cam];"
             f"[s2]crop={gw}:{gh}:{gx}:{gy},scale={W}:{gh_out}:flags=lanczos,setsar=1[game];"
             f"[cam][game]vstack[base]")
        cap = style["captions"]["position_a"]
        y_cap = ch_out if cap == "seam" else float(cap) * H
        return g, {"caption_y": y_cap}
    if layout == "B":
        gw, gh, gx, gy = _crop_aspect(0, 0, src_w, src_h, W / H, style["layout_b"]["gameplay_focus_x"])
        g = f"[0:v]crop={gw}:{gh}:{gx}:{gy},scale={W}:{H}:flags=lanczos,setsar=1[base]"
        return g, {"caption_y": float(style["captions"]["position_b"]) * H}
    if layout == "C":
        C = style["layout_c"]
        vh = round(W * src_h / src_w) // 2 * 2
        top = round(C["video_center_y"] * H - vh / 2)
        if C["background"] == "blur":
            bg = (f"[s1]scale=-2:{H},crop={W}:{H},boxblur={C['blur']}:2,eq=brightness=-0.15[bg]")
        else:
            bg = f"[s1]scale={W}:{H},drawbox=c=black:t=fill[bg]"
        g = (f"[0:v]split=2[s1][s2];{bg};[s2]scale={W}:{vh}:flags=lanczos,setsar=1[fg];"
             f"[bg][fg]overlay=0:{top}[base]")
        cap = style["captions"]
        y_cap = top + vh + cap["size"] * 0.9 if cap["position_c"] == "below_video" else float(cap["position_c"]) * H
        return g, {"caption_y": y_cap, "video_top": top}
    raise ValueError(f"unknown layout {layout}")
