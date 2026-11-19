"""Windows capture node: config merge, chat status, supervisor back-off / hang handling, housekeeping, Windows-safe IO."""
import json
import os
import subprocess
import sys
import time
from datetime import date

import pytest

from studio import config
from studio.chat import status, twitch
from studio.node import proc, supervisor as sv


@pytest.fixture
def node_dirs(tmp_path, monkeypatch):
    status._ok.clear()
    monkeypatch.setattr(sv, "DATA", tmp_path)
    monkeypatch.setattr(sv, "OUT", tmp_path / "outbox")
    monkeypatch.setattr(sv, "APP", tmp_path / "app")
    monkeypatch.setattr(sv, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(sv, "RAW", tmp_path / "raw")
    monkeypatch.setattr(status, "FILE", tmp_path / "app" / "chat_ok.json")
    return tmp_path


def test_local_config_is_deep_merged(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("render: {ffmpeg: ffmpeg, ass: 1}\nlive: {quality: best}\n")
    (tmp_path / "config" / "settings.local.yaml").write_text("render: {ffmpeg: 'C:/x/ffmpeg.exe'}\nnode: {enabled: true}\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    c = config.load("settings")
    assert c["render"] == {"ffmpeg": "C:/x/ffmpeg.exe", "ass": 1}      # nested keys merged, not replaced
    assert c["live"]["quality"] == "best" and c["node"]["enabled"] is True


def test_chat_status_goes_stale(node_dirs):
    status.mark(["a", "b"], True)
    status.mark("b", False)
    status.write()
    assert status.read() == ["a"]
    d = json.loads(status.FILE.read_text())
    d["t"] -= 120
    status.FILE.write_text(json.dumps(d))
    assert status.read(90) is None                  # chatlog hung or dead: nothing counts as covered


def test_chat_lines_are_utf8_with_unix_newlines(node_dirs, monkeypatch):
    monkeypatch.setattr(twitch, "DATA", node_dirs)
    twitch._save("chan", 1.0, "u", "gta 6 🔥 hype")           # cp1252 (the Windows default) cannot encode this
    raw = (node_dirs / "chat" / f"chan-{date.today()}.jsonl").read_bytes()
    assert b"\r\n" not in raw and "🔥".encode() in raw
    assert json.loads(raw)["m"] == "gta 6 🔥 hype"


def test_backoff_grows_caps_and_resets():
    c = sv.Child("x", ["x"])
    for i in range(1, 12):
        c._failed(100.0 + i, "boom")                # quick deaths
    assert c.delay() == 300                         # capped at 5 min, never a tight loop
    c.started = 0.0
    c._failed(5000.0, "after a long healthy run")
    assert c.fails == 0 and c.delay() == 5


def test_dead_child_is_restarted_with_backoff(node_dirs):
    c = sv.Child("dies", [sys.executable, "-c", "import sys; sys.exit(3)"], min_delay=1)
    now = 1000.0
    c.tick(now, True)                               # starts
    assert c.p is not None
    c.p.wait(20)
    c.tick(now + 1, True)                           # sees the exit, schedules a restart
    assert c.p is None and c.fails == 1 and c.next_start > now + 1
    c.tick(now + 1.5, True)                         # too early: still waiting
    assert c.p is None
    c.tick(c.next_start + 0.1, True)                # back-off over: restarted
    assert c.p is not None
    c.p.wait(20)


def test_hung_child_is_killed_and_restarted(node_dirs):
    c = sv.Child("hangs", [sys.executable, "-c", "import time; time.sleep(120)"],
                 hung=lambda now, ch: "no heartbeat")
    c.start(1000.0)
    try:
        c.tick(2000.0, True)                        # alive for 1000 s and the check says stuck
        assert c.p is None and c.next_start > 2000.0     # killed, restart scheduled
    finally:
        c.stop()


def test_no_restarts_while_offline(node_dirs):
    c = sv.Child("quiet", [sys.executable, "-c", "import time; time.sleep(120)"], hung=lambda now, ch: "stuck")
    c.start(1000.0)
    try:
        c.tick(2000.0, False)                        # internet down: waiting is the right thing, killing it is not
        assert c.alive()
    finally:
        c.stop()


def test_silent_live_channel_is_found(node_dirs):
    now = time.time()
    seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60))
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7200))
    rows = [{"seen": seen, "platform": "twitch", "channel": "live1"},
            {"seen": old, "platform": "twitch", "channel": "gone"},
            {"seen": seen, "platform": "kick", "channel": "vodonly", "live": False},
            {"seen": seen, "error": "x"}]
    vods = node_dirs / "outbox" / "vods.jsonl"
    vods.parent.mkdir()
    vods.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert sv.live_channels(vods, now) == {"live1": "twitch"}
    chat = node_dirs / "chat"
    chat.mkdir()
    f = chat / f"live1-{date.fromtimestamp(now)}.jsonl"
    f.write_text("{}\n")
    os.utime(f, (now - 400, now - 400))
    assert sv.chat_file_age(chat, "live1", "twitch", now) == pytest.approx(400, abs=2)
    assert sv.chat_file_age(chat, "nofile", "twitch", now) is None


def test_chatlog_hung_check(node_dirs):
    check = sv.chatlog_hung(300)
    assert "stale" in check(time.time(), None)      # no status file: event loop stuck or dead
    status.mark("x", True)
    status.write()
    assert check(time.time(), None) is None         # fresh status, nobody live: fine


def test_housekeeping_retention_and_budget(node_dirs):
    now = time.time()
    c = {**sv.DEFAULTS, "raw_keep_h": 3, "raw_budget_gb": 0.000003, "outbox_keep_days": 7, "log_keep_days": 14}

    def mk(p, age_s, size=1000):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        os.utime(p, (now - age_s, now - age_s))
        return p
    raw_old = mk(node_dirs / "raw" / "a" / "old.ts", 5 * 3600)       # beyond raw_keep_h
    raw_a = mk(node_dirs / "raw" / "a" / "1.ts", 2000)
    raw_b = mk(node_dirs / "raw" / "a" / "2.ts", 1500)
    raw_c = mk(node_dirs / "raw" / "a" / "3.ts", 1000)
    raw_new = mk(node_dirs / "raw" / "a" / "4.ts", 5)                # being written: never touched
    out_old = mk(node_dirs / "outbox" / "moments" / "old.mp4", 8 * 86400)
    hb = mk(node_dirs / "outbox" / "heartbeat.json", 8 * 86400)      # contract files are never expired
    log_old = mk(node_dirs / "logs" / "old.log", 20 * 86400)
    gone = sv.housekeeping(c, now)
    assert not raw_old.exists() and not out_old.exists() and not log_old.exists()
    assert hb.exists() and raw_new.exists()
    assert not raw_a.exists()                                        # over the 3000-byte budget: oldest goes first
    assert gone["raw"] >= 2 and gone["outbox"] == 1 and gone["logs"] == 1


def test_offline_minute_is_a_hole_in_coverage(node_dirs):
    n = sv.Node({**sv.DEFAULTS, "syncthing": False, "extra_path": []})
    status.mark(["a"], True)
    status.write()
    n.child("chatlog").p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        now = 1_800_000_000
        n.write_status(now, True)
        n.write_status(now + 60, False)
        lines = [json.loads(x) for x in (node_dirs / "outbox" / f"coverage-{date.fromtimestamp(now)}.jsonl").read_text().splitlines()]
        assert lines[0]["chat_ok"] == ["a"] and lines[1] == {"t": now + 60, "chat_ok": [], "recording": []}
        hb = json.loads((node_dirs / "outbox" / "heartbeat.json").read_text())
        assert hb["node"] == "win" and hb["chat_ok"] == [] and "disk_free_gb" in hb and "git" in hb
    finally:
        n.child("chatlog").p.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /T is the Windows way")
def test_kill_tree_kills_grandchildren(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    code = ("import subprocess,sys,time\n"
            f"g=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
            f"open(r'{pidfile}','w').write(str(g.pid)); time.sleep(120)")
    p = subprocess.Popen([sys.executable, "-c", code])
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.1)
    gpid = int(pidfile.read_text())
    proc.kill_tree(p.pid)
    p.wait(10)
    time.sleep(1)
    out = subprocess.run(["tasklist", "/FI", f"PID eq {gpid}"], capture_output=True, text=True).stdout
    assert str(gpid) not in out


@pytest.mark.parametrize("mod", ["studio.chatlog", "studio.vodwatch", "studio.node", "studio.node.supervisor",
                                 "studio.node.health", "studio.chat.youtube", "studio.chat.kick"])
def test_every_node_module_imports(mod):
    """The supervisor starts these as separate processes; a syntax error only shows as 'exited with code 1'."""
    import importlib
    importlib.import_module(mod)


def test_vodwatch_writes_to_the_outbox_on_the_node(tmp_path, monkeypatch):
    from studio import vodwatch
    monkeypatch.setattr(vodwatch, "DATA", tmp_path)
    monkeypatch.setattr(vodwatch, "load", lambda name: {"node": {"enabled": True}})
    assert vodwatch.out_file() == tmp_path / "outbox" / "vods.jsonl"
    monkeypatch.setattr(vodwatch, "load", lambda name: {})
    assert vodwatch.out_file() == tmp_path / "vods.jsonl"


# ------------------------------------------------------------------------------------------- phase 2: capture
def test_chunk_names_epoch_and_windows_local_time(tmp_path, monkeypatch):
    from studio.ingest import live as vid
    assert vid.chunk_time("1795000000") == 1795000000.0
    t = time.mktime(time.strptime("20261119213811", "%Y%m%d%H%M%S"))
    assert vid.chunk_time("20261119213811") == t
    assert vid.chunk_time("abc") is None and vid.chunk_time("2026111921") == 2026111921.0
    d = tmp_path / "chan"
    d.mkdir()
    for n in ("20261119213811", "20261119213821", "20261119213831"):
        (d / f"{n}.ts").write_bytes(b"x")
    monkeypatch.setattr(vid, "RAW", tmp_path)
    got = vid.chunks("chan", t - 5, t + 100, 10)
    assert [round(x - t) for x, _ in got] in ([0, 10], [0, 10, 20])      # the newest chunk may still be written


def test_chat_tail_skips_backlog_reads_new_lines_and_new_days(tmp_path):
    from studio.node.capture import ChatTail, chat_path
    got = []
    chans = {"a": {"platform": "twitch"}, "b": {"platform": "kick"}}
    day = date(2026, 11, 19)
    pa = chat_path(tmp_path, "twitch", "a", day)
    pa.write_text(json.dumps({"t": 1, "u": "old", "m": "history"}) + "\n", encoding="utf-8")
    tail = ChatTail(chans, tmp_path, lambda ch, t, u, m: got.append((ch, t, u, m)))
    assert tail.poll(day) == 0 and got == []                      # backlog at startup is not a live burst
    with open(pa, "ab") as f:
        f.write((json.dumps({"t": 2, "u": "u1", "m": "GTA 🔥"}, ensure_ascii=False) + "\n").encode())
        f.write(b'{"t": 3, "u": "half')                           # a line still being written
    assert tail.poll(day) == 1 and got == [("a", 2.0, "u1", "GTA 🔥")]
    with open(pa, "ab") as f:
        f.write(b'", "m": "done"}\n')
    assert tail.poll(day) == 1 and got[-1][3] == "done"
    pb = chat_path(tmp_path, "kick", "b", day)                    # a channel's file born after startup: from its start
    pb.write_text(json.dumps({"t": 5, "u": "k", "m": "hi"}) + "\n")
    assert tail.poll(day) == 1 and got[-1][0] == "b"
    nxt = date(2026, 11, 20)
    chat_path(tmp_path, "twitch", "a", nxt).write_text(json.dumps({"t": 9, "u": "n", "m": "new day"}) + "\n")
    assert tail.poll(nxt) == 1 and got[-1][3] == "new day"        # midnight rollover


def test_choose_keeps_slots_and_fills_up():
    from studio.node.capture import choose
    assert choose(set(), ["a", "b", "c", "d"], 2) == {"a", "b"}
    assert choose({"b", "c"}, ["a", "b", "c", "d"], 2) == {"b", "c"}     # incumbents inside the slack keep their slot
    assert choose({"f"}, list("abcdef"), 2) == {"a", "b"}                # fell far below the cut: replaced
    assert choose({"x"}, ["a"], 3) == {"a"}                              # fewer live than slots


def test_publish_writes_part_then_mp4_then_json_last(tmp_path, monkeypatch):
    import logging
    from studio.node import capture as cap
    monkeypatch.setattr(cap, "OUT", tmp_path / "outbox")
    monkeypatch.setattr(cap, "APP", tmp_path / "app")
    c = cap.Capture.__new__(cap.Capture)
    c.node, c.moments = {"name": "win"}, tmp_path / "outbox" / "moments"
    c.moments.mkdir(parents=True)
    c.L = {"quality": "720p60,best"}
    c.chans = {"ch1": {"name": "Ch One", "platform": "twitch", "quality": None}}
    src = tmp_path / "cut.mp4"
    src.write_bytes(b"video")
    c.publish("ch1", c.chans["ch1"], src, 1_800_000_000.0, 100.0, 1_800_000_050.7, {"chat": 5.0, "meta": {}}, 15.0, 720)
    mp4, js = c.moments / "win-ch1-1800000050.mp4", c.moments / "win-ch1-1800000050.json"
    assert mp4.read_bytes() == b"video" and not list(c.moments.glob("*.part")) and not src.exists()
    d = json.loads(js.read_text())
    assert set(d) == {"id", "node", "streamer", "channel", "platform", "url", "stream_url", "t_start", "t_end", "spike_t",
                      "signals", "signal_score", "quality", "height", "created"}
    assert d["id"] == "win-ch1-1800000050" and d["spike_t"] == 1_800_000_050 and d["t_end"] == d["t_start"] + 100
    assert d["quality"] == "720p60,best" and d["height"] == 720
