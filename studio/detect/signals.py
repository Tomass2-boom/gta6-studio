"""VOD signals: loudness peaks + YouTube 'most replayed'. Returns merged candidate windows."""
import subprocess
import numpy as np


def loudness_per_second(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = len(a) // 8000
    return np.sqrt((a[: n * 8000].reshape(n, 8000) ** 2).mean(axis=1)) if n else np.zeros(0)


def loud_peaks(rms: np.ndarray, z: float = 2.5, min_gap: int = 30):
    """Seconds where short-term loudness is far above the video's own baseline."""
    if len(rms) < 20:
        return []
    sm = np.convolve(rms, np.ones(3) / 3, mode="same")
    med, mad = np.median(sm), np.median(np.abs(sm - np.median(sm))) + 1e-6
    score = (sm - med) / (1.4826 * mad)
    peaks, last = [], -min_gap
    for t in np.argsort(-score):
        if score[t] < z:
            break
        if all(abs(t - p[0]) >= min_gap for p in peaks):
            peaks.append((int(t), float(score[t])))
    return peaks


def heatmap_peaks(heatmap, thresh: float = 0.6):
    return [(int((h["start_time"] + h["end_time"]) / 2), float(h["value"]))
            for h in heatmap if h["value"] >= thresh]


def candidates(peaks: dict, duration: float, pre=20, post=40, merge_gap=30, max_window=90):
    """peaks: {signal_name: [(t, strength)]}. Cut windows around each, merge overlaps."""
    ev = sorted((t, name, s) for name, lst in peaks.items() for t, s in lst)
    wins = []
    for t, name, s in ev:
        a, b = max(0, t - pre), min(duration, t + post)
        if wins and a <= wins[-1]["t_end"] + merge_gap - pre and b - wins[-1]["t_start"] <= max_window:
            w = wins[-1]
            w["t_end"] = max(w["t_end"], b)
            w["signals"][name] = max(w["signals"].get(name, 0), s)
        else:
            wins.append({"t_start": a, "t_end": b, "signals": {name: s}})
    for w in wins:
        w["signal_score"] = len(w["signals"]) * 10 + sum(w["signals"].values())
    return sorted(wins, key=lambda w: -w["signal_score"])
