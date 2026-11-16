import numpy as np
from studio.detect import signals


def test_loud_peak_found():
    rms = np.full(300, 0.02) + np.random.default_rng(0).normal(0, 0.002, 300)
    rms[150:153] = 0.3
    peaks = signals.loud_peaks(rms)
    assert any(abs(t - 151) <= 2 for t, _ in peaks)


def test_candidates_merge_and_rank():
    w = signals.candidates({"loudness": [(100, 5)], "most_replayed": [(110, 1)]}, 1000)
    assert len(w) == 1 and set(w[0]["signals"]) == {"loudness", "most_replayed"}
    assert w[0]["t_start"] == 80
