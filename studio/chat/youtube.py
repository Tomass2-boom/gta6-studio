"""YouTube Live chat via yt-dlp (already installed): `--write-subs --sub-langs live_chat` keeps writing the chat of a
running stream to a .part file as JSON lines; we tail that file. No extra dependency (chat-downloader is broken
on YouTube as of 2026-11-17: "Unable to parse initial video data"). Retries when the stream is offline or ends."""
import asyncio
import json
import os
import signal
import subprocess
import time

from ..config import DATA
from .twitch import _save


def parse_line(line: str) -> list[tuple[str, str]]:
    """One JSON line of the live_chat file -> [(user, text)] for the text messages in it."""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    out = []
    try:    # YouTube sometimes sends other shapes (strings, lists): skip those lines, never crash the run
        acts = d.get("replayChatItemAction", {}).get("actions", [])
        for a in acts:
            r = a.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
            if not r:
                continue
            runs = r.get("message", {}).get("runs", [])
            text = "".join(x.get("text") or (x.get("emoji", {}).get("shortcuts") or [""])[0] for x in runs)
            out.append((r.get("authorName", {}).get("simpleText", ""), text.strip()))
    except (AttributeError, TypeError):
        return []
    return [m for m in out if m[1]]


async def read_one(channel: str, url: str, on_message, log=None, save_chat=True):
    """Forever: start yt-dlp on the live URL, tail its chat file, call on_message(channel, ts, user, text)."""
    d = DATA / "chat"
    d.mkdir(parents=True, exist_ok=True)
    base = d / f"yt-{channel}"
    while True:
        for f in d.glob(f"yt-{channel}.live_chat.json*"):
            f.unlink()
        p = subprocess.Popen(["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o",
                              f"{base}.%(ext)s", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        t0, pos, buf, got, first = time.time(), 0, b"", 0, True   # first batch = old backlog
        try:
            while True:
                await asyncio.sleep(1)
                alive = p.poll() is None
                f = next(iter(sorted(d.glob(f"yt-{channel}.live_chat.json*"))), None)   # .part, later the final file
                if f:
                    try:
                        with open(f, "rb") as fh:
                            fh.seek(pos)
                            new = fh.read()
                    except OSError:   # yt-dlp renames/removes its .part files while we read (crashed chatlog 21:04)
                        if not alive:
                            break
                        continue
                    pos += len(new)
                    backlog = first and bool(new)
                    first = first and not new
                    *lines, buf = (buf + new).split(b"\n")
                    for ln in lines:
                        for user, text in parse_line(ln.decode("utf-8", "replace")):
                            ts = time.time()
                            got += 1
                            if save_chat:
                                _save(f"yt-{channel}", ts, user, text)
                            if not backlog:      # yt-dlp dumps recent chat at start: not a live burst
                                on_message(channel, ts, user, text)
                if not alive:
                    break
        finally:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except Exception:
                    pass
        if log:
            log.warning("%s: YouTube chat reader stopped after %.0fs (%d messages); retry in 60 s (offline?)",
                        channel, time.time() - t0, got)
        await asyncio.sleep(60 if time.time() - t0 < 120 else 5)
