"""Watchdog: keeps clipping running while nobody is at the computer.

Every 30 s:
- `studio live` gone without a wanted stop (no data/app/live.stopped, which the Stop button / Ctrl-C writes)
  -> kill its orphaned recorders, start it again (through the app's runner, so the app still shows it).
- `studio live` hung (loop heartbeat older than 5 min, or one moment in the worker longer than 25 min)
  -> stop it and start it again.
- crashing over and over (3 starts in 15 min) -> chat-only fallback: `studio.chatlog` saves chat so the
  missed stretch can be clipped later from the VODs; live is retried every 5 min and chatlog stops once
  live has stayed up.
- every downtime is written to data/app/gaps.jsonl; once live is healthy again the stretch is filled
  automatically (`studio fill-gaps`: replay chat from the VODs where chat was not saved, then backfill).
- vodwatch and Ollama are kept running too.
Everything it does goes to data/logs/<date>-watchdog.log AND into the live log, so the app shows it.

Run detached:  nohup caffeinate -i uv run python -m studio.watchdog >/dev/null 2>&1 &
Stop it:       pkill -f studio.watchdog
"""
import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .config import DATA, ROOT
from .review import runner

TICK_S = 30
LOOP_STALE_S = 300
WORKER_STALE_S = 1500
CRASHES_FOR_FALLBACK = 3
CRASH_WINDOW_S = 900
FALLBACK_RETRY_S = 300
HEALTHY_S = 600
SLEEP_GAP_S = 300      # a tick this late means the Mac was asleep (lid shut, bag)
APP = DATA / "app"


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} WATCHDOG {msg}"
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    for name in (f"{datetime.now():%Y-%m-%d}-watchdog.log", f"{datetime.now():%Y-%m-%d}-live.log"):
        try:
            with open(DATA / "logs" / name, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass


def pgrep(pattern: str) -> list[int]:
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip() and int(x) != os.getpid()]


def live_pids() -> list[int]:
    return pgrep(r"bin/studio live|studio\.cli live")


def wanted_stop() -> bool:
    return (APP / "live.stopped").exists()


def beat_age(name: str, key: str) -> tuple[float | None, dict]:
    try:
        d = json.loads((APP / f"{name}.beat").read_text())
        return time.time() - float(d[key]), d
    except Exception:
        return None, {}


def hung(started: float) -> str | None:
    """Why the running live looks hung, or None. Ignores beats older than this run."""
    age, _ = beat_age("loop", "t")
    up = time.time() - started
    if up > LOOP_STALE_S and (age is None or age > LOOP_STALE_S):
        return f"main loop silent for {int(age or up)} s"
    age, d = beat_age("worker", "since")
    if d.get("moment") and age is not None and age > WORKER_STALE_S and time.time() - age >= started - 5:
        return f"moment {d['moment']} stuck in the worker for {int(age / 60)} min"
    return None


def orphan_recorders() -> list[int]:
    """`sh -c streamlink ... | ffmpeg` recorders left behind by a dead live run (parent is now launchd)."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    found = []
    for ln in out.splitlines():
        parts = ln.split(None, 2)
        if len(parts) == 3 and parts[1] == "1" and parts[2].startswith("/bin/sh -c streamlink"):
            found.append(int(parts[0]))
    return found


def kill_group(pid: int, sig=signal.SIGTERM):
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_live(pids: list[int]):
    for p in pids:
        kill_group(p, signal.SIGINT)
    t0 = time.time()
    while time.time() - t0 < 30 and any(runner.alive(p) for p in pids):
        time.sleep(1)
    for p in pids:
        if runner.alive(p):
            for k in runner.descendants(p):
                kill_group(k)
            kill_group(p)
    (APP / "live.stopped").unlink(missing_ok=True)   # our stop, not the user's


def start_live():
    for p in orphan_recorders():
        kill_group(p)
    try:
        runner.start("live", runner.LIVE_CMD, "Clipping")
    except RuntimeError:
        pass


def start_job(name: str, cmd: list[str], label: str):
    if not runner.status(name).get("running"):
        try:
            runner.start(name, cmd, label)
        except RuntimeError:
            pass


def ensure_side_services():
    if not pgrep(r"studio\.vodwatch"):
        log("vodwatch was not running: started it")
        start_job("vodwatch", ["uv", "run", "python", "-m", "studio.vodwatch"], "VOD watcher")
    try:
        import requests
        requests.get("http://localhost:11434/api/tags", timeout=10)
    except Exception:
        log("Ollama not answering: starting `ollama serve`")
        subprocess.Popen(["ollama", "serve"], env=runner.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)


def add_gap(a: float, b: float):
    with open(APP / "gaps.jsonl", "a") as f:
        f.write(json.dumps({"start": a, "end": b, "filled": False,
                            "local": f"{datetime.fromtimestamp(a):%H:%M}-{datetime.fromtimestamp(b):%H:%M}"}) + "\n")


class Watchdog:
    def __init__(self):
        self.starts: list[float] = []        # our restarts
        self.down_since: float | None = None
        self.fallback = False
        self.last_try = 0.0
        self.seen_pid: int | None = None
        self.pid_since = time.time()
        self.gap_pending: tuple[float, float] | None = None
        self.sleep_fill_at: float | None = None

    def slept(self, a: float, b: float):
        """The Mac slept from a to b. If chat was being saved (live or chat-only logging), save the stretch as a gap
        and clip it from replay chat once the network is back."""
        if not (live_pids() or runner.status("chatlog").get("running")):
            return
        add_gap(a, b)
        self.sleep_fill_at = b + 720   # after vodwatch (every 10 min) has seen streams that started meanwhile
        log(f"Mac was asleep {datetime.fromtimestamp(a):%H:%M}-{datetime.fromtimestamp(b):%H:%M}: "
            "saved as a gap, fill-gaps starts in 12 min")

    def sleep_fill(self, now: float):
        if self.sleep_fill_at and now >= self.sleep_fill_at:
            if runner.status("fillgaps").get("running"):
                self.sleep_fill_at = now + 300   # one at a time; try again when it is done
                return
            self.sleep_fill_at = None
            start_job("fillgaps", ["uv", "run", "studio", "fill-gaps"], "Fill missed stretch")
            log("started `studio fill-gaps` for the sleep stretch")

    def tick(self):
        now = time.time()
        pids = live_pids()
        if pids:
            if pids[0] != self.seen_pid:
                self.seen_pid, self.pid_since = pids[0], now
            why = hung(self.pid_since)
            if why:
                log(f"live looks hung ({why}): restarting it")
                self.down_since = self.down_since or now
                stop_live(pids)
                self.restart(now)
                return
            if self.down_since and now - self.pid_since > 120:
                self.gap_pending = (self.down_since, self.pid_since)
                log(f"live is back; downtime {datetime.fromtimestamp(self.down_since):%H:%M}-"
                    f"{datetime.fromtimestamp(self.pid_since):%H:%M} saved for gap fill")
                self.down_since = None
            if self.fallback and now - self.pid_since > HEALTHY_S:
                self.fallback = False
                runner.stop("chatlog")
                log("live stable again: chat-only fallback stopped")
            if self.gap_pending and now - self.pid_since > 180:
                add_gap(*self.gap_pending)
                self.gap_pending = None
                start_job("fillgaps", ["uv", "run", "studio", "fill-gaps"], "Fill missed stretch")
                log("started `studio fill-gaps` to clip the missed stretch")
            return
        if wanted_stop():
            self.down_since = None
            if self.fallback:
                self.fallback = False
                runner.stop("chatlog")
            return
        # live is down and nobody asked for that
        if self.down_since is None:
            self.down_since = now
            log("live is NOT running (crashed?)")
        self.starts = [t for t in self.starts if now - t < CRASH_WINDOW_S]
        if len(self.starts) >= CRASHES_FOR_FALLBACK and not self.fallback:
            self.fallback = True
            log(f"live crashed {len(self.starts)} times in 15 min: chat-only fallback ON (chat saved for backfill), "
                f"retrying live every {FALLBACK_RETRY_S // 60} min")
            start_job("chatlog", ["uv", "run", "python", "-m", "studio.chatlog"], "Chat-only logging")
        if self.fallback and now - self.last_try < FALLBACK_RETRY_S:
            return
        if self.fallback:
            runner.stop("chatlog")   # never two chat writers at once
            time.sleep(5)
        self.restart(now)

    def restart(self, now: float):
        self.starts.append(now)
        self.last_try = now
        start_live()
        log("started `studio live` again")


def main():
    APP.mkdir(parents=True, exist_ok=True)
    pf = APP / "watchdog.pid"
    try:
        other = int(pf.read_text())
        if other != os.getpid() and runner.alive(other):
            print("watchdog already running")
            return
    except (OSError, ValueError):
        pass
    pf.write_text(str(os.getpid()))
    log("watchdog started")
    w = Watchdog()
    last = time.time()
    while True:
        try:
            now = time.time()
            if now - last > SLEEP_GAP_S:
                w.slept(last, now)
            last = now
            w.sleep_fill(now)
            w.tick()
            ensure_side_services()
        except Exception as e:   # the watchdog itself must never die
            log(f"watchdog error: {type(e).__name__}: {e}")
        time.sleep(TICK_S)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
