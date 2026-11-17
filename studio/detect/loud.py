"""Loudness trigger for live channels that have no chat reader. Pure logic: per-second loudness (RMS) goes in
with its wall-clock start time, spikes come out. Each channel has its own baseline (median/MAD of the last
10 minutes), so a streamer who is always loud does not fire all the time."""
from collections import deque

import numpy as np

from .chat import Spike


class LoudDetector:
    def __init__(self, cfg: dict):
        self.c = cfg
        self.hist: dict[str, deque] = {}
        self.seen: dict[str, int] = {}      # channel -> seconds fed so far
        self.last_fire: dict[str, float] = {}

    def feed(self, channel: str, t0: float, rms) -> list[Spike]:
        """rms[i] is the loudness of second t0+i. Returns spikes (t = end of the burst)."""
        c, out = self.c, []
        h = self.hist.setdefault(channel, deque(maxlen=c["baseline_s"] + c["burst_s"]))
        for i, v in enumerate(rms):
            h.append(float(v))
            self.seen[channel] = self.seen.get(channel, 0) + 1
            if self.seen[channel] < c["warmup_s"] or len(h) < c["burst_s"] + 30:
                continue
            a = np.fromiter(h, dtype=np.float32)
            burst, base = np.median(a[-c["burst_s"]:]), a[:-c["burst_s"]]   # median: one bang does not fire
            med = float(np.median(base))
            mad = max(float(np.median(np.abs(base - med))) * 1.4826, 0.15 * med, 1e-4)   # floor: steady loud streams
            z, ratio = (burst - med) / mad, burst / max(med, 1e-4)
            t = t0 + i + 1
            if z >= c["z"] and ratio >= c["min_ratio"] and t - self.last_fire.get(channel, -1e9) >= c["cooldown_s"]:
                self.last_fire[channel] = t
                out.append(Spike(channel, t, float(ratio), float(burst), med, 0.0, "loud"))
        return out
