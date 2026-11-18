"""Fill the stretches the live run missed (crash, restart, hang): clip them later as if we had been there.

For every unfilled gap in data/app/gaps.jsonl (the watchdog writes them; add one by hand with --add):
1. Where no chat was saved, download the chat REPLAY from the Twitch VOD (Twitch keeps VOD chat) into
   data/chat/<channel>-<date>-replay.jsonl. Only the holes are fetched, so chat is never counted twice.
   (YouTube replay chat only exists after the stream ends; Kick has no replay chat: those stay chat-less.)
2. Run `studio backfill` over the gap: chat spikes -> fetch only those stretches from the VODs -> judge -> render.
Re-running is safe: backfill skips moments already in the database.
"""
import json
import subprocess
import time
from datetime import datetime, timezone

from .config import DATA
from . import backfill as B

GQL = "https://gql.twitch.tv/gql"
CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
HOLE_S = 300
GAPS = DATA / "app" / "gaps.jsonl"


def _gql(variables: dict) -> dict:
    body = json.dumps([{"operationName": "VideoCommentsByOffsetOrCursor", "variables": variables,
                        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": HASH}}}])
    for attempt in range(4):
        r = subprocess.run(["curl", "-s", "-m", "20", "-H", f"Client-ID: {CID}", GQL, "-d", body],
                           capture_output=True, text=True)
        try:
            c = json.loads(r.stdout)[0]["data"]["video"]["comments"]
            if c is not None:
                return c
            time.sleep(2 + 3 * attempt)
        except Exception:
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"Twitch replay chat: no answer ({r.stdout[:150]})")


def twitch_replay(vod_id: str, off_a: float, off_b: float):
    """Yield (wall_ts, user, text) for VOD comments between offsets off_a and off_b (seconds).
    Paged by offset (cursor paging needs a browser integrity token): each page starts at or before the asked
    offset and covers ~50 messages, so asking again at the last offset seen leaves no holes; ids dedupe."""
    seen, o = set(), max(0, int(off_a))
    while o <= off_b:
        edges = _gql({"videoID": vod_id, "contentOffsetSeconds": o}).get("edges") or []
        last = o
        for e in edges:
            n = e["node"]
            last = max(last, n["contentOffsetSeconds"])
            if n["id"] in seen or not off_a <= n["contentOffsetSeconds"] <= off_b:
                continue
            seen.add(n["id"])
            text = "".join(f.get("text") or "" for f in (n.get("message") or {}).get("fragments") or []).strip()
            if text:
                t = datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")).timestamp()
                yield t, ((n.get("commenter") or {}).get("login") or ""), text
        o = last if last > o else o + (1 if edges else 30)


def holes(ch: str, platform: str, a: float, b: float) -> list[tuple[float, float]]:
    """Stretches inside [a, b] longer than HOLE_S with no saved chat (replay files excluded)."""
    ts = [a]
    for f in B.chat_files(ch, platform):
        if f.name.endswith("-replay.jsonl"):
            continue
        for ln in f.read_text().splitlines():
            try:
                t = json.loads(ln)["t"]
            except (ValueError, KeyError):
                continue
            if a < t < b:
                ts.append(t)
    ts = sorted(ts) + [b]
    return [(x, y) for x, y in zip(ts, ts[1:]) if y - x > HOLE_S]


def recover_chat(a: float, b: float, lg) -> int:
    from concurrent.futures import ThreadPoolExecutor
    from .live import resolve_channels
    by = B.streams()
    chans = [e["channel"] for e in resolve_channels(None, lg) if e["platform"] == "twitch"]
    with ThreadPoolExecutor(4) as ex:
        return sum(ex.map(lambda ch: _recover_one(ch, by, a, b, lg), chans))


def _recover_one(ch: str, by: dict, a: float, b: float, lg) -> int:
    total = 0
    for x, y in holes(ch, "twitch", a, b):
        ss = [s for s in by.get(ch, []) if s["t0"] <= y and s.get("vod")]
        if not ss:
            continue
        s = max(ss, key=lambda s: s["t0"])
        x = max(x, s["t0"])
        vod = s["vod"].rstrip("/").rsplit("/", 1)[1]
        out = DATA / "chat" / f"{ch}-{datetime.fromtimestamp(x):%Y-%m-%d}-replay.jsonl"
        n = 0
        try:
            with open(out, "a") as f:
                for t, u, m in twitch_replay(vod, x - s["t0"], y - s["t0"]):
                    if x <= t <= y:
                        f.write(json.dumps({"t": round(t, 2), "u": u, "m": m}) + "\n")
                        n += 1
        except Exception as ex:
            lg.warning("%s: replay chat failed (%s)", ch, ex)
        lg.info("%s: recovered %d chat messages %s-%s from the VOD replay", ch, n,
                f"{datetime.fromtimestamp(x):%H:%M}", f"{datetime.fromtimestamp(y):%H:%M}")
        total += n
    return total


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_gaps() -> list[dict]:
    return [json.loads(l) for l in GAPS.read_text().splitlines() if l.strip()] if GAPS.exists() else []


def save_gaps(gs: list[dict]):
    GAPS.write_text("".join(json.dumps(g) + "\n" for g in gs))


def add(a: float, b: float):
    GAPS.parent.mkdir(parents=True, exist_ok=True)
    gs = load_gaps()
    gs.append({"start": a, "end": b, "filled": False,
               "local": f"{datetime.fromtimestamp(a):%H:%M}-{datetime.fromtimestamp(b):%H:%M}"})
    save_gaps(gs)


def run(lg, model: str | None = None):
    for g in load_gaps():
        if g.get("filled"):
            continue
        a, b = g["start"] - 60, g["end"] + 60
        lg.info("filling gap %s (local time)", g.get("local"))
        recover_chat(a, b, lg)
        B.run(iso(a), iso(b), None, None, False, model, lg)
        gs = load_gaps()   # the watchdog may have appended meanwhile
        for x in gs:
            if x["start"] == g["start"] and x["end"] == g["end"]:
                x["filled"] = True
        save_gaps(gs)
        lg.info("gap %s done", g.get("local"))
