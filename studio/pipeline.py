"""One moment through transcribe -> judge -> render. Shared by `studio vod` and `studio live`."""
import json

from . import db
from .config import load


def process(con, mid: int, src_path: str, lg, model: str | None = None) -> bool:
    """Returns True when a clip was rendered. Never raises: a failure is stored on the moment."""
    from .cli import _clamp, _render_one
    from .judge.llm import as_list, judge
    from .transcribe.whisper import transcribe
    cfg = load("settings")
    m = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
    try:
        tr = transcribe(src_path, m["t_start"], m["t_end"])
        db.update(con, mid, state="fetched", transcript=json.dumps(tr["segments"]), words=tr["words"])
        if not tr["segments"]:
            db.fail(con, mid, "no speech")
            return False
        j = judge(tr["segments"], model)
        db.update(con, mid, state="judged", keep=int(bool(j.get("keep"))), score=j.get("score"),
                  reason=j.get("reason"), title=j.get("title"), hashtags=" ".join(j.get("hashtags", [])),
                  j_start=j["start"], j_end=j["end"], judge_model=model or cfg["judge"]["model"],
                  keywords=as_list(j.get("keywords")))
        if j.get("keep") and (j.get("score") or 0) >= cfg["clip"]["min_score"]:
            a, b = _clamp(j["start"], j["end"], m["t_start"], m["t_end"], cfg["clip"])
            if b - a < cfg["clip"]["min_len_s"]:
                db.fail(con, mid, f"clip too short after clamping ({b - a:.0f}s)")
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
