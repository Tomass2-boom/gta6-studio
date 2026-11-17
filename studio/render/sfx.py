"""Sound effects on key moments: where to put them (pure, tested) and the ffmpeg audio graph.

Placement uses the transcript words (phrases per kind in style.yaml) and the clip's loudness
envelope: a word trigger only fires when the voice is clearly raised, and a scream needs no words.
Effects are mixed AFTER loudnorm, so the voice level is the same as without effects.
"""
import re
import subprocess
import numpy as np
import yaml
from ..config import ROOT
from . import layout as L

SFX = ROOT / "assets" / "sfx"
HOP = 0.1  # envelope step, seconds


def sounds() -> dict:
    return yaml.safe_load((SFX / "sounds.yaml").read_text()) or {}


def envelope(video: str, a: float, b: float) -> np.ndarray:
    """Loudness in dB per 100 ms of [a,b] (mono 16 kHz RMS). Empty when there is no audio."""
    raw = subprocess.run([L.tools()[0], "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video,
                          "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = int(16000 * HOP)
    k = len(x) // n
    if k == 0:
        return np.zeros(0)
    rms = np.sqrt((x[: k * n].reshape(k, n) ** 2).mean(axis=1) + 1e-10)
    return 20 * np.log10(rms)


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def _loud_at(env: np.ndarray, s: float, e: float) -> float:
    if not len(env):
        return -99.0
    i, j = int(s / HOP), max(int(s / HOP) + 1, int(np.ceil(e / HOP)))
    seg = env[max(0, i): min(len(env), j)]
    return float(seg.max()) if len(seg) else -99.0


def _peak_t(env: np.ndarray, s: float, e: float) -> float:
    i, j = max(0, int(s / HOP)), min(len(env), max(int(s / HOP) + 1, int(np.ceil(e / HOP))))
    return (i + int(np.argmax(env[i:j]))) * HOP + HOP / 2 if j > i else s


def _after(env: np.ndarray, j: int, med: float, k: dict) -> float:
    """First moment after a loud run (ending at index j) where the voice has dropped: the gap after a scream."""
    lim = med + k.get("gap_db", 6)
    for i in range(j, min(len(env), j + int(k.get("gap_max_s", 1.2) / HOP))):
        if env[i] < lim:
            return i * HOP
    return j * HOP


def place(words: list[dict], env: np.ndarray, duration: float, cfg: dict, cuts: list[float] | None = None) -> list[dict]:
    """Pick the effects for one clip. words: [{word,start,end}] in clip seconds; env: dB per HOP;
    cuts: clip seconds where the picture switches layout (a whoosh goes there).
    Returns [{t, sound, gain_db, kind, why}] sorted by time; t is when the sound should 'meet' its moment."""
    if not cfg or not cfg.get("enabled"):
        return []
    med = float(np.median(env)) if len(env) else -99.0
    toks = [(_norm(w["word"]), w) for w in words]
    toks = [(t, w) for t, w in toks if t]
    cands = []
    kinds = cfg.get("kinds", {}) or {}
    for prio, (kind, k) in enumerate(kinds.items()):
        if "phrases" in k:
            for ph in sorted(k["phrases"], key=lambda p: -len(p.split())):  # longest phrase first
                pt = [_norm(x) for x in ph.split()]
                for i in range(len(toks) - len(pt) + 1):
                    if [t for t, _ in toks[i:i + len(pt)]] != pt:
                        continue
                    s, e = toks[i][1]["start"], toks[i + len(pt) - 1][1]["end"]
                    if len(pt) == 1 and k.get("isolate_s"):  # a lone word ("what") must stand alone
                        gap_b = s - toks[i - 1][1]["end"] if i > 0 else 99
                        gap_a = toks[i + 1][1]["start"] - e if i + 1 < len(toks) else 99
                        if min(gap_b, gap_a) < k["isolate_s"]:
                            continue
                    lift = _loud_at(env, s, e) - med
                    if lift < cfg.get("word_loud_db", 6):
                        continue
                    at = {"start": s, "end": e, "peak": _peak_t(env, s, e)}[k.get("at", "start")]
                    cands.append({"t": at, "kind": kind, "prio": prio, "score": lift + 3 * len(pt),
                                  "span": (s, e), "why": f'"{ph}" +{lift:.0f} dB'})
        elif "loud_db" in k and len(env):  # scream: a loud run above the median
            hot = env >= med + k["loud_db"]
            i = 0
            while i < len(hot):
                if hot[i]:
                    j = i
                    while j < len(hot) and hot[j]:
                        j += 1
                    if (j - i) * HOP >= k.get("min_len_s", 0.3):
                        pk = i + int(np.argmax(env[i:j]))
                        at = {"end": j * HOP, "after": _after(env, j, med, k)}.get(k.get("at"), pk * HOP + HOP / 2)
                        cands.append({"t": at, "kind": kind, "prio": prio,
                                      "score": float(env[pk] - med), "span": (i * HOP, j * HOP),
                                      "why": f"loud burst +{env[pk] - med:.0f} dB for {(j - i) * HOP:.1f} s"})
                    i = j
                else:
                    i += 1
    edge = cfg.get("edge_s", 0.6)
    cands = [c for c in cands if edge <= c["t"] <= duration - edge]
    # one effect per spot: a phrase inside a scream is the same moment; keep the higher-priority kind
    cands.sort(key=lambda c: (c["prio"], -c["score"]))
    picked = []
    for c in cands:
        if len(picked) >= cfg.get("max_per_clip", 2):
            break
        if any(abs(c["t"] - p["t"]) < cfg.get("min_gap_s", 6) for p in picked):
            continue
        picked.append(c)
    vol = cfg.get("volume_db", 0)
    out = []
    for c in picked:
        k = kinds[c["kind"]]
        out.append({"t": round(c["t"], 2), "sound": k["sound"], "kind": c["kind"], "why": c["why"],
                    "gain_db": k.get("gain_db", 0) + vol, "score": c["score"]})
    pay = cfg.get("payoff") or {}
    if pay.get("enabled"):
        # the payoff: the strongest reaction picked, else the loudest clear peak. A riser builds into it.
        top = max(out, key=lambda e: e["score"]) if out else None
        if top is None and len(env):
            lo, hi = int(edge / HOP), max(int(edge / HOP) + 1, int((duration - edge) / HOP))
            seg = env[lo:hi]
            if len(seg) and seg.max() - med >= pay.get("peak_db", 12):
                i = lo + int(np.argmax(seg))  # land on the shout's start: on its peak the voice hides the hit
                while i > lo and env[i - 1] >= seg.max() - pay.get("onset_db", 6):
                    i -= 1
                t = i * HOP
                top = {"t": round(t, 2), "sound": pay["hit"], "kind": "payoff", "why": f"loudest point +{seg.max() - med:.0f} dB",
                       "gain_db": pay.get("hit_gain_db", 0) + vol, "score": 0}
                out.append(top)
        meta = sounds()
        if top is not None:
            for rs in pay.get("risers", []):  # longest that fits before the payoff
                lead = float(meta[rs]["sync"])
                if top["t"] - lead >= pay.get("min_start_s", 0.8):
                    out.append({"t": top["t"], "sound": rs, "kind": "riser", "why": f"builds into {top['kind']}",
                                "gain_db": pay.get("riser_gain_db", 0) + vol})
                    break
    for c in cuts or []:
        if edge <= c <= duration - edge and cfg.get("cut_sound"):
            out.append({"t": round(c, 2), "sound": cfg["cut_sound"], "kind": "cut", "why": "layout switch",
                        "gain_db": cfg.get("cut_gain_db", 0) + vol})
    # booms are bass: phone speakers barely play it, so a crisp hit goes on top (style.yaml `layer`)
    for e in list(out):
        k = kinds.get(e["kind"]) or (pay if e["kind"] == "payoff" else {})
        if k.get("layer"):
            out.append({"t": e["t"], "sound": k["layer"], "kind": "layer", "why": f"phone-audible top on {e['kind']}",
                        "gain_db": k.get("layer_gain_db", 0) + vol})
    for e in out:
        e.pop("score", None)
    if cfg.get("intro_whoosh"):
        out.append({"t": 0.12, "sound": cfg.get("intro_sound", "whoosh-fast-soft"), "kind": "intro",
                    "why": "intro", "gain_db": cfg.get("intro_gain_db", -6) + cfg.get("volume_db", 0)})
    return sorted(out, key=lambda x: x["t"])


HITS = ("riser", "intro", "cut", "layer")


def audio_graph(events: list[dict], loudnorm: str, first_input: int = 1, duck_db: float = 0,
                duck_s: float = 0.25) -> tuple[list[str], str]:
    """(extra ffmpeg input args, filter_complex audio part ending in [a]) for the effects.
    Voice: [0:a] -> loudnorm, dipped by duck_db for duck_s under each hit. Effects are delayed to t - sync,
    gained, mixed without normalising, then limited."""
    meta = sounds()
    duck = ""
    hits = sorted({e["t"] for e in events if e.get("kind") not in HITS})
    if duck_db and hits:
        g = 10 ** (-abs(duck_db) / 20)
        on = "+".join(f"between(t,{t - 0.02:.2f},{t + duck_s:.2f})" for t in hits)
        duck = f",volume='1-{1 - g:.3f}*min(1,{on})':eval=frame"
    args, parts, labels = [], [f"[0:a]{loudnorm}{duck},aformat=sample_fmts=fltp:channel_layouts=stereo[voice]"], ["[voice]"]
    for n, ev in enumerate(events):
        m = meta[ev["sound"]]
        args += ["-i", str(SFX / m["file"])]
        ms = max(0, round((ev["t"] - float(m.get("sync", 0))) * 1000))
        parts.append(f"[{first_input + n}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={ev['gain_db']:.1f}dB,adelay={ms}:all=1[x{n}]")
        labels.append(f"[x{n}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,"
                 f"alimiter=limit=0.84:level=false[a]")
    return args, ";".join(parts)
