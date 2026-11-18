"""Control app (studio review): grades, clips, settings files, log wording, start/stop."""
import shutil
import sys
import time
from pathlib import Path

import pytest

from studio import db
from studio.config import ROOT
from studio.review import activity, clips, runner
from studio.review import settings_io as S


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    con = db.connect()
    clip = tmp_path / "clips" / "7_100.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"\0" * 5000)
    con.execute("INSERT INTO sources(id,url,platform,streamer) VALUES(7,'https://kick.com/x','kick','N3on')")
    con.execute("INSERT INTO moments(id,source_id,state,score,title,hashtags,clip_path,j_start,j_end,judge_model) "
                "VALUES(5,7,'rendered',8,'He FUCKING jumped','#gta6 #fail',?,100,140,'qwen')", (str(clip),))
    con.execute("INSERT INTO moments(id,source_id,state,title,clip_path) VALUES(6,7,'rendered','gone','/nope.mp4')")
    con.commit()
    return con


def test_clip_list_grade_and_posted(tmpdb):
    lst = clips.list_clips(tmpdb)
    assert [c["id"] for c in lst] == [5]  # missing file is skipped
    clips.grade(tmpdb, 5, score=9)
    clips.grade(tmpdb, 5, notes="great hook")
    g = dict(tmpdb.execute("SELECT * FROM grades WHERE moment_id=5").fetchone())
    assert (g["score"], g["notes"], g["streamer"], g["platform"], g["url"]) == (9, "great hook", "N3on", "kick", "https://kick.com/x")
    assert (g["j_start"], g["judge_score"], g["yt_views"]) == (100, 8, None)
    with pytest.raises(ValueError):
        clips.grade(tmpdb, 5, score=11)
    clips.set_posted(tmpdb, 5, "tiktok", True)
    clips.set_posted(tmpdb, 5, "tiktok", True)
    assert clips.get(tmpdb, 5)["posted"] == ["tiktok"]
    clips.set_posted(tmpdb, 5, "tiktok", False)
    assert clips.get(tmpdb, 5)["posted"] == []


def test_texts_masked_and_tagged(tmpdb):
    c = clips.get(tmpdb, 5)
    assert c["title"] == "He F***ING jumped"
    assert "F***ING" in c["description"] and "N3on" in c["description"] and "Kick" in c["description"]
    assert c["hashtags"].split() == ["#gta6", "#fail", "#shorts"]


def test_server_video_range_and_grade(tmpdb):
    from studio.review.server import app
    cl = app.test_client()
    assert cl.get("/api/ping").json["ok"]
    r = cl.get("/video/5", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100
    assert cl.get("/video/6").status_code == 404
    assert cl.post("/api/clips/5/grade", json={"score": 7}).json["ok"]
    assert cl.get("/api/clips/5").json["clip"]["grade"] == 7


@pytest.fixture
def cfgcopy(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.setattr(S, "ROOT", tmp_path)
    return tmp_path / "config"


def test_yaml_roundtrip_keeps_file(cfgcopy):
    for name in ("streamers",):
        assert S.dumps(S.read(name)) == (cfgcopy / f"{name}.yaml").read_text()


def test_switch_changes_one_line(cfgcopy):
    before = (cfgcopy / "style.yaml").read_text()
    was = S.get_switch("marks")
    S.set_switch("marks", not was)
    after = (cfgcopy / "style.yaml").read_text()
    assert S.get_switch("marks") is (not was)
    diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(diff) == 1 and "enabled:" in diff[0][0] and "#" in diff[0][1]  # comment kept
    S.set_switch("marks", was)
    assert (cfgcopy / "style.yaml").read_text() == before


def test_streamer_edits_keep_comments(cfgcopy):
    S.add_streamer("", "https://kick.com/TestGuy", None, "A")
    e = next(x for x in S.streamers() if x["name"] == "TestGuy")
    assert (e["platform"], e["channel"], e["tier"], e["live"]) == ("kick", "TestGuy", "A", True)
    with pytest.raises(ValueError):
        S.add_streamer("TestGuy", "kick.com/TestGuy")
    S.set_streamer("TestGuy", live=False)
    assert not next(x for x in S.streamers() if x["name"] == "TestGuy")["live"]
    S.set_blocked("Baddie", True)
    assert "Baddie" in S.blocklist()
    S.set_blocked("baddie", False)
    S.remove_streamer("TestGuy")
    text = (cfgcopy / "streamers.yaml").read_text()
    assert "TestGuy" not in text and "Baddie" not in text
    assert "# S5 fills this" in text and "allowed: null" in text


def test_parse_link():
    assert S.parse_link("https://www.youtube.com/@Jelly") == ("youtube", "Jelly")
    assert S.parse_link("twitch.tv/xqc") == ("twitch", "xqc")
    assert S.parse_link("n3on") == (None, "n3on")


def test_log_lines_in_plain_english():
    assert activity.plain("SPIKE(chat) n3on x9.5 (burst 19.00 vs normal 2.00, hype 79%)") == ("hype", "Chat went wild for n3on (10× normal)")
    assert activity.plain("moment 59 kept (7) When the Heist Setup Goes Wrong")[0] == "good"
    assert activity.plain("moment 59 CLIP READY (formal), 81s after the spike")[0] == "ready"
    assert activity.plain("xqc recorder stopped (offline?)") is None
    assert activity.plain("moment 59 rendered, layout C (x): /a.mp4") is None
    assert activity.plain("  File \"x.py\", line 3") is None


def test_doctor_plain():
    from studio.review.server import doctor_plain
    rows = doctor_plain("OK   ffmpeg \nOK   ffmpeg with libass (captions) /opt/x\nFAIL ollama server (run: brew)\nOK   disk 120 GB free\n")
    assert [r["ok"] for r in rows] == [True, True, False, True]
    assert "captions" in rows[1]["text"] and "Ollama" in rows[2]["text"] and "120 GB" in rows[3]["text"]


def test_start_stop_is_ctrl_c(tmp_path, monkeypatch):
    """Stop must reach the job as Ctrl-C (KeyboardInterrupt), even if we were started ignoring it."""
    import signal
    monkeypatch.setattr(runner, "DATA", tmp_path)
    flag = tmp_path / "got-ctrl-c"
    code = ("import time\ntry:\n    while True: time.sleep(0.05)\nexcept KeyboardInterrupt:\n"
            f"    open({str(flag)!r}, 'w').write('ok')\n")
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)  # like a server started from a background shell
    try:
        runner.start("dummy", [sys.executable, "-c", code], "Dummy")
    finally:
        signal.signal(signal.SIGINT, old)
    time.sleep(0.5)
    assert runner.status("dummy")["running"]
    with pytest.raises(RuntimeError):
        runner.start("dummy", [sys.executable, "-c", code])
    assert runner.stop("dummy", wait_s=5)
    for _ in range(50):
        if not runner.status("dummy")["running"]:
            break
        time.sleep(0.1)
    assert not runner.status("dummy")["running"]
    assert flag.exists()
    assert Path(runner.info("dummy")["log"]).exists()
