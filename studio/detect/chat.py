"""Chat spike detector. Pure logic, time is passed in so it can be tested on a saved chat log.

Each channel has its own baseline: messages (weighted) per 10 s bin. A spike needs a short burst
(latest bin) AND a sustained rise (last 3 bins = 30 s) against the median of the last 10 minutes.
Hype messages (LOL, W, OMG, clip it, laugh emotes) weigh more than plain chatter.
Copy-paste spam (a raid pasting the same sentence 60 times, seen 2026-11-17) is capped: a message of
repeat_min_words+ words with the same first words counts at most repeat_cap times per bin. Short hype
("LOL", "KEKW") is never capped: many people typing it is the signal.
"""
import re
import statistics
from dataclasses import dataclass, field


@dataclass
class Spike:
    channel: str
    t: float          # wall-clock time (epoch s) when the burst bin ENDED
    strength: float   # burst / baseline
    burst: float
    baseline: float
    hype_share: float
    kind: str = "chat"   # which signal fired: chat | loud


@dataclass
class _Chan:
    bins: dict = field(default_factory=dict)      # idx -> weighted count
    hype: dict = field(default_factory=dict)      # idx -> hype weight
    reps: dict = field(default_factory=dict)      # (idx, first words) -> count, for the spam cap
    first_idx: int | None = None
    done_idx: int | None = None                   # last evaluated bin


def make_weigher(words, hype_weight=3.0):
    words = [str(w) for w in words]
    singles = {w.lower() for w in words if " " not in w}
    phrases = [w.lower() for w in words if " " in w]

    def weigh(text: str) -> tuple[float, bool]:
        t = text.lower()
        toks = set(re.findall(r"[\w?!]+|[^\w\s]", t))
        hype = bool(toks & singles) or any(p in t for p in phrases) or any(s in t for s in singles if not s.isalnum())
        if not hype and len(text) > 6 and sum(c.isupper() for c in text) > 0.7 * sum(c.isalpha() for c in text) > 0:
            hype = True   # SHOUTING
        return (hype_weight if hype else 1.0), hype
    return weigh


class ChatDetector:
    def __init__(self, cfg: dict):
        self.c = cfg
        self.weigh = make_weigher(cfg["hype_words"], cfg["hype_weight"])
        self.ch: dict[str, _Chan] = {}

    def feed(self, channel: str, ts: float, text: str) -> list[Spike]:
        st = self.ch.setdefault(channel, _Chan())
        i = int(ts // self.c["bin_s"])
        if st.first_idx is None:
            st.first_idx = i
            st.done_idx = i - 1
        w, hype = self.weigh(text)
        words = re.findall(r"\w+", text.lower())
        if len(words) >= self.c.get("repeat_min_words", 3):
            key = (i, " ".join(words[:self.c.get("repeat_min_words", 3)]))
            st.reps[key] = st.reps.get(key, 0) + 1
            if st.reps[key] > self.c.get("repeat_cap", 3):
                return self.tick(ts, channel)
        st.bins[i] = st.bins.get(i, 0) + w
        if hype:
            st.hype[i] = st.hype.get(i, 0) + w
        return self.tick(ts, channel)

    def tick(self, now: float, only: str | None = None) -> list[Spike]:
        """Evaluate every bin that has fully ended before `now`."""
        out = []
        for name, st in self.ch.items():
            if only and name != only:
                continue
            last_closed = int(now // self.c["bin_s"]) - 1
            while st.done_idx is not None and st.done_idx < last_closed:
                st.done_idx += 1
                sp = self._eval(name, st, st.done_idx)
                if sp:
                    out.append(sp)
        return out

    def _eval(self, name: str, st: _Chan, i: int) -> Spike | None:
        c = self.c
        if i - st.first_idx < c["warmup_bins"]:
            return None
        w = lambda k: st.bins.get(k, 0)
        burst = w(i)
        sustain = sum(w(i - k) for k in range(3)) / 3
        hist = [w(k) for k in range(max(st.first_idx, i - 3 - c["baseline_bins"]), i - 2)]
        base = max(statistics.median(hist), c["floor"])
        for k in [k for k in st.bins if k < i - c["baseline_bins"] - 10]:   # keep memory small
            st.bins.pop(k); st.hype.pop(k, None)
        for k in [k for k in st.reps if k[0] < i]:
            st.reps.pop(k)
        if burst >= c["min_burst"] and burst >= c["burst_x"] * base and sustain >= c["sustain_x"] * base:
            return Spike(name, (i + 1) * c["bin_s"], burst / base, burst, base,
                         st.hype.get(i, 0) / burst if burst else 0)
        return None
