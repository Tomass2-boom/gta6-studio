# Handoff: S2 done (look approved), next is S3 (M3, live mode), model Sonnet 5.5

Start the new session with: "Read PLAN.md and CLAUDE.md and .claude/handoff.md, do session S3".
Read only PLAN.md sections 2, 3 ("How a moment is detected"), 4b (launch timing), 9 (M3 row), 10.

## DEADLINE
GTA 6 unlocks at midnight local time: **New Zealand Wed Nov 18 11:00 UTC**, AU east 13:00 UTC,
Central Europe 23:00 UTC, UK Thu 00:00 UTC, US Thu 05:00 UTC. Live mode must run on NZ/AU
streams by Wed 11:00 UTC. Rehearse on live GTA 5 streams before that. Build the Twitch path first.

## S3 job (M3): done when a clip is ready within 5 minutes of a live chat spike
- `studio/chat/twitch.py`: anonymous Twitch IRC reader (justinfan login, no account), messages per
  10 s per channel, own baseline per channel (short burst + 30 s sustained), weight LOL/W/OMG/"clip it"/
  laugh emotes higher (PLAN section 3 "How a moment is detected").
- Tier A (top streamers): streamlink records continuously to `data/raw/` (rolling, deleted after 24 h).
  Tier B: on a spike, fetch only the last ~90 s (streamlink/yt-dlp live segment). Twitch ads leave gaps;
  ignore those windows.
- On a spike: insert a moment (signals JSON, streamer, URL, timestamps) -> transcribe -> judge ->
  render, using the existing stages. Reuse `cli._render_one()` / `studio/render/vertical.render()`.
- Merge duplicate moments (same streamer, overlapping windows, chat + loudness both firing).
- `studio live` reads `config/streamers.yaml` (S5 fills it; NZ/AU first). Long runs in the background.
- Test chat logic on a saved chat log fixture in `tests/fixtures/`, not a live stream.

## What exists (S1 + S2)
- `studio doctor` (checks ffmpeg-full/libass, font, Ollama, model), `studio vod <url>`,
  `studio render [ids] [--layout A|B|C]`, `studio moments`. `live`/`review`/`post` are stubs.
- Pipeline: yt-dlp download (`settings.ingest.max_height: 1080`, H.264 preferred) -> signals
  (loudness, YouTube most-replayed) -> mlx-whisper -> Ollama judge `qwen3.8:27b` (no `format: json`
  support; JSON parsed from reply) -> vertical render.
- Render: ALWAYS use `layout.tools()` (`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`); the plain ffmpeg on
  PATH has no libass. Layouts A (facecam top, auto-switch to B where the cam is off) / B / C (title box),
  `layout_mix` (20 % C, for analytics). All look values in `config/style.yaml`. User approved the look.
- Facecam boxes per streamer in `streamers.yaml` (`facecam: [x,y,w,h]`, `ref: [w,h]`). Streamers without
  one get layout B until S2b (automatic facecam detection, proposed as its own session) is done.
- Whisper settings that fixed caption problems (keep them): `condition_on_previous_text: false`,
  `initial_prompt` with screams (otherwise Whisper skips AAAAH/laughs), 2 s padding, `drop_loops()`
  (5+ repeats), `max_word_chars: 10`, retries on fallback. Do NOT turn on `hallucination_silence_s`:
  it dropped screams. Captions: `max_word_s` timing fix, `lead_s` 0.1, `never_highlight` swears/filler.
- DB `moments`: words (JSON, source seconds, with confidence p), keywords, layout, clip_path.
- User decision 2026-11-17: `allowed` in streamers.yaml is a note only, never blocks; the user decides what
  gets posted. Blocklist still refuses. Nothing posts publicly while `review.mode: manual`.
- Tests: `uv run pytest` (10 pass). Fixture `tests/fixtures/sample.mp4`.
- Test data: xQc VOD (source id 1, 1080p60, `data/raw/wEUY9mAx0dE.mp4`); 16 rendered clips in `data/clips/`
  (user checked them: very good). Old 720p copy `data/raw/wEUY9mAx0dE.720p.mp4` can be deleted.

## Open items (not S3 unless needed)
- Captions: user says mostly fine, a few minor slips. Whisper is the limit; WhisperX forced alignment is
  the upgrade path if timing complaints come back.
- No muted-radio option (music copyright risk). Reaction tags and sound effects not built.
- `studio render` does not re-check the blocklist; S4 must check at post time.
- The `vod` path was not re-run end to end after S2 changes (render was tested via `studio render`).
- Clips are ~40-50 MB (crf 20, 1080x1920); fine for platforms, too big for the phone preview.
