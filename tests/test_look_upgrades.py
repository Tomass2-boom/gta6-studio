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


def react(ev):
    return [e for e in ev if e["kind"] not in ("intro", "riser", "cut", "payoff", "layer")]


def test_shouted_lone_what_fires_calm_or_mid_sentence_does_not():
    cfg = on(load("style"), "sound_effects")["sound_effects"]
    env = _env()
    env[40:44] = -20  # 10 dB up at 4.0-4.4 s
    words = [W("so", 1.0, 1.2), W("what", 4.0, 4.3), W("okay", 6.0, 6.3)]
    ev = react(S.place(words, env, 10, cfg))
    assert [e["kind"] for e in ev] == ["bro_what"] and ev[0]["sound"] == "boom-hit-medium"
    calm = react(S.place(words, _env(), 10, cfg))
    assert calm == []
    mid = react(S.place([W("what", 4.0, 4.2), W("is", 4.22, 4.3), W("that", 4.32, 4.4)], env, 10, cfg))
    assert mid == []  # "what is that" is a question, not a "bro what"


def test_effects_are_capped_and_spaced_and_off_does_nothing():
    st = on(load("style"), "sound_effects")
    cfg = st["sound_effects"]
    env = _env(300)
    for i in (20, 60, 90, 200):  # four loud bursts (screams), two of them 3 s apart
        env[i:i + 5] = -5
    env[120:124] = -5
    ev = react(S.place([], env, 30, cfg))
    assert len(ev) == cfg["max_per_clip"]
    assert all(b["t"] - a["t"] >= cfg["min_gap_s"] for a, b in zip(ev, ev[1:]))
    assert S.place([], env, 30, load("style")["sound_effects"]) == []


def test_fail_phrase_lands_at_its_end():
    cfg = on(load("style"), "sound_effects")["sound_effects"]
    env = _env()
    env[30:40] = -18
    ev = react(S.place([W("no", 3.0, 3.2), W("no", 3.3, 3.5), W("no", 3.6, 3.9)], env, 10, cfg))
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


def test_riser_builds_into_the_payoff_and_whooshes_open_and_cut():
    cfg = on(load("style"), "sound_effects")["sound_effects"]
    env = _env()
    env[50:54] = -20
    ev = S.place([W("so", 1.0, 1.2), W("what", 5.0, 5.3)], env, 10, cfg, cuts=[7.5])
    k = {e["kind"]: e for e in ev}
    assert k["riser"]["t"] == k["bro_what"]["t"] == 5.0 and k["riser"]["sound"] == "riser-2s-strong"
    assert "intro" in k and k["cut"]["t"] == 7.5
    early = S.place([W("what", 1.5, 1.8)], env * 0 - 30 + (np.arange(len(env)) // 1 == 15) * 10, 10, cfg)
    assert "riser" not in {e["kind"] for e in early}  # no room for a riser before 1.5 s


def test_scream_hit_lands_in_the_gap_after_it():
    cfg = on(load("style"), "sound_effects")["sound_effects"]
    env = _env()
    env[30:40] = -5   # scream 3.0-4.0 s
    env[40:43] = -24  # tails off, still raised
    ev = [e for e in S.place([], env, 10, cfg) if e["kind"] == "scream"]
    assert ev and ev[0]["t"] == 4.3 and ev[0]["sound"] == "boom-drop-strong"


def test_marks_only_on_hook_or_cue():
    cfg = load("style")["marks"]
    words = [W("oh", 0.3, 0.4), W("he's", 0.5, 0.7), W("running", 0.8, 1.2), W("ok", 6, 6.2),
             W("look", 9.0, 9.2), W("at", 9.25, 9.3), W("that", 9.35, 9.6), W("look", 10, 10.2), W("at", 10.3, 10.4), W("that", 10.5, 10.7)]
    tr = M.triggers(words, 30, cfg)
    assert [t["why"] for t in tr] == ["hook", 'cue "look at that"'] and tr[0]["text"] == "oh he's running"
    assert M.triggers([W("ok", 6, 6.2), W("cool", 7, 7.2)], 30, cfg) == []  # nothing said: no mark


def test_vision_reply_gate():
    cfg = load("style")["marks"]
    assert M.parse_box({"target": "runner", "box": [400, 300, 480, 500], "sure": 0.9}, cfg)[0] is not None
    assert M.parse_box({"target": None}, cfg)[0] is None
    assert M.parse_box({"target": "runner", "box": [400, 300, 480, 500], "sure": 0.4}, cfg)[0] is None
    assert M.parse_box({"target": "street", "box": [0, 0, 1000, 900], "sure": 0.9}, cfg)[0] is None  # too big
    # grounded: the quote must be in what was said and must name the target
    good = {"target": "the runner", "quote": "he's running", "box": [400, 300, 480, 500], "sure": 0.9}
    assert M.parse_box(good, cfg, "oh he's running away")[0] is not None
    assert M.parse_box({**good, "quote": "what is wrong with this"}, cfg, "what is wrong with this oh yeah")[0] is None
    assert M.parse_box({**good, "target": "red car", "quote": "that car"}, cfg, "look at that car bro")[0] is not None
    assert M.parse_box({**good, "quote": "he's flying"}, cfg, "oh he's running")[0] is None  # not said


def test_following_mark_draws_moving_circle_and_arrow():
    cfg = load("style")["marks"]
    path = [(i / 10, 400 + 10 * i, 800, 70) for i in range(30)]
    ev = M.ass_events([{"t": 0, "path": path}], cfg, 1080, 1920)
    assert len(ev) == 29 * 3 and all("\\move(" in e for e in ev)


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


def test_title_box_with_one_very_long_word_does_not_hang():
    from studio.render.captions import build_ass
    st = load("style")
    geo = {"caption_y": 1500, "caption_y_b": 1500, "video_top": 656}
    ass = build_ass([], [], st, "C", geo, 5, "SUPERCALIFRAGILISTICEXPIALIDOCIOUSLY " * 2 + "NPC")
    assert "Dialogue" in ass


def test_voice_dips_under_hits_only():
    ev = [{"t": 2.0, "sound": "boom-hit-medium", "gain_db": 2, "kind": "bro_what"},
          {"t": 0.12, "sound": "whoosh-medium-hard", "gain_db": 0, "kind": "intro"}]
    _, g = S.audio_graph(ev, "loudnorm=I=-14", duck_db=5)
    assert "between(t,1.98,2.25)" in g and "0.12" not in g.split("[voice]")[0]
    _, g0 = S.audio_graph(ev, "loudnorm=I=-14")
    assert "volume='1-" not in g0


def test_long_title_shrinks_instead_of_being_cut():
    from studio.render.captions import build_ass
    st = load("style")
    geo = {"caption_y": 1500, "caption_y_b": 1500, "video_top": 656}
    ass = build_ass([], [], st, "C", geo, 5, "NPC Confrontation Escalates to Shirt Removal")
    assert "REMOVAL" in ass.upper()
