"""S8 part 1: sound effects, circles/arrows, face tracking. All off by default."""
import copy
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from studio.config import load
from studio.render import facetrack as T
from studio.render import layout as L
from studio.render import marks as M
from studio.render import sfx as S

FIX = Path(__file__).parent / "fixtures" / "sample.mp4"
HAVE_FF = shutil.which(L.tools()[0]) or Path(L.tools()[0]).exists()


def W(word, s, e):
    return {"word": word, "start": s, "end": e}


def on(style, *keys):
    st = copy.deepcopy(style)
    for k in keys:
        st[k]["enabled"] = True
    return st


def test_all_extras_are_off_by_default():
    st = load("style")
    assert not st["sound_effects"]["enabled"] and not st["marks"]["enabled"] and not st["face_track"]["enabled"]


def test_with_extras_off_the_render_command_is_unchanged():
    """Regression guard for live mode: no tracking -> plain crop, no effects -> voice through -af."""
    from studio.render.vertical import ffmpeg_cmd
    st = load("style")
    g, _ = L.build("B", st, 1920, 1080)
    assert g == "[0:v]crop=608:1080:656:0,scale=1080:1920:flags=lanczos,setsar=1[base]"
    ga, _ = L.build("A", st, 1920, 1080, (0, 0, 375, 282))
    assert "sendcmd" not in ga and "crop@" not in ga
    cmd = ffmpeg_cmd("ffmpeg", "in.mp4", 1.0, 5.0, "VF", "AF", 20, "out.mp4")
    assert cmd[cmd.index("-af") + 1] == "AF" and "0:a?" in cmd and cmd.count("-i") == 1


def _env(n=100, base=-30.0):
    return np.full(n, base)


def test_shouted_lone_what_fires_calm_or_mid_sentence_does_not():
    cfg = on(load("style"), "sound_effects")["sound_effects"]
    env = _env()
    env[40:44] = -20  # 10 dB up at 4.0-4.4 s
    words = [W("so", 1.0, 1.2), W("what", 4.0, 4.3), W("okay", 6.0, 6.3)]
    ev = S.place(words, env, 10, cfg)
    assert [e["kind"] for e in ev] == ["bro_what"] and ev[0]["sound"] == "boom-hit-medium"
    calm = S.place(words, _env(), 10, cfg)
    assert calm == []
    mid = S.place([W("what", 4.0, 4.2), W("is", 4.22, 4.3), W("that", 4.32, 4.4)], env, 10, cfg)
    assert mid == []  # "what is that" is a question, not a "bro what"


def test_effects_are_capped_and_spaced_and_off_does_nothing():
    st = on(load("style"), "sound_effects")
    cfg = st["sound_effects"]
    env = _env(300)
    for i in (20, 60, 90, 200):  # four loud bursts (screams), two of them 3 s apart
        env[i:i + 5] = -5
    ev = S.place([], env, 30, cfg)
    assert len(ev) == cfg["max_per_clip"]
    assert all(b["t"] - a["t"] >= cfg["min_gap_s"] for a, b in zip(ev, ev[1:]))
    assert S.place([], env, 30, load("style")["sound_effects"]) == []


def test_fail_phrase_lands_at_its_end():
    cfg = on(load("style"), "sound_effects")["sound_effects"]
    env = _env()
    env[30:40] = -18
    ev = S.place([W("no", 3.0, 3.2), W("no", 3.3, 3.5), W("no", 3.6, 3.9)], env, 10, cfg)
    assert ev[0]["kind"] == "fail" and ev[0]["t"] == 3.9


def test_audio_graph_delays_each_effect_by_its_sync_point():
    args, g = S.audio_graph([{"t": 2.0, "sound": "boom-hit-medium", "gain_db": 2}], "loudnorm=I=-14")
    assert args == ["-i", str(S.SFX / "boom-hit-medium.wav")]
    assert "adelay=1962:all=1" in g and "normalize=0" in g and g.endswith("[a]")


def test_smooth_path_is_calm():
    rng = np.random.default_rng(1)
    times = np.arange(50) / 5
    c = 800 + rng.normal(0, 15, 50)  # face shaking a little around one spot
    c[25:] += 300                    # then a real move
    c[[5, 30]] = np.nan              # missed samples
    path = T.smooth_path(times, c, 600, 0, 1320, 30, 10, {"deadband": 0.06, "smooth_s": 0.7, "max_speed": 0.5})
    j = T.jitter(path)
    assert j["max_step_px"] <= 0.5 * 600 / 30 + 1
    assert j["reversals"] <= 2
    assert abs(path[-1] - (1100 - 300)) < 40  # ends centred on the moved face


def test_find_blob_needs_one_compact_moving_thing():
    cfg = load("style")["marks"]
    acc = np.random.default_rng(0).random((180, 320)).astype(np.float32)
    acc[80:95, 150:165] += 60
    res, why = M.find_blob(acc, (0, 0, 320, 180), cfg)
    assert res and abs(res[0] - 157) < 3 and abs(res[1] - 87) < 3
    spread = np.random.default_rng(0).random((180, 320)).astype(np.float32) * 50
    assert M.find_blob(spread, (0, 0, 320, 180), cfg)[0] is None


def test_hand_mark_on_finished_clip_maps_to_output_pixels():
    geo = {"game": ((0, 0, 100, 100), (0, 0, 1080, 1920)), "out_wh": (1080, 1920)}
    o = M.to_output({"t": 1, "x": 0.5, "y": 0.25, "r": 0.1, "space": "out"}, geo, 1920, 1080)
    assert (o["x"], o["y"], o["r"]) == (540, 480, 108)
    ev = M.ass_events([o], load("style")["marks"], 1080, 1920)
    assert ev and all(e.startswith("Dialogue:") for e in ev)


@pytest.mark.skipif(not HAVE_FF, reason="ffmpeg-full missing")
def test_render_with_all_extras_on(tmp_path, monkeypatch):
    from studio.render import vertical
    monkeypatch.setattr(M, "MANUAL", tmp_path / "marks.json")
    M.set_manual("t", [{"t": 1.5, "x": 0.5, "y": 0.3, "r": 0.08, "space": "out"}])
    st = on(load("style"), "sound_effects", "marks", "face_track")
    st["sound_effects"]["intro_whoosh"] = True  # exercise the audio mix even with no loud moment
    info = {}
    path, lay, _ = vertical.render({"path": str(FIX), "streamer": None}, 0.5, 3.5, [W("hello", 1, 1.4)], [],
                                   None, "t", "B", st, tmp_path, info)
    out = subprocess.run([L.tools()[1], "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    assert "video" in out and "audio" in out
    assert info["marks"]["how"] == "by hand" and len(info["marks"]["placed"]) == 1
    assert any(e["kind"] == "intro" for e in info["sfx"])


def test_swear_words_are_masked_like_f_stars_ing():
    from studio.render.captions import mask
    roots = load("style")["captions"]["mask_roots"]
    assert mask("FUCKING", roots) == "F***ING"
    assert mask("MOTHERFUCKER", roots) == "MOTHERF***ER"
    assert mask("SHIT!", roots) == "S***!"
    assert mask("HELLO", roots) == "HELLO"
