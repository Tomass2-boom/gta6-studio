# Handoff: S1 done, next is S2 (M2, the look), model Opus 5.5

Start the new session with: "Read PLAN.md and CLAUDE.md and .claude/handoff.md, do session S2".
Read only PLAN.md section 4 (The look) and section 9 (M2 row) for S2.

## State at end of S1 (committed in git, 2 commits)

`uv run studio doctor` is all green. `uv run pytest` passes (2 tests).
Ollama runs as a brew service. Installed: yt-dlp, streamlink, ollama (brew), mlx-whisper (uv).
Models pulled: `qwen3.8:27b` (the judge, set in config/settings.yaml) and `gemma4:26b` (spare).

### What exists
- `studio/cli.py`: `doctor`, `vod <url> [--top N] [--model M]`, `moments`; `live`/`review`/`post` are stubs.
- `studio/ingest/download.py` (yt-dlp, <=720p mp4 + info.json), `studio/detect/signals.py`
  (loudness peaks, YouTube most-replayed, merged windows capped at 90 s),
  `studio/transcribe/whisper.py` (mlx-whisper), `studio/judge/llm.py` (Ollama /api/chat;
  `format: json` is NOT supported by this build, so JSON is parsed from the reply, 3 tries),
  `studio/render/cut.py` (plain horizontal cut, M1 only), `studio/judge/compare.py` (model comparison).
- `studio/db.py`: tables `sources`, `moments` (state, signals, transcript JSON with segment times,
  judge fields j_start/j_end/score/title/hashtags/reason, clip_path).
- Config: `config/settings.yaml` (review.mode manual, judge, whisper, clip thresholds),
  `config/streamers.yaml` (empty, S5 fills), `config/style.yaml` (only `layout: A`, S2 fills).
- Test data: source id 1 = xQc GTA 5 RP VOD (https://www.youtube.com/watch?v=wEUY9mAx0dE,
  44 min, file in `data/raw/`). 16 rendered moments in `data/studio.db`, plain clips in `data/clips/`
  (named `{source}_{start}.mp4`). Old test clips are in `data/old-test-clips/` (user may delete).

### Results and findings
- 16 clips from one 44-min VOD (M1 target 10+ met), 15-60 s each. About 5 pairs overlap in time,
  so roughly 11-12 distinct moments. Dedupe is not built.
- qwen3.8:27b: 82 s for 16 moments, selective (kept 7/16), drops cutscene/chatter. gemma4:26b: 30 s,
  kept 16/16, scores not discriminating, punchier titles. Qwen chosen.
- Whisper transcript times in the DB are in source-video seconds, with segment text (word timestamps
  were requested but only segment-level are stored; S2 captions need WORD timing, so change
  `transcribe()` to store words per clip, or re-transcribe the clip range).
- Clip content was never watched; only durations and audio/video streams were checked.

## S2 job (M2: the look). Done when the user approves 5 finished clips.

Build `studio/render/` (layouts A/B/C, ASS captions) driven only by `config/style.yaml`:
- Layout A: facecam top (~35-40 % of height), gameplay below. Facecam box per streamer in
  streamers.yaml; unknown streamers: automatic face detection (find a free option first).
- Layout B: gameplay only full height. Layout C: 16:9 in the middle with white rounded title box on top.
- Captions: 1-2 words at a time, heavy rounded font, white with thick black outline, key word green/yellow,
  placed at the seam between facecam and gameplay, never over the face. Reaction tags like `*SECURITY*`
  where nobody speaks. Length 15-50 s typical, 60 max. Sound effects off by default.
- Fonts must be free-licence in `assets/fonts/` with an entry in `assets/LICENCES.md`.
- Output 1080x1920. Test on the xQc clips already in the DB; use ONE contact sheet (ffmpeg `tile`,
  ~1600 px wide) to check, full frames only where detail matters (token rule in CLAUDE.md).
- Note: the xQc VOD may have no facecam or a different layout; check one frame, and if needed add a
  second test source (a Twitch/YouTube GTA stream replay with a facecam).
- Per CLAUDE.md, tell the user before changing model/effort mid-session (resets cache).

## Open items for later sessions
- S3: duplicate-moment merge across signals, 24 h raw-recording cleanup, chat signals, live mode.
- tests/fixtures/ is empty; add a short synthetic sample (`say` + ffmpeg `testsrc`) for stage tests.
- `vod` warns that xQc is not in streamers.yaml; S5 fills the list and the `allowed` field.
- Judge titles are plain; may tune the prompt (hook style, no game name unless spoken).
