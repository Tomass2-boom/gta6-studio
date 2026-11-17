import json
from pathlib import Path

from studio.chat.twitch import parse
from studio.config import load
from studio.detect.chat import ChatDetector
from studio.ingest.live import contiguous_run

FIX = Path(__file__).parent / "fixtures"


def run(rows, cfg=None):
    d, out = ChatDetector(cfg or load("settings")["live"]["chat"]), []
    for r in rows:
        out += d.feed("chan", r["t"], r["m"])
    out += d.tick(rows[-1]["t"] + 60)
    return out


def test_spike_found_once_in_saved_chat():
    rows = [json.loads(x) for x in (FIX / "chat_spike.jsonl").read_text().splitlines()]
    spikes = run(rows)
    t0 = 1794936000
    assert spikes, "burst at t=300 s must fire"
    assert all(t0 + 300 <= s.t <= t0 + 350 for s in spikes), [s.t - t0 for s in spikes]
    assert spikes[0].strength > 3 and spikes[0].hype_share > 0.5


def test_quiet_chat_has_no_spike():
    rows = [json.loads(x) for x in (FIX / "chat_spike.jsonl").read_text().splitlines()]
    assert run([r for r in rows if r["t"] < 1794936000 + 295]) == []


def test_tiny_chat_not_triggered_by_three_messages():
    rows = [{"t": 1794936000 + i * 20, "m": "hi"} for i in range(30)]
    rows += [{"t": 1794936000 + 600 + i, "m": "lol"} for i in range(3)]
    assert run(rows) == []


def test_parse_irc():
    line = ":bob!bob@bob.tmi.twitch.tv PRIVMSG #xqc :LOL clip it"
    assert parse(line) == ("xqc", "bob", "LOL clip it")
    assert parse(":tmi.twitch.tv 001 x :Welcome") is None


def test_ad_gap_splits_recording():
    starts = [0, 10, 20, 30, 100, 110, 120, 130, 140, 150]   # 60 s hole (ad break)
    assert contiguous_run(starts, 125, 10) == (4, 10)
    assert contiguous_run(starts, 32, 10) == (0, 4)
