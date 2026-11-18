"""Finished clips for the control app, plus the user's grades and 'posted' notes."""
import os
import time
import re
from pathlib import Path

from .. import db
from ..config import load

PLATFORMS = ("youtube", "tiktok", "instagram")


def _mask_text(text: str, roots: list[str]) -> str:
    from ..render.captions import mask
    return re.sub(r"[A-Za-z*']+", lambda m: mask(m.group(0), roots), text) if roots else text


def hashtags(m: dict, cfg: dict) -> str:
    tags = (m.get("hashtags") or "").strip() or cfg.get("default_hashtags", "")
    have = {t.lower() for t in tags.split()}
    for t in (cfg.get("always_hashtags") or "").split():
        if t.lower() not in have:
            tags += " " + t
    return tags.strip()


def texts(m: dict) -> dict:
    """Title, description and hashtags to copy, masked like the captions."""
    cfg = load("settings").get("review", {})
    roots = load("style").get("captions", {}).get("mask_roots") or []
    tags = hashtags(m, cfg)
    title = (m.get("title") or "").strip()
    tpl = cfg.get("description_template") or "{title}\n\n{hashtags}"
    plat = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}.get(m.get("platform") or "", m.get("platform") or "stream")
    desc = tpl.format(title=title, streamer=m.get("streamer") or "a streamer", platform=plat, hashtags=tags)
    return {"title": _mask_text(title, roots), "description": _mask_text(desc, roots), "hashtags": tags}


def _rows(con, where: str = "", args=()):
    return con.execute(
        "SELECT m.id, m.state, m.score, m.reason, m.title, m.hashtags, m.clip_path, m.layout, m.j_start, m.j_end,"
        " m.judge_model, m.updated, m.source_id, s.streamer, s.platform, s.url, s.title AS source_title,"
        " g.score AS grade, g.notes, g.yt_views,"
        " CAST(strftime('%s', g.updated) AS REAL) AS graded_at"
        " FROM moments m JOIN sources s ON s.id = m.source_id LEFT JOIN grades g ON g.moment_id = m.id"
        f" WHERE m.clip_path IS NOT NULL {where}", args).fetchall()


def list_clips(con=None) -> list[dict]:
    """Clips whose file exists, newest file first. One row per file (the newest moment wins)."""
    con = con or db.connect()
    posts = {}
    for r in con.execute("SELECT moment_id, platform FROM posts"):
        posts.setdefault(r[0], []).append(r[1])
    seen, out = set(), []
    for r in _rows(con):
        p = r["clip_path"]
        if not os.path.exists(p):
            continue
        d = dict(r)
        d["mtime"] = os.path.getmtime(p)
        d["name"] = Path(p).stem
        d["posted"] = posts.get(r["id"], [])
        # Remade = you already graded or left notes, and the clip file was rewritten after that.
        # Grading it again (or editing the note) clears the flag.
        d["remade"] = bool(d.get("graded_at")) and d["mtime"] > d["graded_at"] + 5
        # To review = remade, or made in the last 3 h and not rated by you yet.
        d["unrated"] = (not d["grade"]) and not d["posted"] and (time.time() - d["mtime"] < 3 * 3600)
        out.append(d)
    out.sort(key=lambda d: (d["mtime"], d["id"]), reverse=True)
    res = []
    for d in out:
        if d["clip_path"] not in seen:
            seen.add(d["clip_path"])
            res.append(d)
    return res


def get(con, mid: int) -> dict | None:
    r = _rows(con, "AND m.id = ?", (mid,))
    if not r:
        return None
    d = dict(r[0])
    d["name"] = Path(d["clip_path"]).stem
    d["exists"] = os.path.exists(d["clip_path"])
    d["posted"] = [x[0] for x in con.execute("SELECT platform FROM posts WHERE moment_id=?", (mid,))]
    d.update(texts(d))
    return d


def grade(con, mid: int, score: int | None = None, notes: str | None = None):
    """Save the user's 1-10 score and/or notes, with the moment's source info copied in."""
    if score is not None and not 1 <= int(score) <= 10:
        raise ValueError("score must be 1-10")
    m = get(con, mid)
    if not m:
        raise KeyError(mid)
    src = {"streamer": m["streamer"], "platform": m["platform"], "url": m["url"], "j_start": m["j_start"],
           "j_end": m["j_end"], "judge_score": m["score"], "judge_model": m["judge_model"],
           "title": m["title"], "clip_path": m["clip_path"], "layout": m["layout"]}
    con.execute("INSERT OR IGNORE INTO grades(moment_id) VALUES(?)", (mid,))
    fields = dict(src)
    if score is not None:
        fields["score"] = int(score)
    if notes is not None:
        fields["notes"] = notes
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE grades SET {cols}, updated=CURRENT_TIMESTAMP WHERE moment_id=?", (*fields.values(), mid))
    con.commit()


def set_posted(con, mid: int, platform: str, on: bool):
    if platform not in PLATFORMS:
        raise ValueError(platform)
    if on:
        con.execute("INSERT OR IGNORE INTO posts(moment_id, platform) VALUES(?,?)", (mid, platform))
    else:
        con.execute("DELETE FROM posts WHERE moment_id=? AND platform=?", (mid, platform))
    con.commit()
