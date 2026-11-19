"""python -m studio.node            run the supervisor (what the boot task runs)
python -m studio.node health     one health check
python -m studio.node status     print the heartbeat and the state of the children's status files
python -m studio.node restart    ask the running supervisor to stop (Task Scheduler starts a fresh one within 5 min,
                                 with new code and settings); works without admin rights even though it runs as SYSTEM"""
import json
import sys
import time

from .supervisor import APP, OUT, main as run_supervisor


def status() -> int:
    try:
        hb = json.loads((OUT / "heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("no heartbeat: node not running")
        return 1
    age = time.time() - hb["t"]
    print(f"heartbeat {age:.0f} s old  git {hb['git']}  disk free {hb['disk_free_gb']} GB")
    print(f"chat_ok ({len(hb['chat_ok'])}): {', '.join(hb['chat_ok']) or '-'}")
    print(f"recording ({len(hb['recording'])}): {', '.join(hb['recording']) or '-'}")
    return 0 if age < 180 else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "health":
        from .health import main as health_main
        sys.exit(health_main())
    if cmd == "restart":
        APP.mkdir(parents=True, exist_ok=True)
        (APP / "restart.request").write_text(str(time.time()))
        print("restart requested")
        sys.exit(0)
    sys.exit(status() if cmd == "status" else run_supervisor())
