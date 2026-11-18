"""Every 10 min, note each live stream's start time and VOD address in data/vods.jsonl.

With chat-only capture (studio.chatlog) this is what lets us fetch the moments later from the VOD:
stream start converts chat wall-clock times into VOD offsets. Twitch muted stretches are saved too
(muted VOD audio ruins a clip). Run: uv run python -m studio.vodwatch
"""
import json
import subprocess
import time

from .config import DATA
from .live import resolve_channels
import logging

GQL = "https://gql.twitch.tv/gql"
CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"


def _curl(url, data=None, headers=()):
    cmd = ["curl", "-s", "-m", "20", "-A", "Mozilla/5.0", *sum((["-H", h] for h in headers), []), url]
    if data:
        cmd += ["-d", json.dumps(data)]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)


def twitch(logins):
    q = ("query{users(logins:%s){login stream{createdAt game{name}} videos(first:1,type:ARCHIVE){edges{node{id createdAt "
         "status muteInfo{mutedSegmentConnection{nodes{offset duration}}}}}}}}" % json.dumps(logins))
    out = []
    for u in _curl(GQL, {"query": q}, [f"Client-ID: {CID}"])["data"]["users"]:
        if not (u and u["stream"]):
            continue
        v = (u["videos"]["edges"] or [{}])[0].get("node") or {}
        same = v.get("status") == "RECORDING"   # an older finished VOD = this stream is not being saved
        out.append({"platform": "twitch", "channel": u["login"], "start": u["stream"]["createdAt"],
                    "game": (u["stream"]["game"] or {}).get("name"),
                    "vod": f"https://www.twitch.tv/videos/{v['id']}" if same else None,
                    "muted": ((v.get("muteInfo") or {}).get("mutedSegmentConnection") or {}).get("nodes") if same else None})
    return out


def kick(slug):
    vids = _curl(f"https://kick.com/api/v2/channels/{slug}/videos")
    out = []
    for v in vids[:3]:
        if v.get("is_live") or v.get("start_time", "") >= "2026-11-18":
            out.append({"platform": "kick", "channel": slug, "start": v.get("start_time"), "live": v.get("is_live"),
                        "duration_ms": v.get("duration"), "vod": v.get("source"),
                        "page": f"https://kick.com/{slug}/videos/{(v.get('video') or {}).get('uuid')}"})
    return out


def youtube(handle):
    r = subprocess.run(["yt-dlp", "--print", "%(id)s %(release_timestamp)s %(live_status)s", "--skip-download",
                        f"https://youtube.com/@{handle}/live"], capture_output=True, text=True, timeout=90)
    if not r.stdout.strip():
        return []
    vid, ts, status = r.stdout.split()
    if status != "is_live":
        return []
    return [{"platform": "youtube", "channel": handle, "start": int(ts) if ts.isdigit() else None,
             "vod": f"https://www.youtube.com/watch?v={vid}"}]


def main():
    lg = logging.getLogger("vodwatch")
    while True:
        chans = resolve_channels(None, lg)
        rows = []
        for fn, arg in ([(twitch, [e["channel"] for e in chans if e["platform"] == "twitch"])] +
                        [(kick, e["channel"]) for e in chans if e["platform"] == "kick"] +
                        [(youtube, e["channel"]) for e in chans if e["platform"] == "youtube"]):
            try:
                rows += fn(arg)
            except Exception as ex:
                rows.append({"error": f"{fn.__name__} {arg}: {type(ex).__name__} {ex}"[:200]})
        with open(DATA / "vods.jsonl", "a") as f:
            for r in rows:
                f.write(json.dumps({"seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **r}) + "\n")
        time.sleep(600)


if __name__ == "__main__":
    main()
