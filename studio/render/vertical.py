"""Render one moment as a finished 1080x1920 clip: layout + captions + loudness."""
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from ..config import DATA, load
from .captions import FONTS, build_ass
from . import layout as L
from . import facecam as FC
from . import marks as M
from . import sfx as S
import numpy as np


def _mix_pick(style: dict, key: str) -> str | None:
    """Stable pseudo-random pick from layout_mix for this clip, or None for the auto choice."""
    u = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for lay, share in (style.get("layout_mix") or {}).items():
        acc += float(share)
        if u < acc:
            return str(lay).upper()
    return None


def _cutout_box(style: dict, src: dict, a: float, b: float, w: int, h: int):
    """A borderless webcam (green-screen cut-out over the game, e.g. IShowSpeed) has no edge to detect.
    If a big face sits steadily in a lower corner, box it as the cam so layout A shows face AND action
    (instead of layout B's crop chasing the face off the gameplay). None if it does not look like that."""
    from . import facetrack as T
    _, best = T.faces_over_time(src["path"], a, min(b, a + 30), w, h, 1.0)
    faces = [f for f in best if f and f[2] >= (style.get("face_track") or {}).get("min_face_w", 0.06) * w]
    if not best or len(faces) / len(best) < 0.5:
        return None
    fx, fy, fw, fh = (float(np.median([f[i] for f in faces])) for i in range(4))
    cx, cy = fx + fw / 2, fy + fh / 2
    if cy < 0.5 * h or 0.35 * w < cx < 0.65 * w:  # not low, or centred: a full-screen camera, not a cut-out
        return None
    bw = min(w, 3.5 * fw)  # the cut-out (head + shoulders) is ~3.5 face widths wide
    bh = min(h, bw / 1.4)
    x0 = min(max(0.0, cx - bw / 2), w - bw)
    y0 = min(max(0.0, cy - 0.45 * bh), h - bh)
    return int(x0) // 2 * 2, int(y0) // 2 * 2, int(bw) // 2 * 2, int(bh) // 2 * 2


def _b_or_c(style: dict, src: dict, a: float, b: float, w: int, h: int, why: str):
    """Auto fallback B, unless the source has a big black band (multi-POV overlays): then C shows it whole."""
    lim = style.get("layout_b", {}).get("black_band_to_c")
    if lim:
        band = L.black_band(src["path"], a, b, w, h)
        if band >= lim:
            return "C", None, [], f"{why}; black band {band:.0%} of the height, so C"
    return "B", None, [], why


def choose_layout(style: dict, src: dict, a: float, b: float, w: int, h: int, force: str | None = None,
                  key: str = ""):
    """Return (layout, cam_box, cam-off ranges in clip seconds, note)."""
    want = (force or style.get("layout", "auto")).upper()
    if not force and want == "AUTO":
        pick = _mix_pick(style, key)
        if pick in ("B", "C"):
            return pick, None, [], "layout_mix experiment"
    cam = L.facecam_box(src["streamer"], w, h)
    how = "from streamers.yaml" if cam else None
    if want in ("AUTO", "A") and not cam:  # no hand-entered box: stored or auto-detected one
        cam, how = FC.auto_box(src["streamer"], src["path"], a, b, w, h)
        if not cam:
            cut = _cutout_box(style, src, a, b, w, h)
            if cut:
                cam, how = cut, "face cut-out in a lower corner (no border)"
    if want in ("AUTO", "A"):
        if not cam:
            if want == "A":
                raise ValueError(f"layout A needs a facecam box ({how})")
            return _b_or_c(style, src, a, b, w, h, how)
        tl = L.cam_timeline(src["path"], a, b, cam, w, h)
        share = sum(tl) / max(1, len(tl))
        if share < style["layout_a"]["cam_presence"] and how.startswith(("from streamers.yaml", "face cut-out")):
            # Borderless cam (green-screen cut-out, e.g. IShowSpeed): no edge to find, so look for the face instead.
            from . import facetrack as T
            _, best = T.faces_over_time(src["path"], a, b, w, h, 1.0, cam)
            tl = [f is not None for f in best]
            share = sum(tl) / max(1, len(tl))
            how += ", face check (no cam border)"
        if want == "AUTO" and share < style["layout_a"]["cam_presence"]:
            return _b_or_c(style, src, a, b, w, h, f"cam seen in only {share:.0%} of the clip")
        off = L.off_ranges(tl, style["layout_a"]["switch_min_s"])
        return "A", cam, off, f"cam {how}; seen in {share:.0%} of the clip, B during {off}"
    return want, cam, [], "forced"


def _track(style: dict, src: dict, a: float, b: float, w: int, h: int, lay: str, cam, off, d: str, info: dict):
    """Face-tracking sendcmd scripts for the crops, or {} (no tracking). Writes the scripts into d."""
    cfg = style.get("face_track") or {}
    if not cfg.get("enabled") or lay not in ("A", "B"):
        return {}
    from . import facetrack as T
    fps = style["output"]["fps"]
    dur = b - a
    _, geo = L.build(lay, style, w, h, cam)
    tr = {}
    if lay == "A":
        cw, ch, (bx, by, bw, bh) = geo["cam_crop"]
        if bw - cw < 4 and bh - ch < 4:
            info["face_track"] = "cam box has the crop's shape: nothing to follow"
            return {}
        times, best = T.faces_over_time(src["path"], a, b, w, h, cfg.get("sample_fps", 5), (bx, by, bw, bh))
        seen = sum(f is not None for f in best) / max(1, len(best))
        if seen < 0.3:
            info["face_track"] = f"face seen in only {seen:.0%} of samples: not tracked"
            return {}
        cmds, xy, stats = [], [], {}
        for axis, key, crop, lo, hi in ((0, "x", cw, bx, bx + bw - cw), (1, "y", ch, by, by + bh - ch)):
            if hi - lo < 4:
                xy.append(lo)
                continue
            path = T.smooth_path(times, T.centres(best, axis), crop, lo, hi, fps, dur, cfg)
            cmds.append(T.sendcmd("crop@cam", key, path, fps))
            xy.append(int(round(path[0])) // 2 * 2)
            stats[key] = T.jitter(path)
        f = os.path.join(d, "cam.cmd")
        open(f, "w").write("".join(cmds))
        tr = {"cam": f, "cam_xy": tuple(xy)}
        info["face_track"] = {"mode": "cam box", "seen": round(seen, 2), **stats}
    elif not cam:
        gw, gh = geo["game_crop"]
        times, best = T.faces_over_time(src["path"], a, b, w, h, cfg.get("sample_fps", 5))
        if not T.full_frame_face(times, best, w, cfg):
            info["face_track"] = "no big steady face (not a full-screen camera)"
            return {}
        path = T.smooth_path(times, T.centres(best, 0), gw, 0, w - gw, fps, dur, cfg)
        f = os.path.join(d, "game.cmd")
        open(f, "w").write(T.sendcmd("crop@game", "x", path, fps))
        tr = {"game": f, "game_xy": (int(round(path[0])) // 2 * 2, 0), "game_path": path}
        info["face_track"] = {"mode": "full-screen face", **T.jitter(path)}
    return tr


def _grade(style: dict) -> str:
    """Subtle colour grade + sharpening on the picture (before captions, so they stay pure white).
    Small on purpose: streams look flat and soft after upscaling; this makes them look 'finished'."""
    g = style.get("grade") or {}
    if not g.get("enabled"):
        return ""
    f = f"eq=contrast={g.get('contrast', 1)}:saturation={g.get('saturation', 1)}:brightness={g.get('brightness', 0)}:gamma={g.get('gamma', 1)},"
    if g.get("sharpen"):
        f += f"unsharp=5:5:{g['sharpen']}:5:5:0,"
    return f


def ffmpeg_cmd(ffmpeg: str, src_path: str, a: float, b: float, vf: str, af: str, crf, out: str,
               sfx_args: list | None = None, sfx_graph: str | None = None) -> list[str]:
    """The ffmpeg call. Without effects this is exactly the pre-S8 command (voice through -af)."""
    head = [ffmpeg, "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src_path]
    if sfx_graph:
        mid = sfx_args + ["-filter_complex", f"{vf};{sfx_graph}", "-map", "[v]", "-map", "[a]"]
    else:
        mid = ["-filter_complex", vf, "-map", "[v]", "-map", "0:a?", "-af", af]
    return head + mid + ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                         "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]


def render(src: dict, a: float, b: float, words: list[dict], keywords: list[str], title: str | None,
           name: str, force_layout: str | None = None, style: dict | None = None, out_dir=None,
           info: dict | None = None) -> tuple[str, str, str]:
    """words: [{word,start,end}] in source seconds. Returns (path, layout, note).
    style: override (default config/style.yaml); out_dir: where to write (default data/clips);
    info: filled with what the S8 extras did (effects, marks, face tracking)."""
    style = style or load("style")
    info = {} if info is None else info
    out = style["output"]
    W, H = out["width"], out["height"]
    w, h = L.probe(src["path"])
    lay, cam, off, note = choose_layout(style, src, a, b, w, h, force_layout, name)
    cut = (style["layout_a"].get("cutout") or {})
    if lay == "A" and ("no cam border" in note or "cut-out" in note) and cut.get("enabled"):
        # Borderless cut-out cam (IShowSpeed style): smaller face panel, more room for the action.
        style = {**style, "layout_a": {**style["layout_a"], "cam_height": cut["cam_height"], "_cutout": True}}
    clip_words = [{**x, "start": x["start"] - a, "end": x["end"] - a} for x in words
                  if x["start"] >= a - 0.05 and x["end"] <= b + 0.05]
    ffmpeg = L.tools()[0]
    dest = Path(out_dir) if out_dir else DATA / "clips"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name}.mp4"
    sfx_cfg, mk_cfg = style.get("sound_effects") or {}, style.get("marks") or {}
    with tempfile.TemporaryDirectory() as d:
        track = _track(style, src, a, b, w, h, lay, cam, off, d, info)
        graph, geo = (L.build_switching(style, w, h, cam, off, track) if off
                      else L.build(lay, style, w, h, cam, track))
        ass_text = build_ass(clip_words, keywords, style, lay, geo, b - a, title)
        env = None
        events = []
        if isinstance(sfx_cfg, dict) and sfx_cfg.get("enabled"):
            env = S.envelope(src["path"], a, b)
            cuts = sorted({round(x, 2) for r in off for x in r})
            events = S.place(clip_words, env, b - a, sfx_cfg, cuts) if len(env) else []
            info["sfx"] = events
        if isinstance(mk_cfg, dict) and mk_cfg.get("enabled"):
            ev = _marks(src, a, b, w, h, name, clip_words, title, geo, cam, track, style, info)
            if ev:
                ass_text += "\n".join(ev) + "\n"
        ass = os.path.join(d, "c.ass")
        open(ass, "w").write(ass_text)
        fonts = str(FONTS).replace("'", r"\'")
        vf = f"{graph};[base]fps={out['fps']},{_grade(style)}ass=filename='{ass}':fontsdir='{fonts}',format=yuv420p[v]"
        af = f"loudnorm=I={out['loudness_lufs']}:TP=-1.5:LRA=11,aresample=48000"
        sfx_args, sfx_graph = (S.audio_graph(events, af, duck_db=sfx_cfg.get("duck_db", 0), duck_s=sfx_cfg.get("duck_s", 0.25))
                              if events else (None, None))
        tmp = os.path.join(d, "out.mp4")
        r = subprocess.run(ffmpeg_cmd(ffmpeg, src["path"], a, b, vf, af, out["crf"], tmp, sfx_args, sfx_graph),
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"ffmpeg failed: {r.stderr[-800:]}")
        os.replace(tmp, path)
    extra = {k: info[k] for k in ("sfx", "marks", "face_track") if k in info}
    return str(path), lay, note + (f"; extras {extra}" if extra else "")


def _marks(src, a, b, w, h, name, clip_words, title, geo, cam, track, style, info) -> list[str]:
    """ASS lines for circles/arrows: hand-set ones from data/marks.json, else hook / speech-cue ones."""
    cfg = style["marks"]
    W, H = style["output"]["width"], style["output"]["height"]
    path = track.get("game_path")
    fps = style["output"]["fps"]
    at = (lambda t: float(path[min(len(path) - 1, int(t * fps))])) if path is not None else None
    g = {**geo, "out_wh": (W, H)}
    hand = M.manual(name)
    out = []
    if hand is not None:
        how = "by hand"
        out = [o for o in (M.to_output(m, g, w, h, at) for m in hand) if o]
    else:
        found, notes = M.find(src["path"], a, clip_words, b - a, title, geo["game"][0], cfg)
        how = "; ".join(notes) or "no hook words and no cue"
        cap_h = style["captions"]["size"] * 0.7
        for f in found:
            pts = []
            for t, x, y, r in f["path"]:
                o = M.to_output({"t": t, "x": x / w, "y": y / h, "r": r / w}, g, w, h, at)
                if not o:  # left the visible picture: stop following here
                    break
                pts.append((t, o["x"], o["y"], o["r"]))
            if len(pts) < 4:
                how += f"; {f['target']} not in the visible picture"
                continue
            cy = geo.get("caption_y_b") if any(s0 <= f["t"] < e0 for s0, e0 in geo.get("b_ranges", [])) else geo["caption_y"]
            if abs(np.median([p[2] for p in pts]) - cy) < cap_h:  # never on the caption band
                how += f"; {f['target']} sits under the captions"
                continue
            out.append({"t": f["t"], "path": pts, "target": f["target"], "why": f["why"]})
    info["marks"] = {"how": how, "placed": [{k: v for k, v in o.items() if k != "path"} |
                                            ({"points": len(o["path"])} if "path" in o else {}) for o in out]}
    return M.ass_events(out, cfg, W, H)
