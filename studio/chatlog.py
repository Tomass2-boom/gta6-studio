"""Chat-only capture: log every channel's chat to data/chat/ with no video (almost no bandwidth).

For slow internet: chat is the spike signal, and the video can be fetched later from the VOD
(`studio backfill`). Run: uv run python -m studio.chatlog
"""
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler

from .config import DATA
from .chat import kick, status, twitch, youtube
from .ingest.live import stream_url
from .live import guard, resolve_channels


async def _kick(e, lg, noop):
    """Kick needs the chatroom id first. Looked up inside the task and retried, so a boot without internet
    (power cut, router still starting) does not leave the channel unlogged until the next restart."""
    room = e.get("kick_chatroom")
    while not room:
        room = await asyncio.to_thread(kick.lookup_chatroom, e["channel"])
        if not room:
            lg.warning("%s: no Kick chatroom id yet (offline?), retry in 60 s", e["channel"])
            await asyncio.sleep(60)
    await kick.read_one(e["channel"], int(room), noop, lg)


async def _status_writer():
    while True:
        try:
            status.write()
        except OSError:
            pass
        await asyncio.sleep(10)


def main():
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    # one rotating file (14 days, a new file at midnight): a node that runs for weeks must not grow one log forever
    h = TimedRotatingFileHandler(DATA / "logs" / "chatlog.log", when="midnight", backupCount=14, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(handlers=[h], level=logging.INFO)
    lg = logging.getLogger("chatlog")
    chans = resolve_channels(None, lg)
    noop = lambda *a: None  # noqa: E731
    tasks = [_status_writer()]
    tw = [e["channel"] for e in chans if e["platform"] == "twitch"]
    if tw:
        tasks.append(guard("twitch", lambda: twitch.read(tw, noop, lg), lg))
    for e in chans:
        if e["platform"] == "youtube":
            tasks.append(guard(e["channel"], lambda e=e: youtube.read_one(e["channel"], stream_url("youtube", e["channel"]), noop, lg), lg))
        elif e["platform"] == "kick":
            tasks.append(guard(e["channel"], lambda e=e: _kick(e, lg, noop), lg))
    lg.info("chat-only logging: %s", ", ".join(f"{e['platform']}:{e['channel']}" for e in chans))

    async def run():
        await asyncio.gather(*tasks)
    asyncio.run(run())


if __name__ == "__main__":
    main()
