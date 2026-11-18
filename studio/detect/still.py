"""Dead-picture check: share of a stretch where the video does not move (a capture card's 'No input signal',
a BRB/starting-soon card, a frozen stream). ffmpeg freezedetect on a small, low-fps copy, so it is cheap."""
import re
import subprocess

from ..ingest.live import tools


def frozen_share(path: str, a: float, b: float, noise: float = 0.003, min_s: float = 2.0) -> float:
    """0..1: how much of [a, b] is frozen (freezes of min_s or longer)."""
    dur = max(b - a, 0.01)
    r = subprocess.run([tools()[0], "-hide_banner", "-nostats", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", path,
                        "-an", "-vf", f"fps=4,scale=320:-2,freezedetect=n={noise}:d={min_s}", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=300)
    frozen, start = 0.0, None
    for line in r.stderr.splitlines():
        if m := re.search(r"freeze_start: ([\d.]+)", line):
            start = float(m.group(1))
        elif (m := re.search(r"freeze_end: ([\d.]+)", line)) and start is not None:
            frozen += float(m.group(1)) - start
            start = None
    if start is not None:          # still frozen at the end
        frozen += dur - start
    return min(frozen / dur, 1.0)
