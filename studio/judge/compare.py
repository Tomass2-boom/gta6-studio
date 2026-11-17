"""Run two Ollama models on the same stored transcripts; print time, validity, keep-rate."""
import json
import sys
import time
from .. import db
from .llm import judge


def main(models):
    rows = db.connect().execute("SELECT id, transcript FROM moments WHERE transcript IS NOT NULL").fetchall()
    for m in models:
        t0, ok, keep, bad, lens = time.time(), 0, 0, 0, []
        for r in rows:
            try:
                j = judge(json.loads(r["transcript"]), m)
                ok += 1
                keep += bool(j.get("keep"))
                lens.append(round(j["end"] - j["start"]))
                print(m, r["id"], j.get("keep"), j.get("score"), round(j["end"] - j["start"]), j.get("title"), flush=True)
            except Exception as e:
                bad += 1
                print(m, r["id"], "ERR", str(e)[:80], flush=True)
        print(f"== {m}: {time.time()-t0:.0f}s, valid {ok}/{len(rows)}, kept {keep}, bad {bad}\n")


if __name__ == "__main__":
    main(sys.argv[1:])
