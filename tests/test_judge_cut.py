from studio.judge.llm import extend_short, prompt, snap_end

SEGS = [{"start": 0.0, "end": 2.0, "text": "Open the map."}, {"start": 2.5, "end": 6.0, "text": "Wait, the map is huge."}]


def test_end_inside_a_line_moves_to_its_end():
    assert snap_end(SEGS, 4.0, 0.8) == 6.8


def test_end_between_lines_only_gets_the_pad():
    assert snap_end(SEGS, 2.2, 0.5) == 2.7


def test_prompt_names_streamer_and_lengths_from_config():
    p = prompt("[0.0-2.0] hi", streamer="Lacy")
    assert "The streamer is Lacy." in p and "names the streamer" in p
    assert "no fixed length" in p and "15-30 s" in p and "up to 60 s" in p
    assert "streamer is" not in prompt("[0.0-2.0] hi")


def test_short_clip_is_extended_through_continuous_talk():
    segs = [{"start": 0, "end": 3, "text": "a"}, {"start": 3.5, "end": 8, "text": "b"},
            {"start": 8.5, "end": 14, "text": "c"}, {"start": 30, "end": 33, "text": "d"}]
    assert extend_short(segs, 0, 3, 12) == 14      # whole sentences, stops once long enough
    assert extend_short(segs, 0, 14, 20) == 14     # silence gap: do not jump to the next topic
    assert extend_short(segs, 0, 14, 10) == 14     # already long enough
