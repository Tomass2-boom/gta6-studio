"""The control app's local web server (127.0.0.1 only). The page is static/index.html."""
import re
import subprocess
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from .. import db
from ..config import DATA, load
from . import activity, clips, runner
from . import settings_io as S

STATIC = Path(__file__).parent / "static"
UV = ["uv", "run", "studio"]

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")


def ok(**kw):
    return jsonify({"ok": True, **kw})


def err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def body() -> dict:
    return request.get_json(silent=True) or {}


@app.get("/")
def index():
    return send_file(STATIC / "index.html")


@app.get("/api/ping")
def ping():
    return ok(app="gta6-studio")


# ---- clipping (studio live) ------------------------------------------------
@app.get("/api/live")
def live():
    st = runner.live_status()
    return ok(live=st, streamers=activity.streamers(st["running"]), activity=activity.activity(),
              jobs={n: runner.status(n) for n in ("vod", "render", "doctor")})


@app.post("/api/live/start")
def live_start():
    if runner.status("vod")["running"]:
        return err("A link is still being turned into clips (Tools tab). Wait for it to finish first.")
    try:
        runner.start_live()
    except RuntimeError as e:
        return err(str(e))
    return ok()


@app.post("/api/live/stop")
def live_stop():
    return ok(stopped=runner.stop_live())


# ---- clips -------------------------------------------------------------
@app.get("/api/clips")
def clip_list():
    out = []
    for c in clips.list_clips():
        out.append({k: c[k] for k in ("id", "name", "title", "streamer", "platform", "score", "grade",
                                      "posted", "mtime", "state")})
    return ok(clips=out)


@app.get("/api/clips/<int:mid>")
def clip_one(mid):
    c = clips.get(db.connect(), mid)
    if not c:
        return err("no such clip", 404)
    from ..render import marks as M
    c["marks"] = M.manual(c["name"])
    c["marks_on"] = S.get_switch("marks")
    return ok(clip=c)


@app.get("/video/<int:mid>")
def video(mid):
    c = clips.get(db.connect(), mid)
    if not c or not c["exists"]:
        abort(404)
    # conditional=True answers Range requests (206); Safari/WebKit will not play mp4 without them
    return send_file(c["clip_path"], mimetype="video/mp4", conditional=True, max_age=0)


@app.post("/api/clips/<int:mid>/grade")
def clip_grade(mid):
    b = body()
    try:
        clips.grade(db.connect(), mid, b.get("score"), b.get("notes"))
    except (ValueError, KeyError) as e:
        return err(str(e))
    return ok()


@app.post("/api/clips/<int:mid>/posted")
def clip_posted(mid):
    b = body()
    try:
        clips.set_posted(db.connect(), mid, b.get("platform", ""), bool(b.get("on")))
    except ValueError as e:
        return err(f"unknown platform {e}")
    return ok()


@app.post("/api/clips/<int:mid>/finder")
def clip_finder(mid):
    c = clips.get(db.connect(), mid)
    if not c or not c["exists"]:
        return err("clip file not found", 404)
    subprocess.run(["open", "-R", c["clip_path"]])
    return ok()


@app.post("/api/clips/<int:mid>/rerender")
def clip_rerender(mid):
    try:
        runner.start("render", UV + ["render", str(mid)], f"Re-making clip {mid}")
    except RuntimeError:
        return err("Another clip is being re-made. Wait a moment and try again.")
    return ok()


@app.post("/api/clips/<int:mid>/marks")
def clip_marks(mid):
    """{action: add, t, x, y, kind} | {action: none} | {action: clear}. x, y are 0-1 of the finished clip."""
    from ..render import marks as M
    c = clips.get(db.connect(), mid)
    if not c:
        return err("no such clip", 404)
    b, name = body(), c["name"]
    act = b.get("action")
    if act == "clear":
        M.set_manual(name, None)
    elif act == "none":
        M.set_manual(name, [])
    elif act == "add":
        try:
            t, x, y = float(b["t"]), float(b["x"]), float(b["y"])
        except (KeyError, TypeError, ValueError):
            return err("click on the video to place the mark")
        if not (0 <= x <= 1 and 0 <= y <= 1 and t >= 0):
            return err("the mark must be inside the video")
        kind = "arrow" if b.get("kind") == "arrow" else "circle"
        M.set_manual(name, (M.manual(name) or []) + [{"t": round(t, 2), "x": round(x, 4), "y": round(y, 4),
                                                      "r": 0.08, "kind": kind, "space": "out"}])
    else:
        return err("unknown action")
    return ok(marks=M.manual(name))


@app.post("/api/copy")
def copy():
    text = body().get("text", "")
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    return ok()


# ---- tools -------------------------------------------------------------
DOCTOR_TEXT = {  # doctor row name (start) -> (good sentence, bad sentence)
    "ffmpeg with libass": ("The video maker (ffmpeg with captions) is ready.",
                           "The video maker with captions is missing. Ask Claude to install ffmpeg-full."),
    "ffmpeg": ("The video tool ffmpeg is installed.", "The video tool ffmpeg is missing."),
    "yt-dlp": ("The video downloader (yt-dlp) is installed.", "The video downloader yt-dlp is missing."),
    "streamlink": ("The stream recorder (streamlink) is installed.", "The stream recorder streamlink is missing."),
    "ollama server": ("The local AI (Ollama) is running.",
                      "The local AI (Ollama) is not running. Open the Ollama app, then check again."),
    "ollama": ("The local AI program (Ollama) is installed.", "The local AI program Ollama is missing."),
    "uv": ("The Python runner (uv) is installed.", "The Python runner uv is missing."),
    "git": ("git is installed.", "git is missing."),
    "caption font": ("The caption font is there.", "The caption font file is missing."),
    "mlx-whisper": ("Speech-to-text (Whisper) is ready.", "Speech-to-text (Whisper) is not working."),
    "model": ("The AI judge model is downloaded.", "The AI judge model is not downloaded yet."),
    "disk": ("Enough free disk space", "Low on disk space"),
}


def doctor_plain(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.startswith(("OK  ", "FAIL")):
            continue
        good, rest = line.startswith("OK"), line[5:].strip()
        key = next((k for k in DOCTOR_TEXT if rest.startswith(k)), None)
        if key == "disk":
            msg = f"{DOCTOR_TEXT['disk'][0 if good else 1]} ({rest[4:].strip()})."
        elif key:
            msg = DOCTOR_TEXT[key][0 if good else 1]
        else:
            msg = f"{rest}: {'OK' if good else 'problem'}"
        rows.append({"ok": good, "text": msg})
    return rows


@app.post("/api/doctor")
def doctor():
    try:
        p = subprocess.run(UV + ["doctor"], cwd=runner.ROOT, env=runner.env(), capture_output=True,
                           text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return err("The health check took too long.")
    rows = doctor_plain(p.stdout)
    return ok(rows=rows, all_ok=p.returncode == 0 and all(r["ok"] for r in rows))


@app.post("/api/vod")
def vod():
    url = (body().get("url") or "").strip()
    if not re.match(r"^https?://\S+$", url):
        return err("Paste a full link that starts with https://")
    if runner.live_status()["running"]:
        return err("Clipping is running. Stop it first: both at once would slow each other down.")
    try:
        runner.start("vod", UV + ["vod", url], "Making clips from a link")
    except RuntimeError:
        return err("A link is already being turned into clips. Wait for it to finish.")
    return ok()


@app.get("/api/jobs/<name>")
def job(name):
    if name not in ("vod", "render"):
        abort(404)
    st = runner.status(name)
    lines = []
    if st.get("log"):
        for line in activity.tail(Path(st["log"]), 300):
            if line.startswith("====="):
                lines = []  # only the latest run
                continue
            r = activity.plain(line) or _job_plain(line)
            if r and (not lines or lines[-1] != r[1]):
                lines.append(r[1])
    return ok(job=st, lines=lines[-15:][::-1])


def _job_plain(line: str):
    for pat, txt in ((r"^downloading", "Downloading the video…"), (r"^detecting signals", "Looking for loud moments…"),
                     (r"^(\d+) moments to process", r"Checking \1 possible moments with the AI…"),
                     (r"moment \d+ rendered", "Made a clip.")):
        m = re.search(pat, line)
        if m:
            return "work", m.expand(txt)
    return None


# ---- settings ----------------------------------------------------------
@app.get("/api/settings")
def settings_get():
    sw = [{"key": k, "label": v[2], "help": v[3], "on": S.get_switch(k)} for k, v in S.SWITCHES.items()]
    return ok(switches=sw, streamers=S.streamers(), blocklist=S.blocklist(), review_mode=S.review_mode())


@app.post("/api/settings/switch")
def settings_switch():
    b = body()
    if b.get("key") not in S.SWITCHES:
        return err("unknown switch")
    S.set_switch(b["key"], bool(b.get("on")))
    return ok()


@app.post("/api/settings/streamer")
def settings_streamer():
    b = body()
    act = b.get("action")
    try:
        if act == "add":
            S.add_streamer(b.get("name", ""), b.get("link", ""), b.get("platform") or None, b.get("tier", "A"))
        elif act == "remove":
            S.remove_streamer(b["name"])
        elif act == "set":
            S.set_streamer(b["name"], b.get("live"), b.get("tier"))
        else:
            return err("unknown action")
    except (KeyError, ValueError) as e:
        return err(str(e).strip("'"))
    return ok(restart=runner.live_status()["running"])


@app.post("/api/settings/blocklist")
def settings_blocklist():
    b = body()
    name = (b.get("name") or "").strip()
    if not name:
        return err("type a name")
    S.set_blocked(name, bool(b.get("on", True)))
    return ok(restart=runner.live_status()["running"])


def port() -> int:
    return int(load("settings").get("review", {}).get("port", 8642))


def serve(host: str = "127.0.0.1", p: int | None = None):
    from werkzeug.serving import make_server
    srv = make_server(host, p or port(), app, threaded=True)
    srv.serve_forever()


__all__ = ["app", "serve", "port", "DATA"]
