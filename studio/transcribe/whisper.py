import subprocess
import tempfile
from pathlib import Path
from ..config import load


def transcribe(video: str, start: float, end: float) -> dict:
    """Transcribe [start,end] of a video. Returns {'text', 'segments':[{start,end,text}],
    'words':[{word,start,end}]} with times relative to the full video."""
    import mlx_whisper
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(end - start), "-i", video,
                        "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=load("settings")["whisper"]["model"],
                                   word_timestamps=True, language=load("settings")["whisper"]["language"])
    segs = [{"start": round(s["start"] + start, 2), "end": round(s["end"] + start, 2),
             "text": s["text"].strip()} for s in r["segments"]]
    words = [{"word": w["word"].strip(), "start": round(w["start"] + start, 2), "end": round(w["end"] + start, 2)}
             for s in r["segments"] for w in s.get("words", []) if w["word"].strip()]
    return {"text": " ".join(s["text"] for s in segs), "segments": segs, "words": words}
