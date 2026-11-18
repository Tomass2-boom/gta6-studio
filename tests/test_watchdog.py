import json
import time

import studio.watchdog as W


def setup(monkeypatch, tmp_path, pids):
    monkeypatch.setattr(W, "APP", tmp_path)
    monkeypatch.setattr(W, "log", lambda m: None)
    monkeypatch.setattr(W, "live_pids", lambda: list(pids))
    started = []
    monkeypatch.setattr(W, "start_live", lambda: started.append(1))
    jobs = []
    monkeypatch.setattr(W, "start_job", lambda n, c, l: jobs.append(n))
    monkeypatch.setattr(W.runner, "stop", lambda *a, **k: True)
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    return started, jobs


def test_crash_restarts_live(monkeypatch, tmp_path):
    started, _ = setup(monkeypatch, tmp_path, [])
    W.Watchdog().tick()
    assert started == [1]


def test_wanted_stop_is_left_alone(monkeypatch, tmp_path):
    started, _ = setup(monkeypatch, tmp_path, [])
    (tmp_path / "live.stopped").write_text("x")
    W.Watchdog().tick()
    assert started == []


def test_crash_loop_turns_on_chat_only_fallback(monkeypatch, tmp_path):
    started, jobs = setup(monkeypatch, tmp_path, [])
    w = W.Watchdog()
    for _ in range(5):
        w.tick()
        w.last_try = 0 if not w.fallback else w.last_try   # simulate time passing before the fallback
    assert w.fallback and "chatlog" in jobs
    n = len(started)
    w.tick()                        # inside the 5-min retry window: no new start
    assert len(started) == n


def test_gap_is_recorded_and_filled_when_live_is_back(monkeypatch, tmp_path):
    pids = []
    started, jobs = setup(monkeypatch, tmp_path, pids)
    monkeypatch.setattr(W, "hung", lambda s: None)
    w = W.Watchdog()
    w.tick()                        # down
    pids.append(123)
    w.tick()                        # back, just now
    w.pid_since -= 400              # healthy for a while
    w.tick()
    w.tick()
    gaps = [json.loads(l) for l in (tmp_path / "gaps.jsonl").read_text().splitlines()]
    assert len(gaps) == 1 and "fillgaps" in jobs


def test_hung_main_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "APP", tmp_path)
    (tmp_path / "loop.beat").write_text(json.dumps({"t": time.time() - 1000}))
    assert W.hung(time.time() - 2000)
    (tmp_path / "loop.beat").write_text(json.dumps({"t": time.time()}))
    assert W.hung(time.time() - 2000) is None


def test_stuck_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "APP", tmp_path)
    (tmp_path / "loop.beat").write_text(json.dumps({"t": time.time()}))
    (tmp_path / "worker.beat").write_text(json.dumps({"moment": 7, "since": time.time() - 2000}))
    assert "moment 7" in W.hung(time.time() - 3000)
    (tmp_path / "worker.beat").write_text(json.dumps({"moment": None, "since": time.time() - 2000}))
    assert W.hung(time.time() - 3000) is None
