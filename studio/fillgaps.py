"""Fill the stretches the live run missed (crash, restart, hang): clip them later as if we had been there.

For every unfilled gap in data/app/gaps.jsonl (the watchdog writes them; add one by hand with --add):
1. Where no chat was saved, download the chat REPLAY from the Twitch VOD (Twitch keeps VOD chat) into
   data/chat/<channel>-<date>-replay.jsonl. Only the holes are fetched, so chat is never counted twice.
   Kick: Kick's chat history API (by channel, paged back by time; ~98% of what live logging saw, checked
   2026-11-19). YouTube: the stream's chat replay via yt-dlp, which exists only once the stream has ENDED;
   a gap that overlaps a still-running YouTube stream stays unfilled so the next run picks it up.
2. Run `studio backfill` over the gap: chat spikes -> fetch only those stretches from the VODs -> judge -> render.
Re-running is safe: backfill skips moments already in the database.
"""
import json
import re
import subprocess
import threading
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


KICK_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/130.0 Safari/537.36")
KICK_EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")
_kick_lock = threading.Lock()
_kick_last = [0.0]


def _kick_get(url: str) -> dict:
    """GET Kick JSON with curl (Kick answers 403 to Python's TLS). One request at a time, paced; Kick briefly
    answers 'Request blocked by security policy' to fast paging, so back off and retry."""
    out = ""
    for attempt in range(10):
        with _kick_lock:
            time.sleep(max(0.0, 0.35 - (time.time() - _kick_last[0])))
            out = subprocess.run(["curl", "-s", "-m", "20", "-A", KICK_UA, url], capture_output=True, text=True).stdout
            _kick_last[0] = time.time()
        try:
            d = json.loads(out)
            if "data" in d or "id" in d:
                return d
        except ValueError:
            pass
        time.sleep(min(10 * (attempt + 1), 60))
    raise RuntimeError(f"Kick: no answer ({out[:120]})")


def kick_replay(slug: str, a: float, b: float):
    """Yield (wall_ts, user, text) for Kick chat between a and b, newest first (paged back by cursor = µs time)."""
    cid = _kick_get(f"https://kick.com/api/v2/channels/{slug}")["id"]
    cur, seen = int(b * 1e6), set()
    while True:
        d = _kick_get(f"https://web.kick.com/api/v1/chat/{cid}/history?cursor={cur}")["data"]
        ms = d.get("messages") or []
        if not ms:
            return
        oldest = b
        for x in ms:
            t = datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")).timestamp()
            oldest = min(oldest, t)
            if x.get("id") in seen or not a <= t <= b:
                continue
            seen.add(x.get("id"))
            text = KICK_EMOTE.sub(r"\1", x.get("content") or "").strip()
            if text:
                yield t, (x.get("sender") or {}).get("username") or "", text
        nc = int(d.get("cursor") or 0)
        if oldest < a or not nc or nc >= cur:
            return
        cur = nc


def youtube_vods(handle: str, lg) -> list[dict]:
    """The channel's last few streams: [{id, t0, t1, status}] (status: was_live / is_live / post_live ...)."""
    r = subprocess.run(["yt-dlp", "--skip-download", "--ignore-errors", "--playlist-end", "4", "--print",
                        "%(id)s %(release_timestamp)s %(duration)s %(live_status)s",
                        f"https://www.youtube.com/@{handle}/streams"], capture_output=True, text=True, timeout=300)
    out = []
    for ln in r.stdout.splitlines():
        p = ln.split()
        if len(p) == 4 and p[1].isdigit():
            t0 = int(p[1])
            dur = float(p[2]) if p[2].replace(".", "").isdigit() else None
            out.append({"id": p[0], "t0": t0, "t1": t0 + dur if dur else time.time(), "status": p[3]})
    if not out:
        lg.warning("%s: could not list YouTube streams (%s)", handle, r.stderr.strip()[-150:])
    return out


def youtube_replay(vid: str):
    """Yield (wall_ts, user, text) from an ended stream's chat replay (downloaded once, cached)."""
    d = DATA / "chat" / "ytreplay"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{vid}.live_chat.json"
    if not f.exists():
        subprocess.run(["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o",
                        str(d / "%(id)s"), f"https://www.youtube.com/watch?v={vid}"],
                       capture_output=True, text=True, timeout=4 * 3600)
    if not f.exists():
        raise RuntimeError("yt-dlp saved no chat replay")
    for ln in f.read_text().splitlines():
        try:
            for a in json.loads(ln).get("replayChatItemAction", {}).get("actions", []):
                r = a.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
                if not r or not r.get("timestampUsec"):
                    continue
                runs = r.get("message", {}).get("runs", [])
                text = "".join(x.get("text") or (x.get("emoji", {}).get("shortcuts") or [""])[0] for x in runs).strip()
                if text:
                    yield int(r["timestampUsec"]) / 1e6, r.get("authorName", {}).get("simpleText", ""), text
        except (ValueError, AttributeError, TypeError):
            continue


def holes(ch: str, platform: str, a: float, b: float) -> list[tuple[float, float]]:
    """Stretches inside [a, b] longer than HOLE_S with no saved chat. Replay files count too, so a re-run
    never fetches (and counts) the same chat twice."""
    ts = [a]
    for f in B.chat_files(ch, platform):
        for ln in f.read_text().splitlines():
            try:
                t = json.loads(ln)["t"]
            except (ValueError, KeyError):
                continue
            if a < t < b:
                ts.append(t)
    ts = sorted(ts) + [b]
    return [(x, y) for x, y in zip(ts, ts[1:]) if y - x > HOLE_S]


def recover_chat(a: float, b: float, lg) -> tuple[int, bool]:
    """Fetch replay chat for every hole in [a, b]. Returns (messages saved, pending): pending is True when a hole
    overlaps a YouTube stream whose chat replay is not out yet (stream still running)."""
    from concurrent.futures import ThreadPoolExecutor
    from .live import resolve_channels
    by = B.streams()
    jobs = [(e["channel"], e["platform"]) for e in resolve_channels(None, lg)
            if e["platform"] in ("twitch", "kick", "youtube")]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: _recover_one(j[0], j[1], by, a, b, lg), jobs))
    return sum(n for n, _ in res), any(p for _, p in res)


def _write(ch: str, platform: str, x: float, y: float, rows, lg) -> int:
    pre = {"kick": f"kick-{ch}", "youtube": f"yt-{ch}"}.get(platform, ch)
    out = DATA / "chat" / f"{pre}-{datetime.fromtimestamp(x):%Y-%m-%d}-replay.jsonl"
    n = 0
    try:
        with open(out, "a") as f:
            for t, u, m in rows:
                if x <= t <= y:
                    f.write(json.dumps({"t": round(t, 2), "u": u, "m": m}) + "\n")
                    n += 1
    except Exception as ex:
        lg.warning("%s (%s): replay chat failed after %d messages (%s)", ch, platform, n, ex)
    lg.info("%s (%s): recovered %d chat messages %s-%s", ch, platform, n,
            f"{datetime.fromtimestamp(x):%H:%M}", f"{datetime.fromtimestamp(y):%H:%M}")
    return n


def _recover_one(ch: str, platform: str, by: dict, a: float, b: float, lg) -> tuple[int, bool]:
    hs = holes(ch, platform, a, b)
    if not hs:
        return 0, False
    total, pending = 0, False
    if platform == "kick":
        for x, y in hs:
            total += _write(ch, platform, x, y, kick_replay(ch, x, y), lg)
        return total, False
    if platform == "youtube":
        try:
            vods = youtube_vods(ch, lg)
        except Exception as ex:
            lg.warning("%s: YouTube stream list failed (%s)", ch, ex)
            return 0, True
        for x, y in hs:
            for v in vods:
                if v["t1"] < x or v["t0"] > y:
                    continue
                if v["status"] != "was_live":
                    lg.info("%s: YouTube stream %s is %s; its chat replay comes after it ends, gap kept for later",
                            ch, v["id"], v["status"])
                    pending = True
                    continue
                total += _write(ch, platform, max(x, v["t0"]), min(y, v["t1"]), youtube_replay(v["id"]), lg)
        return total, pending
    for x, y in hs:   # twitch
        ss = [s for s in by.get(ch, []) if s["t0"] <= y and s.get("vod")]
        if not ss:
            continue
        s = max(ss, key=lambda s: s["t0"])
        x = max(x, s["t0"])
        vod = s["vod"].rstrip("/").rsplit("/", 1)[1]
        total += _write(ch, platform, x, y, twitch_replay(vod, x - s["t0"], y - s["t0"]), lg)
    return total, False


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
        _, pending = recover_chat(a, b, lg)
        B.run(iso(a), iso(b), None, None, False, model, lg)
        if pending:
            lg.info("gap %s: clipped what is in; a YouTube stream is still running, run fill-gaps again after it ends",
                    g.get("local"))
            continue
        gs = load_gaps()   # the watchdog may have appended meanwhile
        for x in gs:
            if x["start"] == g["start"] and x["end"] == g["end"]:
                x["filled"] = True
        save_gaps(gs)
        lg.info("gap %s done", g.get("local"))
