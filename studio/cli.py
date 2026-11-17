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
    if name not in known:
        lg.warning("%s is not in streamers.yaml (allowed status unknown)", src["streamer"])
    elif not known[name].get("allowed", False):
        raise typer.Exit(f"{src['streamer']} has allowed: false; refusing.")


@app.command()
def vod(url: str, top: int = typer.Option(15, help="Max candidate moments to judge"),
        model: str = typer.Option(None, help="Override judge model")):
    """VOD mode: link in -> scored moments -> plain cut clips."""
    from .detect import signals
    from .ingest.download import fetch_video
    from .judge.llm import judge
    from .render.cut import plain_cut
    from .transcribe.whisper import transcribe

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
        mid = m["id"]
        try:
            tr = transcribe(src["path"], m["t_start"], m["t_end"])
            db.update(con, mid, state="fetched", transcript=json.dumps(tr["segments"]))
            if not tr["segments"]:
                db.fail(con, mid, "no speech")
                continue
            j = judge(tr["segments"], model)
            db.update(con, mid, state="judged", keep=int(bool(j.get("keep"))), score=j.get("score"),
                      reason=j.get("reason"), title=j.get("title"), hashtags=" ".join(j.get("hashtags", [])),
                      j_start=j["start"], j_end=j["end"], judge_model=model or cfg["judge"]["model"])
            if j.get("keep") and (j.get("score") or 0) >= cfg["clip"]["min_score"]:
                a, b = _clamp(j["start"], j["end"], m["t_start"], m["t_end"], cfg["clip"])
                if b - a < cfg["clip"]["min_len_s"]:
                    db.fail(con, mid, f"clip too short after clamping ({b - a:.0f}s)")
                    continue
                db.update(con, mid, j_start=a, j_end=b)
                path = plain_cut(src["path"], a, b, f"{sid}_{int(a)}")
                db.update(con, mid, state="rendered", clip_path=path)
                lg.info("moment %d kept (%s) %s", mid, j.get("score"), j.get("title"))
            else:
                lg.info("moment %d dropped: %s", mid, j.get("reason"))
        except Exception as e:  # no silent failures
            lg.exception("moment %d failed", mid)
            db.fail(con, mid, f"{type(e).__name__}: {e}")


@app.command()
def moments():
    """List moments in the database."""
    for m in db.connect().execute("SELECT id,state,score,title,j_start,j_end,judge_model,fail_reason FROM moments"):
        typer.echo(dict(m))


@app.command()
def live():
    typer.echo("Live mode arrives in S3.")


@app.command()
def review():
    typer.echo("Review page arrives in S4.")


@app.command()
def post():
    typer.echo("Posting arrives in S4.")
