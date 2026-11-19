"""Health check, run by a scheduled task (daily, and hourly is fine): if the node's heartbeat is stale, restart the
node; free disk if it is below budget. The supervisor already restarts its children; this guards the supervisor.

Run: python -m studio.node health
"""
import json
import logging
import subprocess
import sys
import time

from .proc import NO_WINDOW, kill_tree
from .supervisor import APP, OUT, cfg, disk_free_gb, housekeeping, setup_logging

log = logging.getLogger("node.health")
TASK = "GTA6Node"


def heartbeat_age() -> float | None:
    try:
        return time.time() - json.loads((OUT / "heartbeat.json").read_text(encoding="utf-8"))["t"]
    except (OSError, ValueError, KeyError):
        return None


def is_node_pid(pid: int) -> bool:
    """Is this PID really a studio.node process (a stale pid file may name an unrelated process now)?"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return "studio.node" in r.stdout


def restart_node():
    try:
        pid = int((APP / "node.pid").read_text())
        if is_node_pid(pid):
            log.warning("killing stuck supervisor pid %s (its job object takes the children with it)", pid)
            kill_tree(pid)
            time.sleep(3)
    except (OSError, ValueError):
        pass
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, text=True, creationflags=NO_WINDOW)
    log.warning("schtasks /Run %s -> %s %s", TASK, r.returncode, (r.stdout + r.stderr).strip()[:200])


def main() -> int:
    setup_logging()
    c = cfg()
    age = heartbeat_age()
    if age is None or age > 300:
        log.warning("heartbeat %s: restarting the node", "missing" if age is None else f"{age:.0f} s old")
        restart_node()
    else:
        log.info("heartbeat fresh (%.0f s)", age)
    low = disk_free_gb() < c["min_free_gb"]
    gone = housekeeping(c, aggressive=low)
    log.info("disk free %.1f GB (low=%s); removed %s", disk_free_gb(), low, gone)
    return 0


if __name__ == "__main__":
    sys.exit(main())
