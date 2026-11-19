"""Is the streamer actually playing GTA 6? (user 2026-11-19: "make sure they are actually live streaming gta6
not some other games").

Live: ask the platform what the stream is set to right now (cached a few minutes).
  Twitch: the stream's category (GQL). Kick: the livestream's category. YouTube: the stream title.
Backfill: the category vodwatch saved in data/vods.jsonl nearest to the moment (it notes it every 10 min).
Answers True / False, or None when the platform did not say (then settings `live.gta6_only.unknown` decides).
"""
import calendar
import json
import re
import subprocess
import time

from ..config import DATA, load

GTA6 = re.compile(r"grand\s*theft\s*auto\s*(vi|6)\b|\bgta\s*-?\s*(vi|6)\b", re.I)
GQL = "https://gql.twitch.tv/gql"
CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_cache: dict[tuple, tuple[float, str | None]] = {}


def cfg() -> dict:
    return load("settings")["live"].get("gta6_only") or {}


def matches(name: str | None, platform: str = "twitch") -> bool | None:
    if not name:
        return None
    if GTA6.search(name):
        return True
    # YouTube has only a free-text title ("LIVE!!") that often names no game: not knowing is not "another game"
    return None if platform == "youtube" else False


def _curl(url, data=None, headers=()):
    cmd = ["curl", "-s", "-m", "15", "-A", "Mozilla/5.0", *sum((["-H", h] for h in headers), []), url]
    if data:
        cmd += ["-d", json.dumps(data)]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout or "null")


def live_game(platform: str, channel: str) -> str | None:
    """Category (Twitch/Kick) or title (YouTube) of the live stream now; None if offline or unknown."""
    key, now = (platform, channel.lower()), time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < cfg().get("cache_s", 180):
        return hit[1]
    name = None
    try:
        if platform == "twitch":
            q = 'query{user(login:%s){stream{game{name} title}}}' % json.dumps(channel.lower())
            s = ((_curl(GQL, {"query": q}, [f"Client-ID: {CID}"]) or {}).get("data") or {}).get("user") or {}
            st = s.get("stream") or {}
            name = (st.get("game") or {}).get("name")
        elif platform == "kick":
            ls = (_curl(f"https://kick.com/api/v2/channels/{channel}") or {}).get("livestream") or {}
            cats = [c.get("name") for c in ls.get("categories") or [] if c.get("name")]
            name = cats[0] if cats else None
        elif platform == "youtube":
            r = subprocess.run(["yt-dlp", "--print", "%(title)s", "--skip-download",
                                f"https://youtube.com/@{channel}/live"], capture_output=True, text=True, timeout=90)
            name = r.stdout.strip() or None
    except Exception:
        name = None
    _cache[key] = (now, name)
    return name


def is_gta6_live(platform: str, channel: str) -> tuple[bool, str | None]:
    """(clip it?, what the stream is set to)."""
    c = cfg()
    if not c.get("enabled", True):
        return True, None
    name = live_game(platform, channel)
    ok = matches(name, platform)
    return (c.get("unknown", "allow") == "allow") if ok is None else ok, name


def game_at(platform: str, channel: str, t: float, window_s: float = 1200) -> str | None:
    """What vodwatch saw the stream set to, nearest to wall time t (within window_s)."""
    p = DATA / "vods.jsonl"
    best, gap = None, window_s
    for ln in (p.read_text().splitlines() if p.exists() else []):
        if channel.lower() not in ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("platform") != platform or str(r.get("channel", "")).lower() != channel.lower() or not r.get("game"):
            continue
        seen = calendar.timegm(time.strptime(r["seen"], "%Y-%m-%dT%H:%M:%SZ"))
        if abs(seen - t) <= gap:
            best, gap = r["game"], abs(seen - t)
    return best


def kick_vod_game(channel: str, t: float) -> str | None:
    """Kick: the category of the saved stream (VOD) that covers wall time t."""
    from datetime import datetime, timezone
    key = ("kickvods", channel.lower())
    hit = _cache.get(key)
    if not hit or time.time() - hit[0] > 600:
        try:
            hit = (time.time(), _curl(f"https://kick.com/api/v2/channels/{channel}/videos") or [])
        except Exception:
            hit = (time.time(), [])
        _cache[key] = hit
    for v in hit[1] if isinstance(hit[1], list) else []:
        try:
            t0 = datetime.fromisoformat(v["start_time"]).replace(tzinfo=timezone.utc).timestamp()
        except (KeyError, ValueError, TypeError):
            continue
        dur = (v.get("duration") or 0) / 1000 or (time.time() - t0 if v.get("is_live") else 0)
        if t0 - 300 <= t <= t0 + dur + 300:
            return next((c.get("name") for c in v.get("categories") or [] if c.get("name")), None)
    return None


def twitch_vods(channel: str) -> list[dict]:
    """The channel's recent Twitch replays straight from Twitch (vodwatch sleeps with the Mac, so streams that
    started during a sleep are not in vods.jsonl): id, t0, t1 (wall clock), chapters [(t0, t1, game)], muted."""
    from datetime import datetime
    key = ("twitchvods", channel.lower())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    q = ('query{user(login:%s){videos(first:10,type:ARCHIVE){edges{node{id createdAt lengthSeconds status '
         'muteInfo{mutedSegmentConnection{nodes{offset duration}}} '
         'moments(momentRequestType:VIDEO_CHAPTER_MARKERS){edges{node{positionMilliseconds durationMilliseconds '
         'description}}} game{name}}}}}}' % json.dumps(channel.lower()))
    out = []
    try:
        u = ((_curl(GQL, {"query": q}, [f"Client-ID: {CID}"]) or {}).get("data") or {}).get("user") or {}
        for e in (u.get("videos") or {}).get("edges") or []:
            n = e["node"]
            t0 = datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")).timestamp()
            ch = [(t0 + m["node"]["positionMilliseconds"] / 1000,
                   t0 + (m["node"]["positionMilliseconds"] + m["node"]["durationMilliseconds"]) / 1000,
                   m["node"]["description"]) for m in (n.get("moments") or {}).get("edges") or []]
            out.append({"id": n["id"], "t0": t0, "t1": t0 + (n.get("lengthSeconds") or 0), "status": n.get("status"),
                        "start": n["createdAt"], "game": (n.get("game") or {}).get("name"), "chapters": ch,
                        "muted": ((n.get("muteInfo") or {}).get("mutedSegmentConnection") or {}).get("nodes")})
    except Exception:
        return hit[1] if hit else []
    _cache[key] = (time.time(), out)
    return out


def twitch_game_at(channel: str, t: float) -> str | None:
    """Game at wall time t from the replay's chapters (one chapter per game the streamer set)."""
    for v in twitch_vods(channel):
        if v["t0"] - 60 <= t <= v["t1"] + 60:
            for a, b, name in v["chapters"]:
                if a <= t <= b + 60:
                    return name
            return v["game"]
    return None


def is_gta6_at(platform: str, channel: str, t: float) -> tuple[bool, str | None]:
    c = cfg()
    if not c.get("enabled", True):
        return True, None
    name = (twitch_game_at(channel, t) if platform == "twitch" else None) or game_at(platform, channel, t) or (kick_vod_game(channel, t) if platform == "kick" else None)
    ok = matches(name, platform)
    return (c.get("unknown", "allow") == "allow") if ok is None else ok, name
