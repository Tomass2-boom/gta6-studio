import json
import subprocess
from ..config import DATA, load


def fetch_video(url: str) -> dict:
    """Download a video (up to ingest.max_height, H.264 preferred) with yt-dlp; return info incl. 'most replayed' heatmap."""
    h = load("settings").get("ingest", {}).get("max_height", 1080)
    out = DATA / "raw"
    out.mkdir(parents=True, exist_ok=True)
    tmpl = str(out / "%(id)s.%(ext)s")
    cmd = ["yt-dlp", "-f", f"bv*[height<={h}][vcodec^=avc1]+ba/bv*[height<={h}]+ba/b[height<={h}]/b", "--merge-output-format", "mp4",
           "--write-info-json", "--no-playlist", "-o", tmpl, "--print", "after_move:filepath", url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"yt-dlp failed: {r.stderr[-500:]}")
    path = r.stdout.strip().splitlines()[-1]
    info = json.loads(open(path.rsplit(".", 1)[0] + ".info.json").read())
    return {"path": path, "title": info.get("title"), "duration": info.get("duration"),
            "streamer": info.get("channel") or info.get("uploader"),
            "platform": (info.get("extractor_key") or "").lower(),
            "heatmap": info.get("heatmap") or []}
