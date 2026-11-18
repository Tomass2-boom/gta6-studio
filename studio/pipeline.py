"""One moment through transcribe -> judge -> render. Shared by `studio vod` and `studio live`."""
import json

from . import db
from .config import load


def process(con, mid: int, src_path: str, lg, model: str | None = None) -> bool:
    """Returns True when a clip was rendered. Never raises: a failure is stored on the moment."""
    from .transcribe.whisper import transcribe
    m = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
    try:
        tr = transcribe(src_path, m["t_start"], m["t_end"])
        db.update(con, mid, state="fetched", transcript=json.dumps(tr["segments"]), words=tr["words"])
        if not tr["segments"]:
            db.fail(con, mid, "no speech")
            return False
        src = con.execute("SELECT * FROM sources WHERE id=?", (m["source_id"],)).fetchone()
        return decide(con, m, tr["segments"], src, src_path, lg, model)
    except Exception as e:  # no silent failures
        lg.exception("moment %d failed", mid)
        db.fail(con, mid, f"{type(e).__name__}: {e}")
    return False


def decide(con, m, segments: list[dict], src, src_path: str, lg, model: str | None = None) -> bool:
    """Judge a transcribed moment and render it if kept. Used by process() and `studio rejudge`."""
    from .cli import _clamp, _render_one
    import time
    from .judge.llm import as_list, hook_title, judge, launch_cfg, snap_end
    cfg = load("settings")
    mid = m["id"]
    tr = {"segments": segments}
    try:
        sig = json.loads(m["signals"] or "{}")
        when = (sig.get("meta") or {}).get("detected_at") or time.time()
        lc = launch_cfg(dict(src) if src else None, tr["segments"], when)
        j = judge(tr["segments"], model, launch=lc, streamer=src["streamer"] if src else None)
        j["end"] = snap_end(tr["segments"], j["end"], cfg["clip"].get("end_pad_s", 0))
        j["title"] = hook_title(j.get("title"), lc, bool(j.get("first_look")), mid)
        db.update(con, mid, state="judged", keep=int(bool(j.get("keep"))), score=j.get("score"),
                  reason=j.get("reason"), title=j.get("title"), hashtags=" ".join(j.get("hashtags", [])),
                  j_start=j["start"], j_end=j["end"], judge_model=model or cfg["judge"]["model"],
                  keywords=as_list(j.get("keywords")))
        if j.get("keep") and (j.get("score") or 0) >= cfg["clip"]["min_score"]:
            a, b = _clamp(j["start"], j["end"], m["t_start"], m["t_end"], cfg["clip"])
            if b - a < cfg["clip"]["min_len_s"]:
                db.fail(con, mid, f"clip too short after clamping ({b - a:.0f}s)")
                return False
            lim = cfg["clip"].get("max_frozen_share", 0.6)
            if lim < 1:
                from .detect.still import frozen_share
                fz = frozen_share(src_path, a, b)
                if fz > lim:
                    db.fail(con, mid, f"picture frozen for {fz:.0%} of the clip (no game on screen?)")
                    lg.info("moment %d dropped: picture frozen %.0f%%", mid, 100 * fz)
                    return False
            db.update(con, mid, j_start=a, j_end=b)
            _render_one(con, mid, lg)
            lg.info("moment %d kept (%s) %s", mid, j.get("score"), j.get("title"))
            return True
        lg.info("moment %d dropped: %s", mid, j.get("reason"))
    except Exception as e:  # no silent failures
        lg.exception("moment %d failed", mid)
        db.fail(con, mid, f"{type(e).__name__}: {e}")
    return False
