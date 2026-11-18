"""Open the control app: start the local server (unless one is already up) and show it in a
native Mac window (pywebview). `python -m studio.review.window` is what the .app runs."""
import threading
import time
import webbrowser
from pathlib import Path

import requests

from . import server

ICON = Path(__file__).parent / "static" / "icon.png"


def up(port: int) -> bool:
    try:
        return requests.get(f"http://127.0.0.1:{port}/api/ping", timeout=1).json().get("app") == "gta6-studio"
    except Exception:
        return False


def _free(port: int) -> bool:
    import socket
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def ensure_server(port: int) -> tuple[int, bool]:
    """(port, started_here). Reuses a running GTA6 Studio server; skips ports other programs use."""
    for p in range(port, port + 20):
        if up(p):
            return p, False
    p = next((p for p in range(port, port + 20) if _free(p)), None)
    if p is None:
        raise SystemExit(f"No free port near {port}")
    threading.Thread(target=server.serve, kwargs={"p": p}, daemon=True).start()
    for _ in range(100):
        if up(p):
            break
        time.sleep(0.1)
    return p, True


def _mac_dress():
    """Dock icon and menu name 'GTA6 Studio' instead of 'Python'."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "GTA6 Studio"
        if ICON.exists():
            NSApplication.sharedApplication().setApplicationIconImage_(
                NSImage.alloc().initWithContentsOfFile_(str(ICON)))
    except Exception:
        pass


def main(browser: bool = False):
    port, mine = ensure_server(server.port())
    url = f"http://127.0.0.1:{port}/"
    if browser:
        webbrowser.open(url)
        if mine:
            threading.Event().wait()  # keep serving until Ctrl-C
        return
    import webview
    _mac_dress()
    webview.create_window("GTA6 Studio", url, width=1440, height=940, min_size=(1000, 700))
    webview.start()
    # The window is closed. Clipping (studio live) keeps running in its own process.


if __name__ == "__main__":
    main()
