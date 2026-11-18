"""SQLite state. detected -> fetched -> judged -> rendered -> approved -> posted (or failed)."""
import json
import sqlite3
from .config import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, url TEXT UNIQUE, platform TEXT, streamer TEXT,
  title TEXT, path TEXT, duration REAL, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS moments(
  id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id),
  state TEXT DEFAULT 'detected', fail_reason TEXT,
  t_start REAL, t_end REAL, signals TEXT, signal_score REAL,
  transcript TEXT, keep INTEGER, score REAL, reason TEXT, title TEXT, hashtags TEXT,
  j_start REAL, j_end REAL, judge_model TEXT, clip_path TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
-- The user's own grade of a finished clip (control app). Source info is copied in so a grade
-- still makes sense if the moment is re-rendered or its source is deleted. yt_* stay empty
-- until view numbers are fetched (later).
CREATE TABLE IF NOT EXISTS grades(
  moment_id INTEGER PRIMARY KEY REFERENCES moments(id), score INTEGER, notes TEXT,
  streamer TEXT, platform TEXT, url TEXT, j_start REAL, j_end REAL,
  judge_score REAL, judge_model TEXT, title TEXT, clip_path TEXT, layout TEXT,
  yt_video_id TEXT, yt_views INTEGER, views_checked TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
-- Clips the user posted by hand (a note only; nothing here posts anything).
CREATE TABLE IF NOT EXISTS posts(
  moment_id INTEGER REFERENCES moments(id), platform TEXT, posted_at TEXT DEFAULT CURRENT_TIMESTAMP,
  post_url TEXT, PRIMARY KEY(moment_id, platform));
"""


def connect() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / "studio.db", timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(moments)")}
    for col in ("reason", "words", "keywords", "layout"):  # words: JSON [{word,start,end}] in source seconds
        if col not in cols:
            con.execute(f"ALTER TABLE moments ADD COLUMN {col} TEXT")
    return con


def update(con, moment_id: int, **fields):
    for k in ("signals", "words", "keywords"):
        if k in fields and fields[k] is not None and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE moments SET {cols}, updated=CURRENT_TIMESTAMP WHERE id=?",
                (*fields.values(), moment_id))
    con.commit()


def fail(con, moment_id: int, reason: str):
    update(con, moment_id, state="failed", fail_reason=reason)
