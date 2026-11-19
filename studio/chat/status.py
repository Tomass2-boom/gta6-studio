"""Which chat connections are up right now.

The readers call mark(); chatlog writes the snapshot to data/app/chat_ok.json every few seconds; the node
supervisor reads it for heartbeat.json and the per-minute coverage file. A stale file means chatlog is hung or dead.
"""
import json
import os
import time

from ..config import DATA

FILE = DATA / "app" / "chat_ok.json"
_ok: set[str] = set()


def mark(channels, ok: bool):
    for c in ([channels] if isinstance(channels, str) else channels):
        (_ok.add if ok else _ok.discard)(c)


def write():
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_name(FILE.name + ".tmp")
    tmp.write_text(json.dumps({"t": time.time(), "ok": sorted(_ok)}), encoding="utf-8")
    os.replace(tmp, FILE)


def read(max_age: float = 60.0) -> list[str] | None:
    """Channels with a live chat connection, or None if the snapshot is missing or stale (chatlog not running)."""
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        return d["ok"] if time.time() - d["t"] <= max_age else None
    except (OSError, ValueError, KeyError):
        return None
