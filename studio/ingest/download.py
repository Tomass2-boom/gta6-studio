import json
import re
import subprocess
import time
from ..config import DATA, load


KICK_VOD = re.compile(r"kick\.com/([\w-]+)/videos/([0-9a-f-]{36})")


def kick_vod(url: str) -> dict | None:
    """Kick VOD -> its HLS address and metadata. yt-dlp's kick:vod extractor is broken (Kick's v1 video API answers
    404, checked 2026-11-17), so read the channel's video list (v2 API, curl: Kick answers 403 to Python)."""
    m = KICK_VOD.search(url)
    if not m:
        return None
    slug, uuid = m.groups()
    vids = None
    for _ in range(3):   # Kick's API sometimes answers empty or with an HTML block page (seen 2026-11-17): retry
        r = subprocess.run(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0", f"https://kick.com/api/v2/channels/{slug}/videos"],
                           capture_output=True, text=True)
        try:
            vids = json.loads(r.stdout)
            break
        except ValueError:
            time.sleep(3)
    if vids is None:
        raise RuntimeError(f"Kick API gave no video list for {slug} (3 tries): {r.stdout[:80]!r}")
    for v in vids:
        if (v.get("video") or {}).get("uuid") == uuid and v.get("source"):
            return {"id": uuid, "m3u8": v["source"], "title": v.get("session_title"), "streamer": slug,
                    "duration": (v.get("duration") or 0) / 1000 or None}
    raise RuntimeError(f"Kick VOD {uuid} not in the latest videos of {slug} (old, private or deleted)")


def fetch_video(url: str) -> dict:
    """Download a video (up to ingest.max_height, H.264 preferred) with yt-dlp; return info incl. 'most replayed' heatmap."""
    h = load("settings").get("ingest", {}).get("max_height", 1080)
    out = DATA / "raw"
    out.mkdir(parents=True, exist_ok=True)
    kv = kick_vod(url)
    tmpl = str(out / (f"kick-{kv['id']}.%(ext)s" if kv else "%(id)s.%(ext)s"))
    cmd = ["yt-dlp", "-f", f"bv*[height<={h}][vcodec^=avc1]+ba/bv*[height<={h}]+ba/b[height<={h}]/b", "--merge-output-format", "mp4",
           "--write-info-json", "--no-playlist", "-o", tmpl, "--print", "after_move:filepath", kv["m3u8"] if kv else url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"yt-dlp failed: {r.stderr[-500:]}")
    path = r.stdout.strip().splitlines()[-1]
    info_p = path.rsplit(".", 1)[0] + ".info.json"
    info = json.loads(open(info_p).read())
    if kv:   # generic HLS info has no title/channel: fill from Kick and save, `studio vod` re-reads this file
        info.update(title=kv["title"], channel=kv["streamer"], extractor_key="Kick",
                    duration=info.get("duration") or kv["duration"])
        open(info_p, "w").write(json.dumps(info))
    return {"path": path, "title": info.get("title"), "duration": info.get("duration"),
            "streamer": info.get("channel") or info.get("uploader"),
            "platform": (info.get("extractor_key") or "").lower(),
            "heatmap": info.get("heatmap") or []}
