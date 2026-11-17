import json
import logging
import shutil
from pathlib import Path
from datetime import date

import requests
import typer

from . import db
from .config import DATA, load

app = typer.Typer(no_args_is_help=True)


def log(stage: str) -> logging.Logger:
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger(stage)
    if not lg.handlers:
        lg.addHandler(logging.FileHandler(DATA / "logs" / f"{date.today()}-{stage}.log"))
        lg.addHandler(logging.StreamHandler())
        lg.setLevel(logging.INFO)
    return lg


@app.command()
def doctor():
    """Check tools, models, disk."""
    ok = True

    def row(name, good, note=""):
        nonlocal ok
        ok &= bool(good)
        typer.echo(f"{'OK  ' if good else 'FAIL'} {name} {note}")

    for t in ("ffmpeg", "yt-dlp", "streamlink", "ollama", "uv", "git"):
        row(t, shutil.which(t))
    import subprocess
    from .render.layout import tools
    ff = tools()[0]
    try:
        flt = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        row("ffmpeg with libass (captions)", " ass " in flt, ff)
    except FileNotFoundError:
        row("ffmpeg with libass (captions)", False, f"{ff} missing (brew install ffmpeg-full)")
    font = load("style")["captions"]["font_file"]
    row("caption font", (Path(__file__).parent.parent / "assets" / "fonts" / font).exists(), font)
    try:
        import mlx_whisper  # noqa: F401
        row("mlx-whisper", True)
    except Exception as e:
        row("mlx-whisper", False, str(e))
    cfg = load("settings")["judge"]
    try:
        have = {m["name"] for m in requests.get(cfg["ollama_url"] + "/api/tags", timeout=5).json()["models"]}
        row("ollama server", True)
        row(f"model {cfg['model']}", cfg["model"] in have)
    except Exception:
        row("ollama server", False, "(run: brew services start ollama)")
    free = shutil.disk_usage(DATA if DATA.exists() else ".").free / 1e9
    row("disk", free > 50, f"{free:.0f} GB free")
    raise typer.Exit(0 if ok else 1)


def _clamp(a, b, lo, hi, c):
    """Keep the judge's cut inside the candidate window; respect max length; extend a too-short cut."""
    a, b = max(lo, a), min(hi, b)
    if b - a < c["min_len_s"]:
        b = min(hi, a + c["min_len_s"])
        a = max(lo, b - c["min_len_s"])
    return a, min(b, a + c["max_len_s"])


def _check_streamer(src, lg):
    s = load("streamers")
    name = (src["streamer"] or "").lower()
    if any(name == str(x).lower() for x in s.get("blocklist", [])):
        raise typer.Exit(f"{src['streamer']} is on the blocklist; refusing.")
    known = {str(x.get("name", "")).lower(): x for x in s.get("streamers", [])}
    # The user decides what may be posted (decided 2026-11-17); `allowed` is a note only, never blocks.
    if name not in known or known[name].get("allowed") is not True:
        lg.info("%s: allowed=%s (user checks before posting)", src["streamer"], known.get(name, {}).get("allowed"))


@app.command()
def vod(url: str, top: int = typer.Option(15, help="Max candidate moments to judge"),
        model: str = typer.Option(None, help="Override judge model")):
    """VOD mode: link in -> scored moments -> plain cut clips."""
    from .detect import signals
    from .ingest.download import fetch_video
    from .pipeline import process

    cfg, lg, con = load("settings"), log("vod"), db.connect()
    src = con.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
    if not src or not src["path"] or not __import__("os").path.exists(src["path"]):
        lg.info("downloading %s", url)
        v = fetch_video(url)
        con.execute("INSERT OR REPLACE INTO sources(url,platform,streamer,title,path,duration) VALUES(?,?,?,?,?,?)",
                    (url, v["platform"], v["streamer"], v["title"], v["path"], v["duration"]))
        con.commit()
        src = con.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
    sid = src["id"]
    _check_streamer(src, lg)
    info = Path(src["path"]).with_suffix(".info.json")
    heat = json.loads(info.read_text()).get("heatmap") or [] if info.exists() else []
    if not con.execute("SELECT 1 FROM moments WHERE source_id=?", (sid,)).fetchone():
        lg.info("detecting signals")
        c = cfg["clip"]
        peaks = {"loudness": signals.loud_peaks(signals.loudness_per_second(src["path"]), z=cfg["clip"]["loud_z"]),
                 "most_replayed": signals.heatmap_peaks(heat, c["heatmap_min"])}
        for w in signals.candidates(peaks, src["duration"], c["pre_roll_s"], c["post_roll_s"], c["merge_gap_s"], c["max_window_s"]):
            con.execute("INSERT INTO moments(source_id,t_start,t_end,signals,signal_score) VALUES(?,?,?,?,?)",
                        (sid, w["t_start"], w["t_end"], json.dumps(w["signals"]), w["signal_score"]))
        con.commit()
    rows = con.execute("SELECT * FROM moments WHERE source_id=? AND state IN ('detected','fetched') "
                       "ORDER BY signal_score DESC LIMIT ?", (sid, top)).fetchall()
    lg.info("%d moments to process", len(rows))
    for m in rows:
        process(con, m["id"], src["path"], lg, model)


def _render_one(con, mid: int, lg, force_layout: str | None = None):
    """Render one judged moment vertically. Fetches word timing / keywords if missing."""
    from .judge.llm import keywords as pick_keywords
    from .render.vertical import render
    from .transcribe.whisper import transcribe
    m = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
    src = dict(con.execute("SELECT * FROM sources WHERE id=?", (m["source_id"],)).fetchone())
    a, b = m["j_start"], m["j_end"]
    words = json.loads(m["words"]) if m["words"] else []
    if not words or words[0]["start"] > a + 2 or words[-1]["end"] < b - 2:
        words = transcribe(src["path"], a, b)["words"]
        db.update(con, mid, words=words)
    kw = json.loads(m["keywords"]) if m["keywords"] else None
    if kw is None:
        kw = pick_keywords(" ".join(w["word"] for w in words if a <= w["start"] <= b))
        db.update(con, mid, keywords=kw)
    path, lay, note = render(src, a, b, words, kw, m["title"], f"{src['id']}_{int(a)}", force_layout)
    db.update(con, mid, state="rendered", clip_path=path, layout=lay)
    lg.info("moment %d rendered, layout %s (%s): %s", mid, lay, note, path)
    return path


@app.command("render")
def render_cmd(ids: list[int] = typer.Argument(None, help="Moment ids (default: all rendered/judged-kept)"),
               layout: str = typer.Option(None, help="Force layout A, B or C (default from style.yaml)")):
    """Render moments as finished vertical clips (re-runnable; style comes from config/style.yaml)."""
    lg, con = log("render"), db.connect()
    if not ids:
        ids = [r["id"] for r in con.execute("SELECT id FROM moments WHERE state='rendered' OR "
                                            "(state='judged' AND keep=1 AND j_end > j_start)")]
    for mid in ids:
        try:
            _render_one(con, mid, lg, layout)
        except Exception as e:  # no silent failures
            lg.exception("moment %d render failed", mid)
            db.fail(con, mid, f"render: {type(e).__name__}: {e}")


@app.command()
def moments():
    """List moments in the database."""
    for m in db.connect().execute("SELECT id,state,score,title,j_start,j_end,judge_model,fail_reason FROM moments"):
        typer.echo(dict(m))


@app.command()
def live(channels: str = typer.Option(None, help="Ad hoc channels instead of streamers.yaml, e.g. lirik:A,gaules:B or kick/destiny:A,youtube/IShowSpeed:A"),
         model: str = typer.Option(None, help="Override judge model")):
    """Live mode: watch chat, record Tier A, fetch Tier B on spikes, turn spikes into clips. Ctrl-C stops."""
    from .live import run
    run(channels, model)


@app.command()
def mark(clip: str = typer.Argument(..., help="Clip name, e.g. 1_1563 (the file name in data/clips without .mp4)"),
         t: float = typer.Argument(None, help="Seconds into the clip"),
         x: float = typer.Argument(None, help="0-1 across the finished clip (left to right)"),
         y: float = typer.Argument(None, help="0-1 down the finished clip (top to bottom)"),
         kind: str = typer.Option("circle", help="circle or arrow"),
         size: float = typer.Option(0.08, help="circle radius as a share of the clip width"),
         none: bool = typer.Option(False, "--none", help="This clip gets no marks (overrides auto)"),
         clear: bool = typer.Option(False, "--clear", help="Forget hand-set marks (back to auto)")):
    """Set a circle/arrow by hand (needs marks.enabled in style.yaml). Re-render the clip afterwards."""
    from .render import marks as M
    if clear:
        M.set_manual(clip, None)
    elif none:
        M.set_manual(clip, [])
    else:
        if None in (t, x, y):
            raise typer.BadParameter("give T X Y, or --none / --clear")
        cur = M.manual(clip) or []
        M.set_manual(clip, cur + [{"t": t, "x": x, "y": y, "r": size, "kind": kind, "space": "out"}])
    typer.echo(f"{clip}: {M.manual(clip)}")


@app.command()
def review():
    typer.echo("Review page arrives in S4.")


@app.command()
def post():
    typer.echo("Posting arrives in S4.")
