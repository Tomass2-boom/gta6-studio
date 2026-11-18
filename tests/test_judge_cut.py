from studio.judge.llm import prompt, snap_end

SEGS = [{"start": 0.0, "end": 2.0, "text": "Open the map."}, {"start": 2.5, "end": 6.0, "text": "Wait, the map is huge."}]


def test_end_inside_a_line_moves_to_its_end():
    assert snap_end(SEGS, 4.0, 0.8) == 6.8


def test_end_between_lines_only_gets_the_pad():
    assert snap_end(SEGS, 2.2, 0.5) == 2.7


def test_prompt_names_streamer_and_lengths_from_config():
    p = prompt("[0.0-2.0] hi", streamer="Lacy")
    assert "The streamer is Lacy." in p and "names the streamer" in p
    assert "8-30 s is best" in p and "up to 60 s" in p
    assert "streamer is" not in prompt("[0.0-2.0] hi")
