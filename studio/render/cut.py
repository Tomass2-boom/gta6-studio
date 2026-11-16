import subprocess
from pathlib import Path
from ..config import DATA


def plain_cut(video: str, start: float, end: float, name: str) -> str:
    """M1: plain horizontal cut (re-encoded for exact frames). The vertical look is S2."""
    out = DATA / "clips"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-to", str(end), "-i", video,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac", str(path)], check=True)
    return str(path)
