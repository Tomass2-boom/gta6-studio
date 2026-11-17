import re
import subprocess
import tempfile
from pathlib import Path
from ..config import load


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def drop_loops(words: list[dict], min_n: int = 3, min_repeats: int = 3) -> list[dict]:
    """Whisper sometimes hallucinates the same phrase over and over in music/noise.
    Drop a phrase of >= min_n words that repeats >= min_repeats times in a row (keep the first)."""
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


def transcribe(video: str, start: float, end: float) -> dict:
    """Transcribe [start,end] of a video. Returns {'text', 'segments':[{start,end,text}],
    'words':[{word,start,end,p}]} with times relative to the full video."""
    import mlx_whisper
    cfg = load("settings")["whisper"]
    pad = cfg.get("pad_s", 2.0)  # context so words at the edges are heard whole
    a = max(0.0, start - pad)
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(end + pad - a), "-i", video,
                        "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=cfg["model"], word_timestamps=True,
                                   language=cfg["language"],
                                   condition_on_previous_text=cfg.get("condition_on_previous_text", False),
                                   hallucination_silence_threshold=cfg.get("hallucination_silence_s", 2.0))
    segs = [{"start": round(s["start"] + a, 2), "end": round(s["end"] + a, 2), "text": s["text"].strip()}
            for s in r["segments"] if s["end"] + a > start and s["start"] + a < end]
    words = [{"word": w["word"].strip(), "start": round(w["start"] + a, 2), "end": round(w["end"] + a, 2),
              "p": round(float(w.get("probability", 1.0)), 3)}
             for s in r["segments"] for w in s.get("words", []) if w["word"].strip()]
    words = [w for w in drop_loops(words) if w["end"] > start and w["start"] < end]
    return {"text": " ".join(s["text"] for s in segs), "segments": segs, "words": words}
