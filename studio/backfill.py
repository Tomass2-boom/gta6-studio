"""Backfill: make clips later from moments that happened while we only logged chat (slow internet).

saved chat (data/chat/*.jsonl) -> the same chat-spike detector as live mode -> spike groups ->
fetch ONLY those stretches from the VOD (stream start from data/vods.jsonl turns wall-clock into a
VOD offset) -> the normal judge + render pipeline. Strongest moments first. Re-running skips moments
already in the database, so it can be stopped and started again at any time.
Streams with no VOD use the local Tier A chunks in data/raw/<channel>/ if there are any.
"""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import DATA, load
from .detect.chat import ChatDetector
from .ingest import game as gta6
from .ingest import live as vid


def _ts(s) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    s = s.replace("Z", "").replace(" ", "T")
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def streams() -> dict[str, list[dict]]:
    """channel -> streams seen by vodwatch (newest info per stream start)."""
    out: dict[str, dict] = {}
    p = DATA / "vods.jsonl"
    for ln in (p.read_text().splitlines() if p.exists() else []):
        r = json.loads(ln)
        if "error" in r or not r.get("start"):
            continue
        out[(r["channel"].lower(), str(r["start"]))] = {**r, "t0": _ts(r["start"])}
    by: dict[str, list[dict]] = {}
    for (ch, _), r in out.items():
        by.setdefault(ch, []).append(r)
    return by


def chat_files(ch: str, platform: str) -> list[Path]:
    pre = {"kick": f"kick-{ch}", "youtube": f"yt-{ch}"}.get(platform, ch)
    return sorted((DATA / "chat").glob(f"{pre}-2*.jsonl"))


def spike_groups(ch: str, platform: str, since: float, until: float, L: dict) -> list[dict]:
    det = ChatDetector(L["chat"])
    msgs = []
    for f in chat_files(ch, platform):
        for ln in f.read_text().splitlines():
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            if since - 900 <= m["t"] <= until:   # 15 min early so the baseline is warm at `since`
                msgs.append((m["t"], m["m"]))
    msgs.sort()
    groups, cur, last_t = [], None, None
    for t, text in msgs:
        if last_t is not None and t - last_t > 600:   # chat gap (offline / lost connection): restart the baseline
            det = ChatDetector(L["chat"])
        last_t = t
        for sp in det.feed(ch, t, text):
            if sp.t < since:
                continue
            if cur and sp.t - cur["last"] <= L["merge_s"] and sp.t - cur["first"] < L["max_window_s"] - L["pre_roll_s"]:
                cur["last"] = sp.t
                cur["spikes"].append(sp)
            else:
                cur = {"first": sp.t, "last": sp.t, "spikes": [sp]}
                groups.append(cur)
    return groups


def done_ranges(con, platform: str, ch: str) -> list[tuple[float, float]]:
    base = vid.stream_url(platform, ch) + "#live="
    rows = con.execute("SELECT url FROM sources WHERE url LIKE ?", (base + "%",)).fetchall()
    return [(float(r["url"].split("#live=")[1]),) * 2 for r in rows]


def hls_url(s: dict, quality: str) -> str:
    if s["platform"] == "kick":
        return s["vod"]
    if s["platform"] == "twitch":
        r = subprocess.run(["streamlink", "--stream-url", s["vod"], quality], capture_output=True, text=True, timeout=60)
        if r.returncode or not r.stdout.startswith("http"):
            raise RuntimeError(f"streamlink: {(r.stdout + r.stderr)[-200:]}")
        return r.stdout.strip()
    raise ValueError(s["platform"])


def fetch(s: dict, a: float, b: float, out: Path, quality: str, min_len: float) -> float:
    """Cut [a, b] out of the VOD; one retry when the cut comes back short (seen once: 18 s of an 80 s cut)."""
    dur = _fetch(s, a, b, out, quality, min_len)
    if dur < 0.8 * (b - a):
        time.sleep(5)
        dur = max(dur, _fetch(s, a, b, out, quality, min_len))
    return dur


def _fetch(s: dict, a: float, b: float, out: Path, quality: str, min_len: float) -> float:
    """Cut [a, b] (wall clock) out of the VOD into out.mp4; return its duration."""
    out.parent.mkdir(parents=True, exist_ok=True)
    # A broken leftover from an earlier failed try (261-byte stubs, Jelly 2026-11-19) makes yt-dlp say "already
    # downloaded" and skip: every retry then fails with "VOD gave only 0s". Always start clean.
    out.unlink(missing_ok=True)
    off, n = max(0.0, a - s["t0"]), b - a
    if s["platform"] == "youtube":
        h = load("settings").get("ingest", {}).get("max_height", 1080)
        r = subprocess.run(["yt-dlp", "-q", "-f", f"bv*[height<={h}][vcodec^=avc1]+ba/b[height<={h}]", "--merge-output-format",
                            "mp4", "--download-sections", f"*{off:.0f}-{off + n:.0f}", "--force-keyframes-at-cuts",
                            "-o", str(out), s["vod"]], capture_output=True, text=True, timeout=900)
        if r.returncode:
            raise RuntimeError(f"yt-dlp: {r.stderr[-300:]} (a live YouTube stream can only be cut after it ends)")
    else:
        ff = vid.tools()[0]
        r = subprocess.run([ff, "-v", "error", "-y", "-ss", f"{off:.2f}", "-i", hls_url(s, quality), "-t", f"{n:.2f}",
                            "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)],
                           capture_output=True, text=True, timeout=900)
        if r.returncode:
            raise RuntimeError(f"ffmpeg: {r.stderr[-300:]}")
    dur = vid.duration(out)
    if dur < min_len:
        raise RuntimeError(f"VOD gave only {dur:.0f}s for this moment (not saved yet, or cut/deleted)")
    return dur


def run(since: str, until: str | None, channels: list[str] | None, top: int | None, dry: bool, model: str | None, lg):
    from .live import resolve_channels
    from .pipeline import process
    L = load("settings")["live"]
    t_since, t_until = _ts(since), (_ts(until) if until else time.time())
    known = {e["channel"]: e for e in resolve_channels(None, lg)}
    by = streams()
    con = db.connect()
    jobs = []
    for ch, e in known.items():
        if channels and ch not in channels and e["name"].lower() not in channels:
            continue
        done = done_ranges(con, e["platform"], ch)
        n, skipped = 0, {}
        for g in spike_groups(ch, e["platform"], t_since, t_until, L):
            if any(abs(g["first"] - d) < L["max_window_s"] for d, _ in done):
                continue
            ok, game = gta6.is_gta6_at(e["platform"], ch, g["first"])
            if not ok:   # user 2026-11-19: only clips of streamers actually playing GTA 6
                skipped[game] = skipped.get(game, 0) + 1
                continue
            # the stream this moment belongs to: the latest one that started before it
            ss = [s for s in by.get(ch, []) if s["t0"] <= g["first"]]
            s = max(ss, key=lambda s: s["t0"]) if ss else None
            jobs.append((max(sp.strength for sp in g["spikes"]), ch, e, g, s))
            n += 1
        lg.info("%s: %d new moments%s", ch, n, f" (skipped, not GTA 6: {skipped})" if skipped else "")
    jobs.sort(key=lambda j: -j[0])
    if top:
        jobs = jobs[:top]
    for strength, ch, e, g, s in jobs:
        when = datetime.fromtimestamp(g["first"], timezone.utc)
        src = "VOD" if s and s.get("vod") else ("local recording" if (vid.RAW / ch).exists() else "NO VIDEO")
        print(f"{when:%H:%M:%S}Z  {e['name']:<14} x{strength:4.1f}  {len(g['spikes'])} spikes  from {src}")
    if dry:
        return
    def prepare(job):
        """Source row + video for one moment (runs ahead in threads). Returns (mid, path) or None."""
        strength, ch, e, g, s = job
        con = db.connect()
        first, last = g["first"], g["last"]
        a, b = first - L["pre_roll_s"], last + L["post_roll_s"]
        sig = {"meta": {"spike_wall": [round(sp.t) for sp in g["spikes"]],
                        "hype_share": round(max(sp.hype_share for sp in g["spikes"]), 2),
                        "tier": "backfill", "detected_at": first, "vod": (s or {}).get("vod")},
               "chat": round(strength, 2)}
        url = f"{vid.stream_url(e['platform'], ch)}#live={int(first)}"
        title = f"{e['name']} live {datetime.fromtimestamp(first, timezone.utc):%Y-%m-%d %H:%MZ}"
        cur = con.execute("INSERT OR IGNORE INTO sources(url,platform,streamer,title) VALUES(?,?,?,?)",
                          (url, e["platform"], e["name"], title))
        con.commit()
        if not cur.rowcount:
            return None
        sid = cur.lastrowid
        out = vid.SEG / f"{ch}-{int(first)}.mp4"
        try:
            q = e.get("quality") or L.get("quality", "best")
            if s and s.get("vod"):
                dur = fetch(s, a, b, out, q, L["min_len_s"])
                base = a
            elif (vid.RAW / ch).exists():
                out, base, dur = vid.cut_from_recording(ch, a, b, first, out, L["chunk_s"], L["min_len_s"],
                                                        L.get("hole_s", {}).get(e["platform"]))
            else:
                raise RuntimeError("no VOD and no local recording for this stream")
        except Exception as ex:   # no silent failures
            lg.warning("%s %s: %s", ch, title, ex)
            con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,state,fail_reason) VALUES(?,?,?,?,'failed',?)",
                        (sid, 0, 0, json.dumps(sig), f"video: {ex}"[:500]))
            con.commit()
            return None
        sig["meta"]["video_from"] = base
        con.execute("UPDATE sources SET path=?, duration=? WHERE id=?", (str(out), dur, sid))
        mid = con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,signal_score) VALUES(?,?,?,?,?)",
                          (sid, 0, dur, json.dumps(sig), 10 + strength)).lastrowid
        con.commit()
        return mid, str(out)

    from concurrent.futures import ThreadPoolExecutor
    ok = 0
    # moments a stopped earlier run downloaded but never judged: do them first
    for r in con.execute("SELECT m.id, s.path FROM moments m JOIN sources s ON s.id=m.source_id WHERE m.state='detected' "
                         "AND m.signals LIKE '%\"tier\": \"backfill\"%' AND s.path IS NOT NULL").fetchall():
        if Path(r["path"]).exists():
            ok += bool(process(con, r["id"], r["path"], lg, model))
            print(f"[left over] moment {r['id']} done ({ok} clips so far)")
    with ThreadPoolExecutor(3) as pool:   # download up to 3 moments ahead while one is judged (GPU is the bottleneck)
        for i, (job, r) in enumerate(zip(jobs, pool.map(prepare, jobs)), 1):
            if not r:
                print(f"[{i}/{len(jobs)}] {job[2]['name']}: skipped or video failed (see log)")
                continue
            good = process(con, r[0], r[1], lg, model)
            ok += bool(good)
            print(f"[{i}/{len(jobs)}] moment {r[0]} {job[2]['name']} {'CLIP READY' if good else 'no clip'}  ({ok} clips so far)")
