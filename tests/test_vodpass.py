from studio.vodpass import candidates


def test_signals_merge_and_rank():
    rated = {0: {"score": 8, "at": 100, "what": "x"}, 1: {"score": 3}, 10: {"score": 6, "at": 1250}}
    clips = [{"off": 120, "dur": 30, "views": 5000, "title": "t"}, {"off": 3000, "dur": 20, "views": 50, "title": "few"}]
    w = candidates(rated, clips, [500], pre=25, post=90)
    assert w[0]["a"] == 75 and len(w[0]["why"]) == 2          # text block + twitch clip merge into one window
    assert all("few" not in " ".join(x["why"]) for x in w)    # clips under 300 views are noise
    assert not any(x["a"] <= 150 <= x["b"] and "text 3" in str(x["why"]) for x in w)   # low blocks dropped


def test_merged_windows_stay_bounded():
    rated = {i: {"score": 7, "at": i * 60.0} for i in range(20)}   # a long busy stretch
    w = candidates(rated, [], [], pre=25, post=90)
    assert max(x["b"] - x["a"] for x in w) <= 200
