"""Kick chat over Kick's public Pusher websocket (unofficial: the key and the chatroom id come from Kick's website
and can change). Verified 2026-11-17 against a live channel; key found in other open-source Kick clients
(eerimoq/moblin, NOALBS). Chatroom ids are stored in streamers.yaml (`kick_chatroom`): Kick's API answers 403 to Python,
so look one up with `curl -s https://kick.com/api/v2/channels/<slug>` and read chatroom.id. Reconnects with backoff."""
import asyncio
import json
import re
import subprocess
import time

import websockets

from .twitch import _save

PUSHER_KEY = "34bf7a0ff419a2a775b9"
URL = f"wss://ws-us2.pusher.com/app/{PUSHER_KEY}?protocol=7&client=js&version=8.4.0&flash=false"
EMOTE = re.compile(r"\[emote:\d+:([^\]]+)\]")


def lookup_chatroom(slug: str) -> int | None:
    """Chatroom id from Kick's API. curl, not Python: Kick answers 403 to Python's TLS fingerprint."""
    try:
        r = subprocess.run(["curl", "-s", "-m", "15", "-A", "Mozilla/5.0", f"https://kick.com/api/v2/channels/{slug}"],
                           capture_output=True, text=True, timeout=20)
        return int(json.loads(r.stdout)["chatroom"]["id"])
    except Exception:
        return None


def parse(frame: str):
    """One Pusher frame -> (user, text) for a chat message, else None. `data` is JSON inside the JSON."""
    try:
        m = json.loads(frame)
        if m.get("event") != r"App\Events\ChatMessageEvent":
            return None
        d = json.loads(m["data"]) if isinstance(m["data"], str) else m["data"]
        return d["sender"]["username"], EMOTE.sub(r"\1", d["content"]).strip()
    except (ValueError, KeyError, TypeError):
        return None


async def read_one(channel: str, room: int, on_message, log=None, save_chat=True):
    """Forever: join chatrooms.<id>.v2 and call on_message(channel, ts, user, text)."""
    delay = 2
    while True:
        try:
            async with websockets.connect(URL, ping_interval=None) as ws:
                await ws.send(json.dumps({"event": "pusher:subscribe",
                                          "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
                delay = 2
                while True:
                    frame = await asyncio.wait_for(ws.recv(), timeout=180)
                    if '"pusher:ping"' in frame:
                        await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                        continue
                    if '"pusher:error"' in frame:
                        raise ConnectionError(frame[:150])
                    m = parse(frame)
                    if m and m[1]:
                        ts = time.time()
                        if save_chat:
                            _save(f"kick-{channel}", ts, m[0], m[1])
                        on_message(channel, ts, m[0], m[1])
        except Exception as e:
            if log:
                log.warning("%s: Kick chat connection lost (%s: %s); retry in %ds", channel, type(e).__name__,
                            str(e)[:120], delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
