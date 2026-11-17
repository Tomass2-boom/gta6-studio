import shutil
import subprocess
from pathlib import Path

import pytest

from studio.config import load
from studio.render import layout as L
from studio.render.captions import ass_color, build_ass, chunk_words

FIX = Path(__file__).parent / "fixtures" / "sample.mp4"
CAP = load("style")["captions"]


def W(word, s, e):
    return {"word": word, "start": s, "end": e}


def test_ass_color_is_bgr():
    assert ass_color("3CFF3C") == "&H003CFF3C"
    assert ass_color("FFE600") == "&H0000E6FF"


def test_chunks_respect_size_pause_and_punctuation():
    words = [W("I'm", 0, .2), W("flying,", .2, .5), W("bro", .55, .7), W("what", 2.0, 2.2), W("is", 2.2, 2.3), W("happening", 2.3, 2.8)]
    ch = [" ".join(w["text"] for w in c["words"]) for c in chunk_words(words, CAP)]
    assert ch == ["I'm flying", "bro", "what is", "happening"]
    times = chunk_words(words, CAP)
    assert all(a["end"] <= b["start"] for a, b in zip(times, times[1:]))


def test_off_ranges_smooths_blips_and_pads():
    tl = [True] * 5 + [False] * 4 + [True] + [False] + [True] * 3
    assert L.off_ranges(tl, 2) == [(4.5, 11.5)]


def test_keyword_gets_highlight_and_captions_move_in_b_ranges():
    style = load("style")
    ass = build_ass([W("eat", 0, .3), W("this", .3, .6), W("now", 3, 3.3)], ["eat"], style, "A",
                    {"caption_y": 730, "caption_y_b": 1190, "b_ranges": [(2.5, 5)]}, 5)
    assert ass_color(style["captions"]["highlight_colors"][0]) in ass
    assert "\\pos(540,730)" in ass and "\\pos(540,1190)" in ass


@pytest.mark.skipif(not shutil.which(L.tools()[0]) and not Path(L.tools()[0]).exists(), reason="ffmpeg-full missing")
@pytest.mark.parametrize("lay", ["B", "C"])
def test_render_fixture(lay, tmp_path, monkeypatch):
    from studio.render import vertical
    monkeypatch.setattr(vertical, "DATA", tmp_path)
    path, got, _ = vertical.render({"path": str(FIX), "streamer": None}, 0.5, 3.5,
                                   [W("hello", 1, 1.4), W("there", 1.4, 1.8)], ["hello"], "Test title", "t", lay)
    out = subprocess.run([L.tools()[1], "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                          path], capture_output=True, text=True).stdout
    assert got == lay and "1080,1920" in out


def test_whisper_loop_is_dropped_but_normal_repeats_stay():
    from studio.transcribe.whisper import drop_loops
    loop = [W(x, i, i + .1) for i, x in enumerate("hey I was the one. I was the one. I was the one. I was the one. bye".split())]
    assert " ".join(w["word"] for w in drop_loops(loop)) == "hey I was the one. bye"
    real = [W(x, i, i + .1) for i, x in enumerate("go go go oh my god oh my god".split())]
    assert len(drop_loops(real)) == len(real)


def test_layout_mix_is_stable():
    from studio.render.vertical import _mix_pick
    style = {"layout_mix": {"C": 0.5}}
    picks = [_mix_pick(style, f"1_{i}") for i in range(200)]
    assert picks == [_mix_pick(style, f"1_{i}") for i in range(200)]
    assert 60 < picks.count("C") < 140
