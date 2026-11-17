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


def place(words: list[dict], env: np.ndarray, duration: float, cfg: dict) -> list[dict]:
    """Pick the effects for one clip. words: [{word,start,end}] in clip seconds; env: dB per HOP.
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
                        at = j * HOP if k.get("at") == "end" else pk * HOP + HOP / 2
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
    out = []
    for c in picked:
        k = kinds[c["kind"]]
        out.append({"t": round(c["t"], 2), "sound": k["sound"], "kind": c["kind"], "why": c["why"],
                    "gain_db": k.get("gain_db", 0) + cfg.get("volume_db", 0)})
    if cfg.get("intro_whoosh"):
        out.append({"t": 0.12, "sound": cfg.get("intro_sound", "whoosh-fast-soft"), "kind": "intro",
                    "why": "intro", "gain_db": cfg.get("intro_gain_db", -6) + cfg.get("volume_db", 0)})
    return sorted(out, key=lambda x: x["t"])


def audio_graph(events: list[dict], loudnorm: str, first_input: int = 1) -> tuple[list[str], str]:
    """(extra ffmpeg input args, filter_complex audio part ending in [a]) for the effects.
    Voice: [0:a] -> loudnorm. Effects are delayed to t - sync, gained, mixed without normalising, then limited."""
    meta = sounds()
    args, parts, labels = [], [f"[0:a]{loudnorm},aformat=sample_fmts=fltp:channel_layouts=stereo[voice]"], ["[voice]"]
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
