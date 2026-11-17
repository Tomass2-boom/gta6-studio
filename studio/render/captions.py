"""ASS subtitle writer: 1-2 word pop-in captions and the layout C title box. All look from style.yaml."""
import re
from ..config import ROOT

FONTS = ROOT / "assets" / "fonts"


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = str(hex_rgb).lstrip("#").zfill(6)
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def chunk_words(words: list[dict], cap: dict) -> list[dict]:
    """Group words (clip-relative times) into captions of up to N words / max_chars."""
    keep = set(cap.get("keep_punctuation", "?!"))
    mx = cap.get("max_word_s", 1.0)
    # Whisper stretches a word after a pause back over the silence; the word is really said at its end.
    words = [{**w, "start": max(w["start"], w["end"] - mx)} for w in words]
    chunks, cur = [], []
    for i, w in enumerate(words):
        txt = "".join(ch for ch in w["word"].strip() if ch.isalnum() or ch in "'-" or ch in keep)
        if not txt:
            continue
        item = {**w, "text": txt}
        if cur:
            joined = len(" ".join(x["text"] for x in cur + [item]))
            gap = w["start"] - cur[-1]["end"]
            ends = re.search(r"[.?!,]$", cur[-1]["word"].strip())
            if len(cur) >= cap["words_per_caption"] or joined > cap["max_chars"] or gap > cap["break_gap_s"] or ends:
                chunks.append(cur)
                cur = []
        cur.append(item)
    if cur:
        chunks.append(cur)
    out = []
    for i, c in enumerate(chunks):
        start = max(0.0, c[0]["start"] - cap.get("lead_s", 0.0))
        if out:
            start = max(start, out[-1]["start"] + 0.05)
        end = c[-1]["end"] + cap["hold_s"]
        if i + 1 < len(chunks):
            end = min(end, chunks[i + 1][0]["start"] - cap.get("lead_s", 0.0))
        end = max(end, start + cap["min_show_s"])
        if out and out[-1]["end"] > start:
            out[-1]["end"] = start
        out.append({"start": start, "end": end, "words": c})
    return out


def mask(word: str, roots: list[str]) -> str:
    """Swear words keep their first letter and any ending: FUCKING -> F***ING, SHIT -> S***."""
    low = word.lower()
    for r in sorted(roots, key=len, reverse=True):
        i = low.find(r)
        if i >= 0 and len(r) > 1:
            return word[: i + 1] + "*" * (len(r) - 1) + word[i + len(r):]
    return word


def _rounded_rect(w: float, h: float, r: float) -> str:
    r = min(r, w / 2, h / 2)
    k = r * 0.448  # bezier handle so corners look round
    f = lambda v: f"{v:.0f}"
    return (f"m {f(r)} 0 l {f(w - r)} 0 b {f(w - k)} 0 {f(w)} {f(k)} {f(w)} {f(r)} "
            f"l {f(w)} {f(h - r)} b {f(w)} {f(h - k)} {f(w - k)} {f(h)} {f(w - r)} {f(h)} "
            f"l {f(r)} {f(h)} b {f(k)} {f(h)} 0 {f(h - k)} 0 {f(h - r)} "
            f"l 0 {f(r)} b 0 {f(k)} {f(k)} 0 {f(r)} 0")


def wrap(text: str, font_file: str, size: int, max_w: float) -> tuple[list[str], float]:
    """Wrap text to max_w pixels; returns (lines, widest line width)."""
    from PIL import ImageFont
    font = ImageFont.truetype(str(FONTS / font_file), size)
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and font.getlength(trial) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines, max((font.getlength(x) for x in lines), default=0)


def build_ass(words: list[dict], keywords: list[str], style: dict, layout: str, geo: dict,
              duration: float, title: str | None = None) -> str:
    out, cap = style["output"], style["captions"]
    W, H = out["width"], out["height"]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cap['font']},{cap['size']},{ass_color(cap['color'])},{ass_color(cap['color'])},{ass_color(cap['outline_color'])},{ass_color('000000', 128)},0,0,0,0,100,100,0,0,1,{cap['outline']},{cap['shadow']},5,0,0,0,1
Style: Box,{cap['font']},20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
"""
    events = []
    if layout == "C" and title:
        tb = style["layout_c"]["title_box"]
        text = title.upper() if tb["uppercase"] else title
        txt = tb.get("style", "box") == "text"
        inner = W - 2 * tb["margin_x"] - (0 if txt else 2 * tb["padding_x"])
        lines, widest = wrap(text, cap["font_file"], tb["font_size"], inner)
        while len(lines) > 1:  # balance: narrow the box while the line count stays the same
            l2, w2 = wrap(text, cap["font_file"], tb["font_size"], widest - 20)
            if len(l2) != len(lines):
                break
            lines, widest = l2, w2
        lines = lines[: tb["max_lines"]]
        line_h = tb["font_size"] * 1.15
        bw, bh = widest + 2 * tb["padding_x"], len(lines) * line_h + 2 * tb["padding_y"]
        bx, by = (W - bw) / 2, geo["video_top"] - tb["gap"] - bh
        if tb.get("style", "box") == "text":
            # No box: big outlined text with a drop shadow, lines in alternating colours (common Shorts look).
            cols = [ass_color(c) for c in tb["line_colors"]]
            head += (f"Style: Title,{cap['font']},{tb['font_size']},{cols[0]},{cols[0]},"
                     f"{ass_color(tb['outline_color'])},{ass_color('000000', 90)},0,0,0,0,100,100,0,0,1,"
                     f"{tb['outline']},{tb['shadow']},5,0,0,0,1\n")
            ty = geo["video_top"] - tb["gap"] - len(lines) * line_h / 2
            body = "\\N".join(f"{{\\1c{cols[i % len(cols)]}}}{ln}" for i, ln in enumerate(lines))
            events.append(f"Dialogue: 1,{ts(0)},{ts(duration)},Title,,0,0,0,,"
                          f"{{\\pos({W // 2},{ty:.0f})\\q2\\fscx{tb['pop_from']}\\fscy{tb['pop_from']}"
                          f"\\t(0,{tb['pop_ms']},\\fscx100\\fscy100)}}" + body)
        else:
            head += (f"Style: Title,{cap['font']},{tb['font_size']},{ass_color(tb['text_color'])},"
                     f"{ass_color(tb['text_color'])},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n")
            events.append(f"Dialogue: 0,{ts(0)},{ts(duration)},Box,,0,0,0,,"
                          f"{{\\an7\\pos({bx:.0f},{by:.0f})\\1c{ass_color(tb['color'])}\\bord0\\shad0\\p1}}"
                          f"{_rounded_rect(bw, bh, tb['radius'])}")
            events.append(f"Dialogue: 1,{ts(0)},{ts(duration)},Title,,0,0,0,,"
                          f"{{\\pos({W // 2},{by + bh / 2:.0f})\\q2}}" + "\\N".join(lines))
    never = {_norm(x) for x in cap.get("never_highlight", [])}
    kw = {_norm(p) for k in keywords or [] for p in str(k).split() if _norm(p)} - never
    colors, n_hl = cap["highlight_colors"], 0
    y = geo["caption_y"]
    pop = f"\\fscx{cap['pop_from']}\\fscy{cap['pop_from']}\\t(0,{cap['pop_ms']},\\fscx100\\fscy100)"
    for c in chunk_words(words, cap):
        parts = []
        for w in c["words"]:
            t = w["text"].upper() if cap["uppercase"] else w["text"]
            if cap.get("mask_profanity"):
                t = mask(t, cap.get("mask_roots", []))
            if _norm(w["text"]) in kw:
                parts.append(f"{{\\1c{ass_color(colors[n_hl % len(colors)])}}}{t}{{\\1c{ass_color(cap['color'])}}}")
                n_hl += 1
            else:
                parts.append(t)
        yc = y
        if any(s0 <= c["start"] < e0 for s0, e0 in geo.get("b_ranges", [])):
            yc = geo["caption_y_b"]
        events.append(f"Dialogue: 2,{ts(c['start'])},{ts(c['end'])},Cap,,0,0,0,,"
                      f"{{\\pos({W // 2},{yc:.0f}){pop}}}" + " ".join(parts))
    return head + "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(events) + "\n"
