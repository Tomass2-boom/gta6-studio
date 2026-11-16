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
  transcript TEXT, keep INTEGER, score REAL, title TEXT, hashtags TEXT,
  j_start REAL, j_end REAL, judge_model TEXT, clip_path TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def connect() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / "studio.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def update(con, moment_id: int, **fields):
    if "signals" in fields and not isinstance(fields["signals"], str):
        fields["signals"] = json.dumps(fields["signals"])
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE moments SET {cols}, updated=CURRENT_TIMESTAMP WHERE id=?",
                (*fields.values(), moment_id))
    con.commit()


def fail(con, moment_id: int, reason: str):
    update(con, moment_id, state="failed", fail_reason=reason)
