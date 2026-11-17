"""Circles and arrows that point at the thing that matters, only where it helps.

Two triggers (style.yaml `marks`), nothing else; most clips get no mark:
- hook: the opening words name someone or something ("oh he's running"). A local vision model
  (Ollama, gemma4) looks at the first frame with those words and gives that thing's box, or none.
- cue: the streamer says "look at that", "right there" and similar. Same question at that moment.
A tracker (OpenCV MIL) then follows the box for `follow_s`, and the circle + arrow move with it.
Any doubt (model unsure, box too big or small, tracker lost) means no mark.

By hand: data/marks.json, {clip name: [{t, x, y, kind?, r?, space?}]} with t in clip seconds and x, y, r
as shares of the source frame, or of the finished clip with space "out". An entry (even an empty list)
replaces the automatic choice. `studio mark` writes it.
"""
import base64
import json
import math
import re
import subprocess
import numpy as np
from ..config import DATA, load
from . import layout as L
from .captions import ass_color, ts

MANUAL = DATA / "marks.json"
TW = 640  # tracking width


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


# ---------- when (pure, tested) ----------

def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def triggers(words: list[dict], duration: float, cfg: dict) -> list[dict]:
    """[{t, why, text}] in clip seconds: the hook (t=0) and speech cues. words: [{word,start,end}]."""
    out = []
    if cfg.get("hook", True):
        hw = [w["word"] for w in words if w["start"] < cfg.get("hook_words_s", 4.0)]
        if len(hw) >= 2:
            out.append({"t": 0.0, "why": "hook", "text": " ".join(hw)})
    if cfg.get("cues", True):
        toks = [(_norm(w["word"]), w) for w in words]
        toks = [(t, w) for t, w in toks if t]
        for ph in cfg.get("cue_phrases", []):
            pt = [_norm(x) for x in ph.split()]
            for i in range(len(toks) - len(pt) + 1):
                if [t for t, _ in toks[i:i + len(pt)]] == pt:
                    t0 = toks[i][1]["start"]
                    ctx = " ".join(w["word"] for _, w in toks if t0 - 3 <= w["start"] <= t0 + 2)
                    out.append({"t": round(t0, 2), "why": f'cue "{ph}"', "text": ctx})
    out.sort(key=lambda x: x["t"])
    picked = []  # one per spot: cues inside the hook's follow, or close together, are the same moment
    for c in out:
        if c["t"] > duration - 1.0:
            continue
        if any(abs(c["t"] - p["t"]) < cfg.get("follow_s", 3.0) + 0.5 for p in picked):
            continue
        picked.append(c)
    return picked[: cfg.get("max_per_clip", 2)]


def box_ok(box, cfg: dict) -> tuple[bool, str]:
    """box (x, y, w, h) as shares of the gameplay picture."""
    if not box:
        return False, "no box"
    x, y, w, h = box
    if w <= 0 or h <= 0 or x < -0.01 or y < -0.01 or x + w > 1.01 or y + h > 1.01:
        return False, "box outside the picture"
    a = w * h
    if not (cfg.get("min_box", 0.002) <= a <= cfg.get("max_box", 0.15)):
        return False, f"box is {a:.1%} of the picture"
    return True, f"box {a:.1%}"


PRONOUNS = {"he", "he's", "hes", "she", "she's", "shes", "him", "her", "they", "they're", "them", "his", "guy", "dude", "man"}


def grounded(quote, target, text: str) -> tuple[bool, str]:
    """The model must quote the words that name the thing, those words must really be in what was said,
    and they must actually name it: a pronoun (he, she, him...) or a word shared with its target."""
    q = [_norm(x) for x in str(quote or "").split() if _norm(x)]
    said = [_norm(x) for x in text.split() if _norm(x)]
    if not q or not any(said[i:i + len(q)] == q for i in range(len(said) - len(q) + 1)):
        return False, f"quote '{quote}' is not in what was said"
    tw = [_norm(x) for x in str(target).split() if len(_norm(x)) >= 3]
    if set(q) & PRONOUNS or any(a[:4] == b[:4] for a in q if len(a) >= 3 for b in tw):
        return True, f"named by '{quote}'"
    return False, f"'{quote}' does not name {target}"


def parse_box(reply: dict, cfg: dict, text: str | None = None):
    """The model's JSON -> ((x,y,w,h) shares, target, why) or (None, None, why).
    text: what was said; when given, the reply's quote must be grounded in it."""
    tgt, b = reply.get("target"), reply.get("box")
    sure = float(reply.get("sure") or 0)
    if not tgt or not b or str(tgt).lower() in ("none", "null"):
        return None, None, "model: nothing named and visible"
    if text is not None:
        ok, gwhy = grounded(reply.get("quote"), tgt, text)
        if not ok:
            return None, None, f"{tgt}: {gwhy}"
    if sure < cfg.get("min_sure", 0.7):
        return None, None, f"model unsure ({sure:.2f}) about {tgt}"
    try:
        x1, y1, x2, y2 = (float(v) / 1000 for v in b)
    except (TypeError, ValueError):
        return None, None, f"bad box {b}"
    box = (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
    ok, why = box_ok(box, cfg)
    return (box, tgt, why) if ok else (None, None, f"{tgt}: {why}")


# ---------- where (vision model + tracker) ----------

PROMPT = """This picture is from a GTA gameplay clip (vertical short). {when}
The streamer says: "{text}"
Do his words name ONE specific person, vehicle or object that is clearly visible in this picture?
Most of the time they don't: then answer target null. Do not guess, and do not pick something just
because it stands out. If they do, copy the exact words from his speech that name it into "quote",
and give its bounding box.
Reply ONLY JSON: {{"target": "short name" or null, "quote": "his exact words" or null, "box": [x1, y1, x2, y2] in 0-1000 of the picture width and height, "sure": 0.0-1.0}}"""


def _frame(video: str, t: float, crop, w: int = TW) -> bytes:
    gx, gy, gw, gh = crop
    return subprocess.run([L.tools()[0], "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", video, "-frames:v", "1",
                           "-vf", f"crop={gw}:{gh}:{gx}:{gy},scale={w}:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "3", "-"],
                          capture_output=True, check=True).stdout


def ask_box(jpg: bytes, text: str, when: str, cfg: dict):
    import requests
    url = load("settings")["judge"]["ollama_url"]
    r = requests.post(url + "/api/chat", timeout=300, json={
        "model": cfg.get("vlm_model", "gemma4:26b"), "stream": False, "think": False,
        "options": {"temperature": 0.1},
        "messages": [{"role": "user", "content": PROMPT.format(when=when, text=text.replace('"', "'")),
                      "images": [base64.b64encode(jpg).decode()]}]})
    r.raise_for_status()
    txt = r.json()["message"]["content"]
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0)) if m else {}


def _clip_frames(video: str, t0: float, dur: float, crop, fps: float) -> np.ndarray:
    gx, gy, gw, gh = crop
    th = round(TW * gh / gw) // 2 * 2
    raw = subprocess.run([L.tools()[0], "-v", "error", "-ss", f"{max(0, t0):.3f}", "-t", f"{dur:.3f}", "-i", video,
                          "-vf", f"fps={fps},crop={gw}:{gh}:{gx}:{gy},scale={TW}:{th}", "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "-"], capture_output=True, check=True).stdout
    k = len(raw) // (TW * th * 3)
    return np.frombuffer(raw[: k * TW * th * 3], np.uint8).reshape(k, th, TW, 3)


def track(frames: np.ndarray, box, fps: float, max_lost: float = 0.3):
    """Follow box (shares of the picture) through frames. [(t, cx, cy, r) shares] or (None, why)."""
    import cv2
    if not len(frames):
        return None, "no frames"
    th, tw = frames.shape[1:3]
    x, y, w, h = box
    bb = (int(x * tw), int(y * th), max(4, int(w * tw)), max(4, int(h * th)))
    tr = cv2.TrackerMIL_create()
    tr.init(frames[0], bb)
    pts, lost, last = [], 0, bb
    for i, f in enumerate(frames):
        if i:
            ok, b = tr.update(f)
            if ok:
                last = b
            else:
                lost += 1
        bx, by, bw, bh = last
        pts.append((i / fps, (bx + bw / 2) / tw, (by + bh / 2) / th, 0.5 * max(bw, bh) / tw))
    if lost > max_lost * len(frames):
        return None, f"tracker lost it in {lost}/{len(frames)} frames"
    return pts, f"tracked {len(frames)} frames, lost {lost}"


def find(video: str, a: float, words: list[dict], duration: float, title, game_src, cfg: dict):
    """Automatic marks: [{t, path: [(t, x, y, r) shares of source]}] and notes."""
    out, notes = [], []
    gx, gy, gw, gh = (int(v) for v in game_src)
    fps = cfg.get("track_fps", 10)
    for trg in triggers(words, duration, cfg):
        t = trg["t"] + (0.2 if trg["why"] == "hook" else 0.0)
        when = ("These are the clip's opening words." if trg["why"] == "hook"
                else "The streamer is pointing something out right now.")
        text = trg["text"] + (f'  (clip title: "{title}")' if title and trg["why"] == "hook" else "")
        try:
            reply = ask_box(_frame(video, a + t, (gx, gy, gw, gh)), text, when, cfg)
        except Exception as e:  # model down: no mark, never a failed clip
            notes.append(f"{trg['why']}: vision model failed ({type(e).__name__})")
            continue
        box, tgt, why = parse_box(reply, cfg, trg["text"])
        if not box:
            notes.append(f"{trg['why']} '{trg['text'][:40]}': {why}")
            continue
        frames = _clip_frames(video, a + t, cfg.get("follow_s", 3.0), (gx, gy, gw, gh), fps)
        pts, twhy = track(frames, box, fps, cfg.get("max_lost", 0.3))
        if not pts:
            notes.append(f"{trg['why']} {tgt}: {twhy}")
            continue
        path = [(round(t + pt, 2), (gx + cx * gw), (gy + cy * gh), r * gw) for pt, cx, cy, r in pts]
        out.append({"t": round(t, 2), "target": tgt, "why": trg["why"], "path": path})
        notes.append(f"{trg['why']} -> {tgt} ({why}, {twhy})")
    return out, notes


# ---------- drawing ----------

def _ellipse(r: float) -> str:
    k = 0.5523 * r
    f = lambda v: f"{v:.0f}"
    return (f"m {f(r)} 0 b {f(r + k)} 0 {f(2 * r)} {f(r - k)} {f(2 * r)} {f(r)} "
            f"b {f(2 * r)} {f(r + k)} {f(r + k)} {f(2 * r)} {f(r)} {f(2 * r)} "
            f"b {f(r - k)} {f(2 * r)} 0 {f(r + k)} 0 {f(r)} b 0 {f(r - k)} {f(r - k)} 0 {f(r)} 0")


def _arrow_pts(length: float, width: float) -> list[tuple[float, float]]:
    """Arrow pointing right with its tip at (0, 0)."""
    hw, sh, head = width / 2, width * 0.22, width * 0.9
    p = [(0, -sh), (length - head, -sh), (length - head, -hw), (length, 0),
         (length - head, hw), (length - head, sh), (0, sh)]
    return [(x - length, y) for x, y in p]


def _arrow(ang: float, length: float = 230, width: float = 90):
    """Shape (shifted so its box starts at 0,0) and the tip's offset inside it. ang: direction it points, degrees."""
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    p = [(x * c - y * s, x * s + y * c) for x, y in _arrow_pts(length, width)]
    mx, my = min(x for x, _ in p), min(y for _, y in p)
    shape = "m " + " l ".join(f"{x - mx:.0f} {y - my:.0f}" for x, y in p)
    return shape, (-mx, -my)


def _arrow_angle(x: float, y: float, W: int, H: int) -> float:
    """Point at (x, y) from the side with more room (towards the frame centre)."""
    return math.degrees(math.atan2(y - H * 0.45, x - W / 2)) if math.dist((x, y), (W / 2, H * 0.45)) > 120 else 35


def ass_events(marks_out: list[dict], cfg: dict, W: int, H: int) -> list[str]:
    """ASS lines. A mark is {t, x, y, r, kind} (still) or {path: [(t, x, y, r)] in output px} (follows)."""
    ev = []
    col, ocol, th = ass_color(cfg.get("color", "FF2D2D")), ass_color(cfg.get("outline_color", "000000")), cfg.get("thickness", 12)
    pop = cfg.get("pop_ms", 140)
    for m in marks_out:
        path = m.get("path") or [(m["t"], m["x"], m["y"], m["r"]), (m["t"] + cfg.get("show_s", 1.4), m["x"], m["y"], m["r"])]
        kind = m.get("kind") or cfg.get("kind", "both")
        r = max(60, min(cfg.get("max_radius", 220), float(np.median([p[3] for p in path])) * 1.25))
        ang = _arrow_angle(path[0][1], path[0][2], W, H)
        ashape, (tx, ty) = _arrow(ang)
        circ = _ellipse(r)
        n = len(path) - 1
        for i in range(n):
            (t1, x1, y1, _), (t2, x2, y2, _) = path[i], path[i + 1]
            first, last = i == 0, i == n - 1
            dt = round((t2 - t1) * 1000)
            fx = (f"\\fscx60\\fscy60\\t(0,{pop},\\fscx100\\fscy100)\\alpha&HFF&\\t(0,60,\\alpha&H00&)" if first
                  else f"\\t({max(0, dt - 200)},{dt},\\alpha&HFF&)" if last else "")
            mv = f"\\move({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f})"
            if kind in ("circle", "both"):
                base = f"\\an5{mv}\\p1\\1a&HFF&{fx}"
                ev.append(f"Dialogue: 3,{ts(t1)},{ts(t2)},Box,,0,0,0,,{{{base}\\3c{ocol}\\bord{th + 6}}}{circ}")
                ev.append(f"Dialogue: 4,{ts(t1)},{ts(t2)},Box,,0,0,0,,{{{base}\\3c{col}\\bord{th}}}{circ}")
            if kind in ("arrow", "both"):
                gap = r + 14 + th
                c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                p1 = (x1 - gap * c - tx, y1 - gap * s - ty)
                p2 = (x2 - gap * c - tx, y2 - gap * s - ty)
                afx = fx.replace("\\fscx60\\fscy60", "\\fscx70\\fscy70")
                ev.append(f"Dialogue: 4,{ts(t1)},{ts(t2)},Box,,0,0,0,,{{\\an7\\move({p1[0]:.0f},{p1[1]:.0f},{p2[0]:.0f},{p2[1]:.0f})"
                          f"\\p1{afx}\\1c{col}\\3c{ocol}\\bord{th // 2}}}{ashape}")
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
