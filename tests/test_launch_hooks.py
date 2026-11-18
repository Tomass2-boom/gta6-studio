"""Launch-day hook titles: only GTA 6 footage, hook in front of the judge's own title, own line on the video."""
from datetime import datetime, timezone

from studio.judge import llm
from studio.render.captions import split_hook

LC = {"enabled": True, "game": "GTA 6", "from_utc": "2026-11-18T11:00:00Z", "until_utc": "2026-11-21T00:00:00Z",
      "hooks": ["GTA 6 IS ALREADY OUT", "GTA 6 JUST DROPPED"], "first_look_hook": "GTA 6 FIRST LOOK"}
AFTER = datetime(2026, 11, 18, 12, tzinfo=timezone.utc).timestamp()
BEFORE = datetime(2026, 11, 18, 10, tzinfo=timezone.utc).timestamp()
SEG = [{"start": 0, "end": 2, "text": "bro what is he doing"}]


def _cfg(monkeypatch):
    monkeypatch.setattr(llm, "load", lambda name: {"judge": {"launch": LC}})


def test_live_after_unlock_gets_hook(monkeypatch):
    _cfg(monkeypatch)
    assert llm.launch_cfg({"url": "https://kick.com/n3on#live=1"}, SEG, AFTER) == LC


def test_gta5_vod_and_pre_unlock_never_hooked(monkeypatch):
    _cfg(monkeypatch)
    assert llm.launch_cfg({"url": "https://youtu.be/x", "title": "GTA 5 funny moments"}, SEG, AFTER) is None
    assert llm.launch_cfg({"url": "https://kick.com/n3on#live=1"}, SEG, BEFORE) is None


def test_vod_naming_gta6_gets_hook(monkeypatch):
    _cfg(monkeypatch)
    seg = [{"start": 0, "end": 2, "text": "GTA six is finally here"}]
    assert llm.launch_cfg({"url": "https://youtu.be/x"}, seg, AFTER) == LC


def test_hook_title_rotates_and_first_look():
    assert llm.hook_title("Dad Grabs A Stranger", LC, False, 0) == "GTA 6 IS ALREADY OUT: Dad Grabs A Stranger"
    assert llm.hook_title("Dad Grabs A Stranger", LC, False, 1).startswith("GTA 6 JUST DROPPED")
    assert llm.hook_title("The Character Creator", LC, True, 5).startswith("GTA 6 FIRST LOOK")
    assert llm.hook_title("x", None, False, 0) == "x"


def test_split_hook():
    assert split_hook("GTA 6 IS ALREADY OUT: I'M FLYING", LC["hooks"]) == ("GTA 6 IS ALREADY OUT", "I'M FLYING")
    assert split_hook("NPC FIGHT", LC["hooks"]) == (None, "NPC FIGHT")


def test_on_video_hook_only_for_lower_rated(monkeypatch):
    _cfg(monkeypatch)
    LC["on_video_max_score"] = 6
    t = "GTA 6 JUST DROPPED: Dad Grabs A Stranger"
    assert llm.video_title(t, 6) == t          # lower rated: banner / hook line on the video
    assert llm.video_title(t, 8) == "Dad Grabs A Stranger"   # best clips stay clean
    assert llm.video_title("Plain Title", 9) == "Plain Title"
