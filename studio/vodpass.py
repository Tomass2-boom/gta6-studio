"""VOD pass: go through a WHOLE past Twitch stream and clip its best moments (user 2026-11-19, ohnePixel).

VOD mode (`studio vod`) only judges the top loudness spots. This reads everything:
1. audio-only download, full Whisper transcript in 10-min chunks (cached in data/vodpass/<id>/, resumable);
2. the local LLM rates every 2-min block of the transcript for clip potential (cached);
3. viewers' own Twitch clips of that stream (view counts) and loudness peaks are added as signals;
4. the strongest windows (merged, minus moments already in the DB) are cut from the VOD at full quality and go
   through the normal judge + render pipeline, strongest first. Re-running continues where it stopped.
"""
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import DATA, load

OUT = DATA / "vodpass"
BLOCK_S = 120

RATE = """You read a 2-minute piece of a live stream transcript of {name} playing GTA 6 and rate how good a viral
TikTok/Shorts clip could be cut from it. Big hits are: a streamer's disbelief when the game acts like real life,
a funny fail or chaos, a shocking or absurd discovery, an honest verdict on the game told to chat, a goal-driven
mini-story that escalates, moments with the main characters in gameplay. Low: calm driving, menus, reading chat,
sponsor talk, story cutscene dialogue, music, off-topic chatter.
Lines are "[seconds] text":
{lines}
Reply ONLY JSON: {{"score": 0-10, "at": second where the best moment starts, "what": "max 8 words"}}"""


def _ffprobe_dur(p: str) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "default=nw=1:nk=1", p]).strip())


def info(url: str) -> dict:
    r = subprocess.run(["yt-dlp", "-J", "--skip-download", url], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    return {"id": str(d["id"]).lstrip("v"), "t0": float(d["timestamp"]), "duration": float(d["duration"]),
            "title": d.get("title"), "channel": (d.get("uploader_id") or d.get("uploader") or "").lower(),
            "live": d.get("live_status") == "is_live"}


def audio(url: str, d: Path) -> str:
    for p in d.glob("audio.*"):
        if not p.name.endswith((".part", ".ytdl")):
            return str(p)
    subprocess.run(["yt-dlp", "-q", "-f", "Audio_Only/audio_only/bestaudio", "-o", str(d / "audio.%(ext)s"), url],
                   check=True)
    return audio(url, d)


def transcript(a: str, d: Path, lg, chunk: float = 600) -> list[dict]:
    """Words [{word,start,end}] of the whole file; chunks already done are skipped."""
    from .transcribe.whisper import transcribe
    f, dur = d / "words.jsonl", _ffprobe_dur(a)
    done = {json.loads(l)["a"]: json.loads(l)["words"] for l in f.read_text().splitlines()} if f.exists() else {}
    with f.open("a") as fh:
        t = 0.0
        while t < dur:
            if t not in done:
                w = [x for x in transcribe(a, t, min(t + chunk, dur))["words"] if t <= x["start"] < t + chunk]
                done[t] = w
                fh.write(json.dumps({"a": t, "words": w}) + "\n"); fh.flush()
                lg.info("transcript %.1fh / %.1fh", (t + chunk) / 3600, dur / 3600)
            t += chunk
    return [w for k in sorted(done) for w in done[k]]


def rate_blocks(words: list[dict], name: str, d: Path, lg, model: str | None = None) -> dict[int, dict]:
    from .judge.llm import _ask
    f = d / "rated.json"
    rated = {int(k): v for k, v in json.loads(f.read_text()).items()} if f.exists() else {}
    n = int(words[-1]["end"] // BLOCK_S) + 1 if words else 0
    for b in range(n):
        if b in rated:
            continue
        ws = [w for w in words if b * BLOCK_S <= w["start"] < (b + 1) * BLOCK_S]
        if len(ws) < 15:          # near-silence: nothing to clip
            rated[b] = {"score": 0}
        else:
            lines, cur, t = [], [], ws[0]["start"]
            for w in ws:          # ~one line per sentence-ish pause
                if cur and (w["start"] - cur[-1]["end"] > 0.6 or len(cur) >= 18):
                    lines.append(f"[{cur[0]['start']:.0f}] " + " ".join(x["word"] for x in cur)); cur = []
                cur.append(w)
            lines.append(f"[{cur[0]['start']:.0f}] " + " ".join(x["word"] for x in cur))
            try:
                r = _ask(RATE.format(name=name, lines="\n".join(lines)), model)
                rated[b] = {"score": float(r.get("score") or 0), "at": float(r.get("at") or ws[0]["start"]),
                            "what": str(r.get("what") or "")[:80]}
            except Exception as ex:   # no silent failures
                lg.warning("block %d: %s", b, ex)
                rated[b] = {"score": 0, "error": str(ex)[:200]}
        if b % 10 == 0:
            f.write_text(json.dumps(rated)); lg.info("rated %d/%d blocks", b + 1, n)
    f.write_text(json.dumps(rated))
    return rated


def twitch_clips(channel: str, t0: float, dur: float) -> list[dict]:
    """Viewer-made clips of this stream: [{off, dur, views, title}] (offset = created - stream start - length)."""
    r = subprocess.run(["yt-dlp", "--flat-playlist", "-J", "--playlist-end", "500",
                        f"https://www.twitch.tv/{channel}/clips?filter=clips&range=7d"], capture_output=True, text=True)
    try:
        e = json.loads(r.stdout).get("entries", [])
    except ValueError:
        return []
    return [{"off": x["timestamp"] - t0 - (x.get("duration") or 30), "dur": x.get("duration") or 30,
             "views": x.get("view_count") or 0, "title": x.get("title") or ""}
            for x in e if x.get("timestamp") and t0 <= x["timestamp"] <= t0 + dur + 60]


def candidates(rated: dict, clips: list[dict], loud: list[int], pre: float, post: float) -> list[dict]:
    """Windows [a, b] (VOD seconds) with a combined strength; overlapping windows merge (strength = max + small bonus)."""
    w = []
    for b, r in rated.items():
        if r.get("score", 0) >= 6:
            t = r.get("at", b * BLOCK_S)
            w.append({"a": t - pre, "b": t + post, "s": r["score"], "why": [f"text {r['score']:.0f}: {r.get('what', '')}"]})
    for c in clips:
        if c["views"] >= 300:
            w.append({"a": c["off"] - 30, "b": c["off"] + c["dur"] + 30, "s": 4 + 1.5 * math.log10(c["views"]),
                      "why": [f"twitch clip {c['views']} views: {c['title'][:40]}"]})
    for t in loud:
        w.append({"a": t - pre, "b": t + post * 0.6, "s": 6.0, "why": ["loud"]})
    w.sort(key=lambda x: x["a"])
    out = []
    for x in w:
        if out and x["a"] <= out[-1]["b"] - 10 and max(out[-1]["b"], x["b"]) - out[-1]["a"] <= 200:
            o = out[-1]
            o["b"], o["why"] = max(o["b"], x["b"]), o["why"] + x["why"]
            o["s"] = max(o["s"], x["s"]) + 0.5
        else:
            out.append(dict(x))
    for o in out:
        o["a"] = max(0.0, o["a"])
    return sorted(out, key=lambda x: -x["s"])


def run(url: str, top: int, dry: bool, model: str | None, lg):
    from .backfill import fetch
    from .detect import signals
    from .ingest import live as vid
    from .pipeline import process
    v = info(url)
    if v["live"]:
        raise SystemExit("This stream is still live: live mode covers it. Run the VOD pass after it ends.")
    names = {s.get("channel", "").lower(): s for s in load("streamers").get("streamers", [])}
    e = names.get(v["channel"], {"name": v["channel"], "channel": v["channel"]})
    d = OUT / v["id"]; d.mkdir(parents=True, exist_ok=True)
    lg.info("%s: %s (%.1f h)", e["name"], v["title"], v["duration"] / 3600)
    a = audio(url, d)
    words = transcript(a, d, lg)
    rated = rate_blocks(words, e["name"], d, lg, model)
    clips = twitch_clips(v["channel"], v["t0"], v["duration"])
    loud = signals.loud_peaks(signals.loudness_per_second(a), z=load("settings")["clip"]["loud_z"])
    from .config import clip_cfg
    cap = clip_cfg(e["name"])["max_len_s"]
    win = candidates(rated, clips, list(loud), pre=25, post=min(cap, 90))
    con = db.connect()
    base = f"https://twitch.tv/{v['channel']}#live="
    done = [float(r["url"].split("#live=")[1]) for r in
            con.execute("SELECT url FROM sources WHERE url LIKE ?", (base + "%",)).fetchall()]
    win = [x for x in win if not any(v["t0"] + x["a"] - 30 <= t <= v["t0"] + x["b"] for t in done)][:top]
    (d / "candidates.json").write_text(json.dumps(win, indent=1))
    for i, x in enumerate(win, 1):
        print(f"{i:>3}. {x['a'] / 3600:5.2f}h  s={x['s']:4.1f}  {' | '.join(x['why'])[:110]}")
    if dry:
        return
    s = {"platform": "twitch", "vod": url, "t0": v["t0"]}
    L = load("settings")["live"]
    ok = 0
    for i, x in enumerate(win, 1):
        first = v["t0"] + x["a"]
        src_url = f"{base}{int(first)}"
        title = f"{e['name']} VOD {datetime.fromtimestamp(first, timezone.utc):%Y-%m-%d %H:%MZ}"
        cur = con.execute("INSERT OR IGNORE INTO sources(url,platform,streamer,title) VALUES(?,?,?,?)",
                          (src_url, "twitch", e["name"], title))
        con.commit()
        if not cur.rowcount:
            continue
        sid = cur.lastrowid
        sig = {"meta": {"tier": "vodpass", "vod": url, "vod_off": round(x["a"]), "why": x["why"], "detected_at": first},
               "vodpass": round(x["s"], 2)}
        out = vid.SEG / f"{v['channel']}-vod-{int(first)}.mp4"
        try:
            dur = fetch(s, first, v["t0"] + x["b"], out, e.get("quality") or L.get("quality", "best"), L["min_len_s"])
        except Exception as ex:
            lg.warning("%s: %s", title, ex)
            con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,state,fail_reason) VALUES(?,?,?,?,'failed',?)",
                        (sid, 0, 0, json.dumps(sig), f"video: {ex}"[:500]))
            con.commit()
            continue
        con.execute("UPDATE sources SET path=?, duration=? WHERE id=?", (str(out), dur, sid))
        mid = con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,signal_score) VALUES(?,?,?,?,?)",
                          (sid, 0, dur, json.dumps(sig), x["s"])).lastrowid
        con.commit()
        good = process(con, mid, str(out), lg, model)
        ok += bool(good)
        print(f"[{i}/{len(win)}] moment {mid} {'CLIP READY' if good else 'no clip'}  ({ok} clips so far)", flush=True)
