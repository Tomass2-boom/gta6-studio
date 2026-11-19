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


def start_recording(platform: str, channel: str, chunk_s: int, quality: str = "best") -> subprocess.Popen:
    """Tier A. Exits when the stream ends or is offline; the caller restarts it."""
    d = RAW / channel
    d.mkdir(parents=True, exist_ok=True)
    ff = tools()[0]
    low = "--twitch-low-latency " if platform == "twitch" else ""
    cmd = (f'streamlink -O {low}"{stream_url(platform, channel)}" "{quality}" 2>/dev/null | '
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


def contiguous_run(starts: list[float], around: float, chunk_s: int, max_gap: float = 3.0,
                   lengths: list[float] | None = None) -> tuple[int, int]:
    """Indexes [i, j) of the run of chunks without a gap (ad break, reconnect) that contains/is nearest `around`.
    Without `lengths`: a chunk is expected every chunk_s; a hole bigger than max_gap seconds ends the run.
    With `lengths` (real seconds of each chunk): Kick delivers video in bursts, so a chunk's wall-clock name can lag
    its content by up to ~30 s and then catch up (measured 2026-11-17: spacing 2-28 s, 799 s of content in 798 s of
    wall time). lag = name - content timeline. A stall recovers; a real hole shifts every later chunk for good, so a
    hole is where the lowest lag after it is more than max_gap above the lowest lag before it."""
    if not starts:
        return 0, 0
    runs, s = [], 0
    if lengths is None:
        for k in range(1, len(starts) + 1):
            if k == len(starts) or starts[k] - starts[k - 1] > chunk_s + max_gap:
                runs.append((s, k))
                s = k
    else:
        cum, lag = 0.0, []
        for k in range(len(starts)):
            lag.append(starts[k] - starts[0] - cum)
            cum += lengths[k]
        for k in range(1, len(starts) + 1):
            if k == len(starts) or min(lag[k:]) - min(lag[s:k]) > max_gap:
                runs.append((s, k))
                s = k

    def dist(r):
        a, b = starts[r[0]], starts[r[1] - 1] + chunk_s
        return 0 if a <= around <= b else min(abs(a - around), abs(b - around))
    return min(runs, key=lambda r: (dist(r), -(r[1] - r[0])))


def chunk_len(path: Path) -> float:
    """Real seconds of one chunk from its audio packets (format duration is wrong on .ts, measured)."""
    r = subprocess.run([tools()[1], "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "packet=pts_time,duration_time", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    rows = [x.strip(",").split(",") for x in r.stdout.split() if "N/A" not in x and x.strip(",").count(",") == 1]
    if not rows:
        return 0.0
    return float(rows[-1][0]) + float(rows[-1][1]) - float(rows[0][0])


def cut_from_recording(channel: str, t0: float, t1: float, spike_t: float, out: Path, chunk_s: int, min_len: float,
                       hole_s: float | None = None):
    """Tier A: join the chunks for the window into one file. Returns (path, base_epoch, duration) or raises.
    hole_s: measure each chunk and allow name lag up to this (Kick, see contiguous_run); None = fixed chunk_s."""
    cs = chunks(channel, t0, t1, chunk_s)
    if not cs:
        raise RuntimeError("no recorded chunks for this window (offline, or recorder not running)")
    if hole_s is None:
        i, j = contiguous_run([t for t, _ in cs], spike_t, chunk_s)
    else:
        i, j = contiguous_run([t for t, _ in cs], spike_t, chunk_s, hole_s, [chunk_len(p) for _, p in cs])
    cs = cs[i:j]
    if (len(cs) * chunk_s if hole_s is None else sum(chunk_len(p) for _, p in cs)) < min_len:
        got = len(cs) * chunk_s if hole_s is None else sum(chunk_len(p) for _, p in cs)
        raise RuntimeError(f"only {got:.0f}s of unbroken video around the spike (ad break, or the internet is too "
                           f"slow for the recordings: lower live.quality)")
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p}'\n" for _, p in cs))
    subprocess.run([tools()[0], "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(out)], check=True)
    lst.unlink()
    return out, cs[0][0], duration(out)


def fetch_rewind(platform: str, channel: str, seconds: int, out: Path, min_len: float, quality: str = "best"):
    """Tier B: Twitch rewind + keep recording until `seconds` of stream. Blocks ~ (seconds - rewind) s."""
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw.ts")
    started = time.time()
    try:
        subprocess.run(["streamlink", "--hls-live-restart", "--hls-duration", str(seconds), *(["--twitch-low-latency"] if platform == "twitch" else []),
                        "-o", str(raw), "-f", stream_url(platform, channel), quality],
                       check=True, capture_output=True, text=True, timeout=seconds + 120)
    except subprocess.CalledProcessError as e:  # keep streamlink's own reason (it was lost: "non-zero exit status")
        raw.unlink(missing_ok=True)
        tail = " ".join((e.stdout or "").split()[-25:] + (e.stderr or "").split()[-25:])
        raise RuntimeError(f"rewind failed: {tail[-300:] or f'streamlink exit {e.returncode}'}") from None
    except subprocess.TimeoutExpired:
        raw.unlink(missing_ok=True)
        raise RuntimeError(f"rewind timed out after {seconds + 120}s (slow internet or stream stalled)") from None
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
    try:
        return float(r.stdout.strip() or 0)
    except ValueError:
        # 'N/A': a yt-dlp cut of a live YouTube stream has no duration header (Jelly, 2026-11-19); measure packets
        return chunk_len(path)


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
