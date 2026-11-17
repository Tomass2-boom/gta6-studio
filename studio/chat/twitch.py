"""Anonymous Twitch chat reader (IRC over TCP, justinfan login, no account). Reconnects by itself."""
import asyncio
import json
import random
import time
from datetime import date

from ..config import DATA

HOST, PORT = "irc.chat.twitch.tv", 6667


def parse(line: str):
    """':nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :text' -> (channel, user, text) or None."""
    if " PRIVMSG #" not in line:
        return None
    try:
        prefix, rest = line.lstrip("@").split(" PRIVMSG #", 1)
        user = prefix.split(":", 1)[1].split("!", 1)[0] if ":" in prefix else ""
        chan, text = rest.split(" :", 1)
        return chan.strip().lower(), user, text.strip()
    except ValueError:
        return None


async def read(channels: list[str], on_message, log=None, save_chat=True):
    """Join all channels and call on_message(channel, ts, user, text) forever."""
    delay = 2
    while True:
        try:
            r, w = await asyncio.open_connection(HOST, PORT)
            w.write(f"NICK justinfan{random.randint(10000, 99999)}\r\n".encode())
            for i in range(0, len(channels), 15):      # stay under the join rate limit
                w.write(("JOIN " + ",".join("#" + c for c in channels[i:i + 15]) + "\r\n").encode())
                await w.drain()
                await asyncio.sleep(1.5)
            delay = 2
            while True:
                raw = await asyncio.wait_for(r.readline(), timeout=360)   # Twitch pings every ~5 min
                if not raw:
                    raise ConnectionError("closed")
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("PING"):
                    w.write(line.replace("PING", "PONG", 1).encode() + b"\r\n")
                    await w.drain()
                    continue
                m = parse(line)
                if m:
                    ts = time.time()
                    if save_chat:
                        _save(m[0], ts, m[1], m[2])
                    on_message(m[0], ts, m[1], m[2])
        except Exception as e:
            if log:
                log.warning("chat connection lost (%s: %s); retry in %ds", type(e).__name__, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def _save(chan, ts, user, text):
    """Keep every chat log (small): useful as test fixtures and for analytics later."""
    d = DATA / "chat"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{chan}-{date.today()}.jsonl", "a") as f:
        f.write(json.dumps({"t": round(ts, 2), "u": user, "m": text}, ensure_ascii=False) + "\n")
