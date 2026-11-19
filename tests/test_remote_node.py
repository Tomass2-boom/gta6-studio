"""Mac side of the Windows capture node: reading its chat, importing its moments, heartbeat."""
import json
import logging
import time

from studio import backfill as B
from studio import db, importer as I
from studio import watchdog as W


def _chat(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"t": t, "u": u, "m": m}) + "\n" for t, u, m in rows))


def _setup(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    for mod in (B, I):
        monkeypatch.setattr(mod, "REMOTE", remote)
    monkeypatch.setattr(B, "DATA", tmp_path)
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(W, "DATA", tmp_path)
    monkeypatch.setattr(I.vid, "SEG", tmp_path / "segments")
    monkeypatch.setattr(I.vid, "duration", lambda p: 70.0)
    monkeypatch.setattr(I, "load", lambda name: {"live": {"max_window_s": 100}, "remote": {"import_delay_s": 600}})
    return remote


def _pair(remote, ch, spike, score=20.0, created=None, platform="twitch"):
    d = remote / "win" / "outbox" / "moments"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"win-{ch}-{spike}"
    (d / f"{stem}.mp4").write_bytes(b"x" * 100)
    (d / f"{stem}.json").write_text(json.dumps({
        "id": stem, "node": "win", "streamer": ch.title(), "channel": ch, "platform": platform,
        "url": f"https://twitch.tv/{ch}#live={spike}", "stream_url": f"https://twitch.tv/{ch}",
        "t_start": spike - 40, "t_end": spike + 30, "spike_t": spike,
        "signals": {"meta": {"tier": "A", "node": "win"}, "chat": 2.0}, "signal_score": score,
        "quality": "720p60", "height": 720, "created": created or spike + 31}))
    return stem


def test_chat_from_both_machines_counts_once(tmp_path, monkeypatch):
    remote = _setup(tmp_path, monkeypatch)
    _chat(tmp_path / "chat" / "lacy-2026-11-19.jsonl", [(100.0, "a", "LOL"), (101.0, "b", "W")])
    # Windows got the same messages a little later, plus one the Mac missed (Mac asleep)
    _chat(remote / "win" / "chat" / "lacy-2026-11-19.jsonl", [(100.4, "a", "LOL"), (101.6, "b", "W"), (500.0, "c", "omg")])
    assert B.read_chat("lacy", "twitch", 0, 1000) == [(100.0, "a", "LOL"), (101.0, "b", "W"), (500.0, "c", "omg")]


def test_same_user_repeating_later_is_kept(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _chat(tmp_path / "chat" / "lacy-2026-11-19.jsonl", [(100.0, "a", "LOL"), (140.0, "a", "LOL")])
    assert len(B.read_chat("lacy", "twitch", 0, 1000)) == 2


def test_backfill_skips_moments_the_node_already_has(tmp_path, monkeypatch):
    remote = _setup(tmp_path, monkeypatch)
    _pair(remote, "lacy", 1795121270)
    con = db.connect()
    assert (1795121270.0, 1795121270.0) in B.done_ranges(con, "twitch", "lacy")
    assert B.done_ranges(con, "twitch", "jynxzi") == []


def test_streams_reads_node_vod_list(tmp_path, monkeypatch):
    remote = _setup(tmp_path, monkeypatch)
    p = remote / "win" / "outbox" / "vods.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"platform": "twitch", "channel": "Lacy", "start": "2026-11-19T09:00:00Z",
                             "vod": "https://www.twitch.tv/videos/1"}) + "\n{broken")
    assert B.streams()["lacy"][0]["vod"].endswith("/1")


def test_import_adds_once_best_first(tmp_path, monkeypatch):
    remote = _setup(tmp_path, monkeypatch)
    _pair(remote, "lacy", 1795121270, score=20)
    _pair(remote, "n3on", 1795121382, score=35, platform="kick")
    (remote / "win" / "outbox" / "moments" / "win-xqc-1795121999.json").write_text("{}")   # video not synced yet
    con = db.connect()
    lg = logging.getLogger("t")
    assert I.scan(con, lg, live_running=False) == 2
    assert I.scan(con, lg, live_running=False) == 0          # never twice
    first = I.next_moment(con, set())
    assert con.execute("SELECT s.streamer FROM moments m JOIN sources s ON s.id=m.source_id WHERE m.id=?",
                       (first["id"],)).fetchone()[0].lower() == "n3on"   # strongest first
    sig = json.loads(con.execute("SELECT signals FROM moments WHERE id=?", (first["id"],)).fetchone()[0])
    assert sig["meta"]["node"] == "win" and sig["meta"]["video_from"] == 1795121382 - 40
    assert (tmp_path / "segments" / "win-n3on-1795121382.mp4").exists()


def test_import_skips_what_the_mac_already_cut(tmp_path, monkeypatch):
    remote = _setup(tmp_path, monkeypatch)
    _pair(remote, "lacy", 1795121270)
    con = db.connect()
    sid = con.execute("INSERT INTO sources(url,platform,streamer) VALUES(?,?,?)",
                      ("https://twitch.tv/lacy#live=1795121262", "twitch", "Lacy")).lastrowid
    con.execute("INSERT INTO moments(source_id,t_start,t_end) VALUES(?,0,70)", (sid,))
    con.commit()
    assert I.scan(con, logging.getLogger("t"), live_running=False) == 0
    row = con.execute("SELECT state, fail_reason FROM moments ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "failed" and "duplicate" in row[1]


def test_import_waits_for_mac_live_cut(tmp_path, monkeypatch):
    remote = _setup(tmp_path, monkeypatch)
    now = time.time()
    _pair(remote, "lacy", int(now) - 60, created=now - 30)
    con = db.connect()
    assert I.scan(con, logging.getLogger("t"), live_running=True) == 0
    assert I.scan(con, logging.getLogger("t"), live_running=False) == 1


def test_node_heartbeat(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(W, "DATA", tmp_path)
    hb = tmp_path / "remote" / "win" / "outbox" / "heartbeat.json"
    hb.parent.mkdir(parents=True)
    hb.write_text(json.dumps({"node": "win", "t": time.time() - 30, "chat_ok": ["lacy"]}))
    assert W.node_healthy()
    hb.write_text(json.dumps({"node": "win", "t": time.time() - 900, "chat_ok": ["lacy"]}))
    assert not W.node_healthy()
    hb.write_text(json.dumps({"node": "win", "t": time.time(), "chat_ok": []}))
    assert not W.node_healthy()
