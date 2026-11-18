import asyncio
import logging

from studio.chat.youtube import parse_line
from studio.live import Live


def test_parse_line_survives_odd_shapes():
    for bad in ['{"replayChatItemAction": "oops"}', '[1]', '"x"', 'null', 'not json',
                '{"replayChatItemAction":{"actions":["x"]}}',
                '{"replayChatItemAction":{"actions":[{"addChatItemAction":"y"}]}}']:
        assert parse_line(bad) == []


def test_parse_line_reads_a_real_message():
    line = ('{"replayChatItemAction":{"actions":[{"addChatItemAction":{"item":{"liveChatTextMessageRenderer":'
            '{"message":{"runs":[{"text":"hi"}]},"authorName":{"simpleText":"bob"}}}}}]}}')
    assert parse_line(line) == [("bob", "hi")]


def test_guard_restarts_a_crashing_watcher(monkeypatch):
    calls = []

    async def boom():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("bad")
        raise asyncio.CancelledError

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    obj = Live.__new__(Live)
    obj.lg = logging.getLogger("t")
    try:
        asyncio.run(obj.guard("x", boom))
    except asyncio.CancelledError:
        pass
    assert len(calls) == 3


def test_module_guard_restarts(monkeypatch):
    from studio.live import guard
    n = []

    async def boom():
        n.append(1)
        if len(n) < 2:
            raise FileNotFoundError("part file gone")
        raise asyncio.CancelledError

    async def fast(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", fast)
    try:
        asyncio.run(guard("x", boom, logging.getLogger("t")))
    except asyncio.CancelledError:
        pass
    assert len(n) == 2
