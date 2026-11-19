"""Import moments a capture node (the Windows PC, docs/windows-node.md) saved while the Mac was away.

Syncthing brings data/remote/<node>/outbox/moments/<node>-<channel>-<spike>.mp4 + .json. A pair is imported once
both files are there (Syncthing writes each file under a temp name and renames it when complete):
the video is copied to data/segments/, a source + moment go into the DB with all source info, then the normal
pipeline (Whisper -> judge -> render) runs, STRONGEST MOMENT FIRST, so the best clips are ready within minutes
of getting home even with a day of backlog.

Duplicates: a moment the Mac's own live run already cut (same channel, spike within live.max_window_s) is skipped,
and the Mac's cut wins (1080p, more streams). While Mac live is running, a Windows moment younger than
remote.import_delay_s waits so the Mac's own cut has time to land first.
Re-running is safe: the source URL (stream#live=<spike>) is unique, so nothing is imported twice.
"""
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import DATA, load
from .ingest import live as vid

REMOTE = DATA / "remote"


def settings() -> dict:
    s = load("settings")
    r = s.get("remote") or {}
    return {"delay": r.get("import_delay_s", 600), "window": s["live"].get("max_window_s", 100)}


def pairs() -> list[tuple[Path, Path]]:
    out = []
    for j in sorted(REMOTE.glob("*/outbox/moments/*.json")):
        mp4 = j.with_suffix(".mp4")
        if mp4.exists():
            out.append((j, mp4))
    return out


def mac_live_running() -> bool:
    try:
        from .watchdog import live_pids
        return bool(live_pids())
    except Exception:
        return False


def _copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)   # same disk: instant, and survives Windows deleting its outbox copy later
    except OSError:
        shutil.copy2(src, dst)


def scan(con, lg, live_running: bool | None = None) -> int:
    """Bring every new complete pair into the DB as a 'detected' moment. Returns how many were added."""
    cfg = settings()
    live_running = mac_live_running() if live_running is None else live_running
    added = 0
    for j, mp4 in pairs():
        try:
            m = json.loads(j.read_text())
            ch, platform, spike = m["channel"].lower(), m["platform"], float(m["spike_t"])
        except (OSError, ValueError, KeyError, TypeError) as ex:
            lg.warning("import: unreadable %s (%s), skipped for now", j.name, ex)
            continue
        base = vid.stream_url(platform, ch) + "#live="
        url = base + str(int(spike))
        if con.execute("SELECT 1 FROM sources WHERE url=?", (url,)).fetchone():
            continue   # imported (or the Mac has this exact moment) already
        if live_running and time.time() - float(m.get("created") or spike) < cfg["delay"]:
            continue   # give the Mac's own live cut time to land first
        title = f"{m.get('streamer') or ch} live {datetime.fromtimestamp(spike, timezone.utc):%Y-%m-%d %H:%MZ}"
        sig = m.get("signals") or {}
        sig.setdefault("meta", {})["node"] = m.get("node", j.parent.parent.parent.name)
        sig["meta"].setdefault("video_from", m.get("t_start"))
        dup = None
        for r in con.execute("SELECT s.url, m.id FROM sources s LEFT JOIN moments m ON m.source_id=s.id "
                             "WHERE s.url LIKE ?", (base + "%",)):
            try:
                if abs(float(r["url"].split("#live=")[1]) - spike) < cfg["window"]:
                    dup = r["id"]
                    break
            except ValueError:
                continue
        cur = con.execute("INSERT OR IGNORE INTO sources(url,platform,streamer,title) VALUES(?,?,?,?)",
                          (url, platform, m.get("streamer") or ch, title))
        if not cur.rowcount:
            continue
        sid = cur.lastrowid
        if dup is not None:   # recorded so it is never looked at again; the Mac's moment is the one we keep
            con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,state,fail_reason) VALUES(?,?,?,?,'failed',?)",
                        (sid, 0, 0, json.dumps(sig), f"duplicate: the Mac already has this moment (#{dup})"))
            con.commit()
            lg.info("import: %s is a duplicate of moment %s, skipped", j.stem, dup)
            continue
        dst = vid.SEG / f"{j.stem}.mp4"
        try:
            _copy(mp4, dst)
            dur = vid.duration(dst)
            if dur < 3:
                raise RuntimeError(f"video is only {dur:.1f}s")
        except Exception as ex:   # no silent failures
            con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,state,fail_reason) VALUES(?,?,?,?,'failed',?)",
                        (sid, 0, 0, json.dumps(sig), f"import: {ex}"[:500]))
            con.commit()
            lg.warning("import: %s failed (%s)", j.stem, ex)
            continue
        con.execute("UPDATE sources SET path=?, duration=? WHERE id=?", (str(dst), dur, sid))
        con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,signal_score) VALUES(?,?,?,?,?)",
                    (sid, 0, dur, json.dumps(sig), float(m.get("signal_score") or 0)))
        con.commit()
        added += 1
        lg.info("import: %s (%.0fs, signal %.1f) added", j.stem, dur, float(m.get("signal_score") or 0))
    return added


def next_moment(con, skip: set[int]):
    """The strongest imported moment not processed yet. 'fetched' = transcribed but interrupted (Mac slept)."""
    for r in con.execute("SELECT m.id, s.path FROM moments m JOIN sources s ON s.id=m.source_id "
                         "WHERE m.state IN ('detected','fetched') AND m.signals LIKE '%\"node\": %' "
                         "AND s.path IS NOT NULL ORDER BY m.signal_score DESC"):
        if r["id"] not in skip and Path(r["path"]).exists():
            return r
    return None


def run(lg, model: str | None = None, watch: bool = False, poll_s: int = 60):
    """Import and process, best first. Re-scans after every moment, so a stronger one that just synced jumps ahead.
    watch=True keeps going (the watchdog runs it like this); otherwise stops when nothing is left."""
    from .pipeline import process
    tried: set[int] = set()
    while True:
        con = db.connect()
        try:
            n = scan(con, lg)
            if n:
                lg.info("import: %d new moments from the capture node", n)
            r = next_moment(con, tried)
            if r:
                tried.add(r["id"])
                ok = process(con, r["id"], r["path"], lg, model)
                lg.info("import: moment %d %s", r["id"], "CLIP READY" if ok else "no clip")
                continue
        except Exception:   # a bad pair must never stop the importer
            lg.exception("import: error (continuing)")
        finally:
            con.close()
        if not watch:
            return
        time.sleep(poll_s)
