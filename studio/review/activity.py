"""Plain-English view of what `studio live` is doing: who is being recorded, and the recent log."""
import re
import time
from pathlib import Path

from ..config import DATA, load

RECORDING_FRESH_S = 90  # Kick names chunks in bursts (lag up to 30 s), so allow a margin


def newest_log(stage: str = "live") -> Path | None:
    """The newest data/logs/*-<stage>.log. A run past midnight keeps writing yesterday's file."""
    logs = sorted((p for p in (DATA / "logs").glob(f"*-{stage}.log") if "-app-" not in p.name),
                  key=lambda p: p.stat().st_mtime)  # not our own stdout capture (*-app-live.log)
    return logs[-1] if logs else None


def tail(path: Path | None, n: int = 400) -> list[str]:
    if not path or not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.seek(max(0, f.tell() - 200_000))
        data = f.read().decode("utf-8", "replace")
    lines = data.splitlines()
    if size:
        lines = lines[1:]  # first line may be cut
    return lines[-n:]


_RULES = [
    (r"^SPIKE\(chat\) (\S+) x([\d.]+)", lambda m: ("hype", f"Chat went wild for {m[1]} ({float(m[2]):.0f}× normal)")),
    (r"^SPIKE\(loud\) (\S+) x([\d.]+)", lambda m: ("hype", f"{m[1]} got really loud ({float(m[2]):.0f}× normal)")),
    (r"^moment \d+: (\S+) cut .*queued", lambda m: ("work", f"Cut a moment from {m[1]}, the AI is checking it…")),
    (r"^moment \d+ dropped: (.*)", lambda m: ("skip", f"Skipped a moment: {m[1]}")),
    (r"^moment \d+ kept \((\d+)\) (.*)", lambda m: ("good", f"Kept “{m[2]}” (AI score {m[1]}/10)")),
    (r"^moment \d+ CLIP READY \((\S+)\)", lambda m: ("ready", f"New clip ready from {m[1]}! See the Clips tab")),
    (r"^watching (.*)", lambda m: ("start", f"Started watching {len(m[1].split(', '))} streamers")),
    (r"^deleted (\d+) old raw files", lambda m: ("info", f"Cleaned up {m[1]} old recordings")),
    (r"^(\S+) is on the blocklist", lambda m: ("info", f"{m[1]} is on the blocklist, not watched")),
    (r"^(\S+): no kick_chatroom", lambda m: ("info", f"{m[1]}: chat not found, using loudness only")),
    (r"^(\S+) cut failed", lambda m: ("error", f"Problem cutting a moment from {m[1]} (details in the log file)")),
    (r"^moment (\d+) render failed", lambda m: ("error", f"Problem making clip {m[1]} (details in the log file)")),
    (r"(?i)(error|failed|exception)", lambda m: ("error", None)),
]
_SKIP = re.compile(r"^(moment \d+ (no clip|rendered)|\S+ recorder stopped|Traceback|\s|\S+ has live: false|\S+ \(\S+\): no rewind)")


def plain(line: str) -> tuple[str, str] | None:
    """One log line -> (kind, English sentence), or None for noise."""
    line = line.rstrip()
    if not line or _SKIP.match(line):
        return None
    for pat, fn in _RULES:
        m = re.search(pat, line)
        if m:
            kind, text = fn(m)
            return kind, text or ("Problem: " + line[:200])
    return None


def activity(n: int = 60) -> list[dict]:
    """Newest first."""
    out, last = [], None
    for line in tail(newest_log("live")):
        r = plain(line)
        if r and r != last:
            out.append({"kind": r[0], "text": r[1]})
            last = r
    return out[::-1][:n]


def _watching() -> dict[str, str]:
    """name -> tier from the newest 'watching ...' line of the live log."""
    for line in reversed(tail(newest_log("live"), 4000)):
        if line.startswith("watching "):
            return {m[1]: m[2] for m in re.finditer(r"([^,]+?)\(tier (\w)\)", line[9:])}
    return {}


def streamers(running: bool, now: float | None = None) -> list[dict]:
    """Each streamer from streamers.yaml with a plain status."""
    now = now or time.time()
    s = load("streamers")
    block = {str(x).lower() for x in s.get("blocklist") or []}
    watching = {k.strip().lower(): v for k, v in _watching().items()} if running else {}
    out = []
    for e in s.get("streamers") or []:
        name = e.get("name", "?")
        ch = (e.get("channel") or (e.get("url") or "").rstrip("/").split("/")[-1] or name).lower()
        platform = e.get("platform", "twitch")
        tier = str(e.get("tier", "B")).upper()
        if platform != "twitch":
            tier = "A"  # live.py records non-Twitch Tier B continuously
        d = DATA / "raw" / "live" / ch
        newest = 0
        if d.exists():
            for p in d.glob("*.ts"):
                try:  # a segment can be deleted between glob and stat
                    newest = max(newest, p.stat().st_mtime)
                except FileNotFoundError:
                    pass
        recording = running and now - newest < RECORDING_FRESH_S
        if name.lower() in block or ch in block:
            st, kind = "Blocked: never clipped", "off"
        elif e.get("live") is False:
            st, kind = "Switched off", "off"
        elif not running:
            st, kind = "Will be watched when you press Start", "idle"
        elif recording:
            st, kind = "LIVE: recording now", "live"
        elif tier == "A":
            st, kind = "Offline (checks again every minute)", "idle"
        else:
            st, kind = "Watching chat; records when chat goes wild", "watch"
        out.append({"name": name, "platform": platform, "status": st, "kind": kind,
                    "watched": name.lower() in watching or ch in watching})
    order = {"live": 0, "watch": 1, "idle": 2, "off": 3}
    return sorted(out, key=lambda x: order[x["kind"]])
