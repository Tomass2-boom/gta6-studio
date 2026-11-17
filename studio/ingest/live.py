"""Live video: Tier A continuous recording in wall-clock-named chunks, Tier B rewind fetch, cutting, cleanup.

Tier A: `streamlink -O | ffmpeg -f segment` writes data/raw/live/<chan>/<epoch>.ts every ~10 s. Names are the
wall-clock start, so a chat spike maps straight to chunks, and Twitch ad gaps show up as missing time.
Tier B: on a spike, `streamlink --hls-live-restart` takes Twitch's rewind (~30-50 s, measured 2026-11-17)
and keeps recording forward for the rest of the window.
"""
import subprocess
import time
from pathlib import Path

from ..config import DATA
from ..render.layout import tools

RAW = DATA / "raw" / "live"
SEG = DATA / "segments"


def stream_url(platform: str, channel: str) -> str:
    return {"twitch": f"https://twitch.tv/{channel}", "kick": f"https://kick.com/{channel}",
            "youtube": f"https://youtube.com/@{channel}/live"}[platform]


def start_recording(platform: str, channel: str, chunk_s: int) -> subprocess.Popen:
    """Tier A. Exits when the stream ends or is offline; the caller restarts it."""
    d = RAW / channel
    d.mkdir(parents=True, exist_ok=True)
    ff = tools()[0]
    cmd = (f'streamlink -O --twitch-low-latency "{stream_url(platform, channel)}" best 2>/dev/null | '
           f'"{ff}" -v error -i - -c copy -f segment -segment_time {chunk_s} -strftime 1 '
           f'-reset_timestamps 1 "{d}/%s.ts"')
    return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def chunks(channel: str, t0: float, t1: float, chunk_s: int) -> list[tuple[float, Path]]:
    """Chunks whose time range touches [t0, t1], sorted. The newest chunk is still being written: skip it."""
    d = RAW / channel
    allc = sorted((float(p.stem), p) for p in d.glob("*.ts") if p.stem.isdigit()) if d.exists() else []
    allc = allc[:-1] if allc and time.time() - allc[-1][0] < chunk_s * 1.5 else allc
    return [(t, p) for t, p in allc if t + chunk_s >= t0 and t <= t1]


def contiguous_run(starts: list[float], around: float, chunk_s: int, max_gap: float = 3.0) -> tuple[int, int]:
    """Indexes [i, j) of the run of chunks without a gap (ad break, reconnect) that contains/is nearest `around`.
    A chunk is expected every chunk_s; a hole bigger than max_gap seconds ends the run."""
    if not starts:
        return 0, 0
    runs, s = [], 0
    for k in range(1, len(starts) + 1):
        if k == len(starts) or starts[k] - starts[k - 1] > chunk_s + max_gap:
            runs.append((s, k))
            s = k

    def dist(r):
        a, b = starts[r[0]], starts[r[1] - 1] + chunk_s
        return 0 if a <= around <= b else min(abs(a - around), abs(b - around))
    return min(runs, key=lambda r: (dist(r), -(r[1] - r[0])))


def cut_from_recording(channel: str, t0: float, t1: float, spike_t: float, out: Path, chunk_s: int, min_len: float):
    """Tier A: join the chunks for the window into one file. Returns (path, base_epoch, duration) or raises."""
    cs = chunks(channel, t0, t1, chunk_s)
    if not cs:
        raise RuntimeError("no recorded chunks for this window (offline, or recorder not running)")
    i, j = contiguous_run([t for t, _ in cs], spike_t, chunk_s)
    cs = cs[i:j]
    if len(cs) * chunk_s < min_len:
        raise RuntimeError(f"only {len(cs) * chunk_s:.0f}s of unbroken video around the spike (ad break?)")
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p}'\n" for _, p in cs))
    subprocess.run([tools()[0], "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(out)], check=True)
    lst.unlink()
    return out, cs[0][0], duration(out)


def fetch_rewind(platform: str, channel: str, seconds: int, out: Path, min_len: float):
    """Tier B: Twitch rewind + keep recording until `seconds` of stream. Blocks ~ (seconds - rewind) s."""
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw.ts")
    started = time.time()
    subprocess.run(["streamlink", "--hls-live-restart", "--hls-duration", str(seconds), "--twitch-low-latency",
                    "-o", str(raw), "-f", stream_url(platform, channel), "best"],
                   check=True, capture_output=True, timeout=seconds + 120)
    # remux with timestamps from zero: ffprobe on the raw .ts reports a wrong duration (measured)
    subprocess.run([tools()[0], "-v", "error", "-y", "-i", str(raw), "-c", "copy", "-avoid_negative_ts",
                    "make_zero", str(out)], check=True)
    raw.unlink()
    d = duration(out)
    if d < min_len:
        raise RuntimeError(f"rewind fetch gave only {d:.0f}s (offline or ad break)")
    return out, started, d


def duration(path: Path) -> float:
    r = subprocess.run([tools()[1], "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def cleanup(keep_h: float):
    """Raw recordings and segments are deleted after keep_h hours. Finished clips are kept."""
    cutoff = time.time() - keep_h * 3600
    n = 0
    for root in (RAW, SEG):
        for p in root.rglob("*") if root.exists() else []:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
    return n
