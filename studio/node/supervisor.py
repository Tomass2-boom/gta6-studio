"""Supervisor of the Windows capture node. Runs chatlog + vodwatch (+ Syncthing, + capture in phase 2) and keeps them
alive for weeks with nobody touching the PC.

  * restarts any dead child, and any hung one (chatlog: status file stale, or a live channel silent for stall_s;
    vodwatch: beat file stale), with back-off so a broken child cannot spin in a loop
  * all children live in a Windows Job Object: if this supervisor is killed they die with it, so the restarted
    supervisor never duplicates them (Task Scheduler restarts the supervisor)
  * offline: children are left alone (they reconnect themselves); the minute is written to coverage with chat_ok = []
    so the Mac sees the hole
  * writes outbox/heartbeat.json every 60 s and outbox/coverage-YYYY-MM-DD.jsonl once a minute (contract: section C of
    docs/windows-node.md), and keeps disk use inside its budget

Run: python -m studio.node            (Task Scheduler runs exactly this, from the repo folder)
"""
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..chat import status as chat_status
from ..config import DATA, ROOT, load
from .proc import NO_WINDOW, KillOnCloseJob, kill_tree

OUT = DATA / "outbox"
APP = DATA / "app"
LOGS = DATA / "logs"
RAW = DATA / "raw"
DEFAULTS = {
    "enabled": True, "name": "win",
    "raw_keep_h": 3,            # raw recordings are only needed until a spike is cut
    "raw_budget_gb": 60,        # hard cap for data/raw
    "outbox_keep_days": 7,
    "log_keep_days": 14,
    "min_free_gb": 30,          # below this the oldest raw files go first
    "stall_s": 300,             # a live channel with no new chat line this long = hung reader
    "hang_cooldown_s": 900,     # at most one hung-restart of chatlog per this
    "hb_s": 60,
    "syncthing": True,
    "extra_path": [],           # tool folders for the children's PATH (the boot task runs as SYSTEM, which lacks them)
    "capture": False,           # phase 2: also run the capture-only live mode
    "max_streams": 3,           # Tier A streams recorded at once (W1: 2.4 GHz Wi-Fi, about 19-25 Mbps = 3 x 720p60)
}
log = logging.getLogger("node")


def cfg() -> dict:
    return {**DEFAULTS, **(load("settings").get("node") or {})}


# --------------------------------------------------------------------------------------------- small helpers
def online(timeout: float = 4.0) -> bool:
    """Is there internet? Any one of three independent endpoints answers a TCP connect."""
    for host, port in (("irc.chat.twitch.tv", 6667), ("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), timeout).close()
            return True
        except OSError:
            continue
    return False


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")        # *.tmp and *.part are in outbox/.stignore: never synced
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def git_sha() -> str:
    try:
        # safe.directory: the boot task runs as SYSTEM, git refuses a repo owned by another user without it
        return subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "--short", "HEAD"],
                              cwd=ROOT, capture_output=True, text=True,
                              timeout=10, creationflags=NO_WINDOW).stdout.strip() or "?"
    except Exception:
        return "?"


def disk_free_gb(path: Path = DATA) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return round(shutil.disk_usage(path).free / 1e9, 1)


def live_channels(vods: Path, now: float, max_age_s: float = 900) -> dict[str, str]:
    """{channel: platform} for streams vodwatch saw live in the last max_age_s (it writes a row only while live)."""
    out = {}
    try:
        with open(vods, "rb") as f:
            start = max(0, vods.stat().st_size - 200_000)
            f.seek(start)
            lines = f.read().decode("utf-8", "replace").splitlines()
            lines = lines[1:] if start else lines          # the first line is cut in half when we did not start at 0
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
            seen = datetime.strptime(r["seen"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=None)
        except (ValueError, KeyError, TypeError):
            continue
        age = now - (seen - datetime(1970, 1, 1)).total_seconds()
        if "channel" in r and age <= max_age_s and r.get("live", True) is not False:
            out[r["channel"]] = r["platform"]
    return out


def chat_file_age(chat: Path, channel: str, platform: str, now: float) -> float | None:
    """Seconds since the channel's chat file today was last written; None if there is no file today."""
    prefix = {"twitch": "", "kick": "kick-", "youtube": "yt-"}[platform]
    p = chat / f"{prefix}{channel}-{date.fromtimestamp(now)}.jsonl"
    try:
        return now - p.stat().st_mtime
    except OSError:
        return None


# --------------------------------------------------------------------------------------------- children
class Child:
    """One supervised process. `hung(now)` returns a reason string if it is alive but stuck."""

    def __init__(self, name, cmd, env=None, hung=None, job=None, min_delay=5, max_delay=300):
        self.name, self.cmd, self.env, self._hung, self.job = name, cmd, env, hung, job
        self.p = None
        self.started = 0.0
        self.fails = 0
        self.next_start = 0.0
        self.last_hang_restart = 0.0
        self.min_delay, self.max_delay = min_delay, max_delay

    def delay(self) -> float:
        return min(self.min_delay * 2 ** self.fails, self.max_delay)

    def start(self, now):
        LOGS.mkdir(parents=True, exist_ok=True)
        err = LOGS / f"{self.name}.err"
        if err.exists() and err.stat().st_size > 2_000_000:
            err.unlink()
        self.p = subprocess.Popen(self.cmd, cwd=ROOT, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=open(err, "ab"), creationflags=NO_WINDOW)
        if self.job:
            self.job.add(self.p)
        self.started = now
        log.info("%s started (pid %s)", self.name, self.p.pid)

    def stop(self):
        if self.p and self.p.poll() is None:
            kill_tree(self.p.pid)
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                pass

    def alive(self) -> bool:
        return bool(self.p) and self.p.poll() is None

    def _failed(self, now, why):
        # a child that ran 10+ min was healthy: forget older failures; a quick death doubles the wait (5 s .. 5 min)
        self.fails = 0 if now - self.started > 600 else self.fails + 1
        self.next_start = now + self.delay()
        log.warning("%s %s; restart in %.0f s (failure #%d)", self.name, why, self.delay(), self.fails)

    def tick(self, now, is_online):
        if self.p is None:
            if now >= self.next_start:
                self.start(now)
            return
        if self.p.poll() is not None:
            code, self.p = self.p.returncode, None
            self._failed(now, f"exited with code {code}")
            return
        if is_online and self._hung and now - self.started > 120 and now - self.last_hang_restart > 900:
            why = self._hung(now, self)
            if why:
                self.last_hang_restart = now
                self.stop()
                self.p = None
                self._failed(now, f"hung ({why}); killed")


def chatlog_hung(stall_s):
    def check(now, child):
        if chat_status.read(90) is None:
            return "chat status file stale: event loop stuck"
        chat = DATA / "chat"
        for ch, plat in live_channels(OUT / "vods.jsonl", now).items():
            age = chat_file_age(chat, ch, plat, now)
            if age is not None and age > stall_s:       # has had chat today, live now, silent for stall_s
                return f"{plat}:{ch} live but no chat line for {age:.0f} s"
        return None
    return check


def vodwatch_hung(now, child):
    try:
        age = now - float((APP / "vodwatch.beat").read_text())
    except (OSError, ValueError):
        return None if now - child.started < 300 else "no beat file"
    return f"beat {age:.0f} s old" if age > 1800 else None


def capture_hung(now, child):
    try:
        age = now - float((APP / "capture.beat").read_text())
    except (OSError, ValueError):
        return None if now - child.started < 300 else "no beat file"
    return f"beat {age:.0f} s old" if age > 300 else None


# --------------------------------------------------------------------------------------------- housekeeping
def _files(root: Path):
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def housekeeping(c: dict, now: float | None = None, aggressive: bool = False) -> dict:
    """Disk budget, retention and log rotation. Returns what it deleted (for the log and the tests).
    aggressive (or free disk below min_free_gb): halve what is left of the raw recordings, oldest first."""
    now = now or time.time()
    gone = {"raw": 0, "outbox": 0, "logs": 0}

    def rm(p, key):
        try:
            p.unlink()
            gone[key] += 1
        except OSError:
            pass

    raw = sorted(_files(RAW), key=lambda p: p.stat().st_mtime)
    for p in raw[:]:
        if p.stat().st_mtime < now - c["raw_keep_h"] * 3600:
            rm(p, "raw")
            raw.remove(p)
    size = sum(p.stat().st_size for p in raw)
    limit = c["raw_budget_gb"] * 1e9
    if aggressive or disk_free_gb() < c["min_free_gb"]:
        limit = min(limit, size * 0.5)
        log.warning("disk low (%.1f GB free) or forced: trimming raw to %.1f GB", disk_free_gb(), limit / 1e9)
    while raw and size > limit:                       # oldest first; never the file being written right now
        p = raw.pop(0)
        if p.stat().st_mtime > now - 60:
            break
        size -= p.stat().st_size
        rm(p, "raw")
    keep = {"heartbeat.json", "vods.jsonl", ".stignore"}
    for p in _files(OUT):
        if p.name not in keep and p.stat().st_mtime < now - c["outbox_keep_days"] * 86400:
            rm(p, "outbox")
    for p in _files(LOGS):
        if p.stat().st_mtime < now - c["log_keep_days"] * 86400:
            rm(p, "logs")
    # vods.jsonl only grows: once it is big, keep the last outbox_keep_days days
    v = OUT / "vods.jsonl"
    if v.exists() and v.stat().st_size > 5_000_000:
        cut = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - c["outbox_keep_days"] * 86400))
        lines = []
        for ln in v.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                if json.loads(ln).get("seen", "") >= cut:
                    lines.append(ln)
            except ValueError:
                pass
        atomic_write(v, "\n".join(lines) + "\n")
    if any(gone.values()):
        log.info("housekeeping removed %s", gone)
    return gone


# --------------------------------------------------------------------------------------------- the node
def recording_now() -> list[str]:
    try:
        d = json.loads((APP / "recording.json").read_text(encoding="utf-8"))
        return d["channels"] if time.time() - d["t"] <= 90 else []
    except (OSError, ValueError, KeyError):
        return []


def acquire_lock():
    """One supervisor per PC: the boot task, the repeat trigger and the health check may all try to start one."""
    APP.mkdir(parents=True, exist_ok=True)
    f = open(APP / "node.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


class Node:
    def __init__(self, c: dict | None = None):
        self.c = c or cfg()
        self.job = KillOnCloseJob()
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        extra = [str(Path(p)) for p in self.c["extra_path"]]
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        os.environ["PATH"] = env["PATH"]        # the supervisor itself calls git / curl too
        self.env = env
        py = sys.executable
        self.children = [
            Child("chatlog", [py, "-m", "studio.chatlog"], env, chatlog_hung(self.c["stall_s"]), self.job),
            Child("vodwatch", [py, "-m", "studio.vodwatch"], env, vodwatch_hung, self.job),
        ]
        st = self.syncthing_cmd()
        if st:
            self.children.append(Child("syncthing", st, env, None, self.job))
        if self.c["capture"]:
            self.children.append(Child("capture", [py, "-m", "studio.node.capture"], env, capture_hung, self.job))
        self.sha = git_sha()
        self.stop_flag = False
        self.was_online = True
        self.last_hb = self.last_hk = 0.0

    def syncthing_cmd(self):
        if not self.c["syncthing"]:
            return None
        exe = self.c.get("syncthing_bin") or shutil.which("syncthing", path=self.env["PATH"])
        if not exe:
            log.warning("syncthing not found: not started")
            return None
        home = self.c.get("syncthing_home") or str(DATA / "syncthing")
        return [exe, "serve", "--no-browser", "--no-restart", "--no-upgrade", "--home", home,
                "--gui-address", "127.0.0.1:8384"]

    def child(self, name) -> Child | None:
        return next((x for x in self.children if x.name == name), None)

    def snapshot(self, is_online: bool) -> dict:
        chat = self.child("chatlog")
        ok = (chat_status.read(90) or []) if is_online and chat and chat.alive() else []
        return {"chat_ok": sorted(ok), "recording": recording_now() if is_online else []}

    def write_status(self, now, is_online):
        snap = self.snapshot(is_online)
        atomic_write(OUT / "heartbeat.json", json.dumps({
            "node": self.c["name"], "t": int(now), "git": self.sha, **snap, "disk_free_gb": disk_free_gb()}))
        with open(OUT / f"coverage-{date.fromtimestamp(now)}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"t": int(now), **snap}) + "\n")

    def run(self):
        OUT.mkdir(parents=True, exist_ok=True)
        stig = OUT / ".stignore"
        if not stig.exists():
            stig.write_text("*.part\n.*.tmp\n", encoding="utf-8")
        for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGTERM)):
            signal.signal(s, lambda *a: setattr(self, "stop_flag", True))
        log.info("node supervisor up: git %s, children %s", self.sha, [x.name for x in self.children])
        is_online, last_net = True, 0.0
        while not self.stop_flag:
            now = time.time()
            try:
                if now - last_net >= 10:
                    is_online, last_net = online(), now
                    if is_online != self.was_online:
                        log.warning("internet %s", "back" if is_online else "LOST: waiting, coverage will show a hole")
                        self.was_online = is_online
                req = APP / "restart.request"          # `python -m studio.node restart`: no admin rights needed
                if req.exists():
                    req.unlink(missing_ok=True)
                    log.warning("restart requested: stopping; Task Scheduler starts a fresh supervisor within 5 min")
                    break
                for ch in self.children:
                    ch.tick(now, is_online)
                if now - self.last_hb >= self.c["hb_s"]:
                    self.last_hb = now
                    self.write_status(now, is_online)
                if now - self.last_hk >= 300:
                    self.last_hk = now
                    housekeeping(self.c, now)
            except Exception:                         # never let one bad tick end the supervisor
                log.exception("supervisor tick failed (continuing)")
            time.sleep(2)
        log.info("stopping")
        for ch in self.children:
            ch.stop()


def setup_logging():
    LOGS.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(LOGS / "node.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(handlers=[h], level=logging.INFO)


def main(argv=None) -> int:
    setup_logging()
    lock = acquire_lock()
    if not lock:
        log.info("another supervisor is already running: exiting")
        return 0
    (APP / "node.pid").write_text(str(os.getpid()))
    try:
        Node().run()
    except Exception:
        log.exception("supervisor crashed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
