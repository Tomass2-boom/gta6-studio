"""Render one moment as a finished 1080x1920 clip: layout + captions + loudness."""
import hashlib
import os
import subprocess
import tempfile
from ..config import DATA, load
from .captions import FONTS, build_ass
from . import layout as L
from . import facecam as FC


def _mix_pick(style: dict, key: str) -> str | None:
    """Stable pseudo-random pick from layout_mix for this clip, or None for the auto choice."""
    u = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for lay, share in (style.get("layout_mix") or {}).items():
        acc += float(share)
        if u < acc:
            return str(lay).upper()
    return None


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
    if want in ("AUTO", "A"):
        if not cam:
            if want == "A":
                raise ValueError(f"layout A needs a facecam box ({how})")
            return "B", None, [], how
        tl = L.cam_timeline(src["path"], a, b, cam, w, h)
        share = sum(tl) / max(1, len(tl))
        if want == "AUTO" and share < style["layout_a"]["cam_presence"]:
            return "B", None, [], f"cam seen in only {share:.0%} of the clip"
        off = L.off_ranges(tl, style["layout_a"]["switch_min_s"])
        return "A", cam, off, f"cam {how}; seen in {share:.0%} of the clip, B during {off}"
    return want, cam, [], "forced"


def render(src: dict, a: float, b: float, words: list[dict], keywords: list[str], title: str | None,
           name: str, force_layout: str | None = None) -> tuple[str, str, str]:
    """words: [{word,start,end}] in source seconds. Returns (path, layout, note)."""
    style = load("style")
    out = style["output"]
    w, h = L.probe(src["path"])
    lay, cam, off, note = choose_layout(style, src, a, b, w, h, force_layout, name)
    graph, geo = L.build_switching(style, w, h, cam, off) if off else L.build(lay, style, w, h, cam)
    clip_words = [{**x, "start": x["start"] - a, "end": x["end"] - a} for x in words
                  if x["start"] >= a - 0.05 and x["end"] <= b + 0.05]
    ffmpeg = L.tools()[0]
    dest = DATA / "clips"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name}.mp4"
    with tempfile.TemporaryDirectory() as d:
        ass = os.path.join(d, "c.ass")
        open(ass, "w").write(build_ass(clip_words, keywords, style, lay, geo, b - a, title))
        fonts = str(FONTS).replace("'", r"\'")
        vf = f"{graph};[base]fps={out['fps']},ass=filename='{ass}':fontsdir='{fonts}',format=yuv420p[v]"
        af = f"loudnorm=I={out['loudness_lufs']}:TP=-1.5:LRA=11,aresample=48000"
        tmp = os.path.join(d, "out.mp4")
        r = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src["path"],
                            "-filter_complex", vf, "-map", "[v]", "-map", "0:a?", "-af", af,
                            "-c:v", "libx264", "-preset", "medium", "-crf", str(out["crf"]),
                            "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"ffmpeg failed: {r.stderr[-800:]}")
        os.replace(tmp, path)
    return str(path), lay, note
