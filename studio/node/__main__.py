"""python -m studio.node            run the supervisor (what the boot task runs)
python -m studio.node health     one health check
python -m studio.node status     print the heartbeat and the state of the children's status files"""
import json
import sys
import time

from .supervisor import OUT, main as run_supervisor


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
    sys.exit(status() if cmd == "status" else run_supervisor())
