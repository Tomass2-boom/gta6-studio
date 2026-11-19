import re
import subprocess
import tempfile
from pathlib import Path
from ..config import load


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def drop_loops(words: list[dict], min_n: int = 3, min_repeats: int = 5) -> list[dict]:
    """Whisper sometimes hallucinates the same phrase over and over in music/noise.
    Drop a phrase of >= min_n words that repeats >= min_repeats times in a row (keep the first).
    Real speech repeats 3-4 times ("he just fell, he just fell"), so the bar is 5."""
    w = list(words)
    changed = True
    while changed:
        changed = False
        for n in range(8, min_n - 1, -1):
            i = 0
            while i + n * min_repeats <= len(w):
                key = [_norm(x["word"]) for x in w[i:i + n]]
                reps = 1
                while w[i + reps * n:i + (reps + 1) * n] and [_norm(x["word"]) for x in w[i + reps * n:i + (reps + 1) * n]] == key:
                    reps += 1
                if reps >= min_repeats:
                    del w[i + n:i + reps * n]
                    changed = True
                i += 1
    return w


def _once(wav: str, cfg: dict, a: float) -> tuple[list, list, bool]:
    import mlx_whisper
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=cfg["model"], word_timestamps=True, language=cfg["language"],
                               condition_on_previous_text=cfg.get("condition_on_previous_text", False),
                               hallucination_silence_threshold=cfg.get("hallucination_silence_s"),
                               initial_prompt=cfg.get("initial_prompt"))
    mx = cfg.get("max_word_chars", 10)  # "AHHHHHHHH..." can come out hundreds of letters long
    segs = [{"start": round(s["start"] + a, 2), "end": round(s["end"] + a, 2), "text": s["text"].strip()}
            for s in r["segments"]]
    words = [{"word": w["word"].strip()[:mx], "start": round(w["start"] + a, 2), "end": round(w["end"] + a, 2),
              "p": round(float(w.get("probability", 1.0)), 3)}
             for s in r["segments"] for w in s.get("words", []) if w["word"].strip()]
    fell_back = any(s.get("temperature", 0) > 0 for s in r["segments"])
    return segs, drop_loops(words), fell_back


def transcribe(video: str, start: float, end: float) -> dict:
    """Transcribe [start,end] of a video. Returns {'text', 'segments':[{start,end,text}],
    'words':[{word,start,end,p}]} with times relative to the full video.
    Screams and noise make Whisper fall back to random sampling, which sometimes skips whole
    stretches; then it runs again (up to `retries`) and keeps the run with the most words."""
    cfg = load("settings")["whisper"]
    pad = cfg.get("pad_s", 2.0)  # context so words at the edges are heard whole
    a = max(0.0, start - pad)
    with tempfile.TemporaryDirectory() as d:
        wav = str(Path(d) / "a.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(end + pad - a), "-i", video,
                        "-vn", "-ac", "1", "-ar", "16000", wav], check=True, timeout=300)
        best = _once(wav, cfg, a)
        for _ in range(cfg.get("retries", 2)):
            if not best[2]:
                break
            nxt = _once(wav, cfg, a)
            if len(nxt[1]) > len(best[1]):
                best = nxt
    segs = [s for s in best[0] if s["end"] > start and s["start"] < end]
    words = [w for w in best[1] if w["end"] > start and w["start"] < end]
    return {"text": " ".join(s["text"] for s in segs), "segments": segs, "words": words}


def fill_missing(words: list[dict], segments: list[dict], a: float, b: float) -> list[dict]:
    """Captions must never vanish (clip 528: Whisper skipped most of the first 30 s of a window). Where a transcript
    segment inside [a, b] has fewer than 60% of its words timed, spread the segment's own words evenly over it."""
    out = list(words)
    for seg in segments:
        if seg["end"] <= a or seg["start"] >= b:
            continue
        toks = seg["text"].split()
        if not toks:
            continue
        inside = [w for w in out if w["start"] >= seg["start"] - 0.3 and w["end"] <= seg["end"] + 0.3]
        if len(inside) >= 0.6 * len(toks):
            continue
        out = [w for w in out if w not in inside]
        step = (seg["end"] - seg["start"]) / len(toks)
        out += [{"word": t, "start": round(seg["start"] + i * step, 2),
                 "end": round(seg["start"] + (i + 1) * step - 0.03, 2), "p": 0.5} for i, t in enumerate(toks)]
    return sorted(out, key=lambda w: w["start"])
