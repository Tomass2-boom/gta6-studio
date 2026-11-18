"""Background processes the control app starts: `studio live` (one at a time) and short jobs
(doctor, vod, render). Each runs in its own process group with its output in data/logs/, and a
small JSON file in data/app/ remembers it, so closing the app window never stops clipping and a
reopened app finds the run again.

Stop sends SIGINT to the whole group, exactly like Ctrl-C in Terminal: `live.run` then stops its
recorders in its `finally`. SIGTERM would skip that and leave streamlink/ffmpeg running.
"""
import json
import os
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from ..config import DATA, ROOT

# A Finder-started app gets only /usr/bin:/bin:..., so uv, streamlink, yt-dlp, ollama would be missing.
EXTRA_PATH = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", str(Path.home() / ".local/bin")]
LIVE_CMD = ["caffeinate", "-i", "uv", "run", "studio", "live"]
STOP_WAIT_S = 25


def env() -> dict:
    e = dict(os.environ)
    have = e.get("PATH", "").split(":")
    e["PATH"] = ":".join([p for p in EXTRA_PATH if p not in have] + [p for p in have if p])
    e["PYTHONUNBUFFERED"] = "1"
    return e


def state_dir() -> Path:
    d = DATA / "app"
    d.mkdir(parents=True, exist_ok=True)
    return d


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a finished child of ours stays a zombie until reaped
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def _default_sigint():
    """A process started from a background shell inherits 'ignore Ctrl-C'; then Stop could not
    reach `studio live`. Give the child the normal Ctrl-C behaviour back."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def descendants(pid: int) -> list[int]:
    """All child processes under `pid` (the recorders live in their own process groups)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return []
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        a, b = line.split()
        kids.setdefault(int(b), []).append(int(a))
    found, todo = [], [pid]
    while todo:
        for k in kids.get(todo.pop(), []):
            found.append(k)
            todo.append(k)
    return found


_procs: dict[str, subprocess.Popen] = {}
_stopping: set[str] = set()
_lock = threading.Lock()


def _info_path(name: str) -> Path:
    return state_dir() / f"{name}.json"


def info(name: str) -> dict | None:
    p = _info_path(name)
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def start(name: str, cmd: list[str], label: str = "") -> dict:
    """Start `cmd` in its own process group; output goes to data/logs/<date>-app-<name>.log."""
    with _lock:
        cur = info(name)
        if cur and alive(cur.get("pid")):
            raise RuntimeError(f"{label or name} is already running")
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        log = DATA / "logs" / f"{datetime.now():%Y-%m-%d}-app-{name}.log"
        out = open(log, "a")
        out.write(f"\n===== {datetime.now():%H:%M:%S} {' '.join(cmd)}\n")
        out.flush()
        p = subprocess.Popen(cmd, cwd=ROOT, env=env(), stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True, preexec_fn=_default_sigint)
        out.close()
        _procs[name] = p
        d = {"name": name, "pid": p.pid, "cmd": cmd, "label": label, "log": str(log),
             "started": time.time(), "exit": None}
        _info_path(name).write_text(json.dumps(d))
        return d


def status(name: str) -> dict:
    """{running, pid, started, exit, stopping, log, label} for a job we started."""
    d = info(name) or {"name": name}
    p = _procs.get(name)
    if p is not None and p.pid == d.get("pid"):
        code = p.poll()
        running = code is None
        if not running and d.get("exit") is None:
            d["exit"] = code
            _info_path(name).write_text(json.dumps(d))
    else:
        running = alive(d.get("pid"))
    d["running"] = running
    d["stopping"] = running and name in _stopping
    if not running:
        _stopping.discard(name)
    return d


def _signal_group(pid: int, sig: int):
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop(name: str, pid: int | None = None, wait_s: float = STOP_WAIT_S) -> bool:
    """Ctrl-C the job's process group, give it `wait_s` to finish, then SIGINT again, then SIGTERM.
    Returns at once; the waiting happens in a thread."""
    pid = pid or (info(name) or {}).get("pid")
    if not alive(pid):
        return False
    _stopping.add(name)
    _signal_group(pid, signal.SIGINT)

    def escalate():
        for sig in (signal.SIGINT, signal.SIGTERM):
            t0 = time.time()
            while time.time() - t0 < wait_s:
                if not alive(pid):
                    return
                time.sleep(0.5)
            if sig == signal.SIGTERM:  # forced: stop the recorders too, or they keep running orphaned
                for k in descendants(pid):
                    _signal_group(k, sig)
            _signal_group(pid, sig)
    threading.Thread(target=escalate, daemon=True).start()
    return True


def outside_live_pids() -> list[int]:
    """`studio live` runs started from Terminal (not by this app)."""
    try:
        out = subprocess.run(["pgrep", "-f", r"bin/studio live|studio\.cli live"], capture_output=True,
                             text=True).stdout
    except FileNotFoundError:
        return []
    mine = {(info("live") or {}).get("pid"), os.getpid()}
    return [int(x) for x in out.split() if x.strip() and int(x) not in mine]


def live_status() -> dict:
    d = status("live")
    if not d["running"]:
        other = outside_live_pids()
        if other:
            d.update(running=True, outside=True, pid=other[0], started=None)
    return d


def start_live() -> dict:
    if live_status()["running"]:
        raise RuntimeError("Clipping is already running")
    return start("live", LIVE_CMD, "Clipping")


def stop_live() -> bool:
    d = live_status()
    if not d["running"]:
        return False
    return stop("live", d["pid"])
