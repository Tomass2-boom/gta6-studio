"""Chat-only capture: log every channel's chat to data/chat/ with no video (almost no bandwidth).

For slow internet: chat is the spike signal, and the video can be fetched later from the VOD
(`studio backfill`). Run: uv run python -m studio.chatlog
"""
import asyncio
import logging
from datetime import date

from .config import DATA
from .chat import kick, twitch, youtube
from .ingest.live import stream_url
from .live import resolve_channels


def main():
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA / "logs" / f"{date.today()}-chatlog.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    lg = logging.getLogger("chatlog")
    chans = resolve_channels(None, lg)
    noop = lambda *a: None  # noqa: E731
    tasks = []
    tw = [e["channel"] for e in chans if e["platform"] == "twitch"]
    if tw:
        tasks.append(twitch.read(tw, noop, lg))
    for e in chans:
        if e["platform"] == "youtube":
            tasks.append(youtube.read_one(e["channel"], stream_url("youtube", e["channel"]), noop, lg))
        elif e["platform"] == "kick":
            room = e.get("kick_chatroom") or kick.lookup_chatroom(e["channel"])
            if room:
                tasks.append(kick.read_one(e["channel"], int(room), noop, lg))
            else:
                lg.warning("%s: no Kick chatroom id, chat not logged", e["channel"])
    lg.info("chat-only logging: %s", ", ".join(f"{e['platform']}:{e['channel']}" for e in chans))

    async def run():
        await asyncio.gather(*tasks)
    asyncio.run(run())


if __name__ == "__main__":
    main()
