# Handoff: S3 (M3, live mode) built; next is S4 (review page + posting) or S5 (streamer list), Sonnet 5.5

Start the new session with: "Read PLAN.md and CLAUDE.md and .claude/handoff.md, do session S4" (or S5).

## DEADLINE
GTA 6 unlocks at midnight local time: NZ Wed Nov 18 11:00 UTC, AU east 13:00 UTC, Central Europe 23:00 UTC,
UK Thu 00:00 UTC, US Thu 05:00 UTC. Live mode must run on NZ/AU streams by Wed 11:00 UTC.

## S3 built: `studio live [--channels lirik:A,gaules:B] [--model m]`
- `studio/chat/twitch.py` anonymous IRC reader (plain asyncio socket, justinfan, reconnects; every chat saved to
  `data/chat/<chan>-<date>.jsonl`, usable as test fixtures / analytics).
- `studio/detect/chat.py` ChatDetector: per-channel baseline (median of last 10 min, floor 2), spike = latest 10 s bin
  >= 3x AND last 30 s >= 1.8x AND weight >= 12; hype words weigh 3 (list in `settings.yaml live.chat`). 2 min warm-up.
- `studio/ingest/live.py`: Tier A = `streamlink -O | ffmpeg -f segment` into `data/raw/live/<chan>/<epoch>.ts` (10 s
  chunks named by wall-clock; cut by time, a gap of >3 s (Twitch ad break) ends the run, only the unbroken run
  around the spike is used, dropped if < 15 s). Tier B = `streamlink --hls-live-restart` at the spike: Twitch's
  rewind is only ~30-50 s (measured), so it starts immediately and records forward to 80 s total.
- `studio/live.py` orchestrator: spike -> source row (url `...#live=<epoch>`) + moment -> `pipeline.process()`
  (transcribe -> judge -> render; extracted from `vod`, shared) in one worker thread. Duplicate spikes of a channel within
  60 s merge into one moment (signals.meta.merged_spikes). Loudness peaks in the segment add a second signal.
  Logs `CLIP READY ... s after the chat spike`. Cleanup deletes raw + segments older than 24 h.
- Only Twitch. YouTube/Kick live = S7. `studio live` with no `--channels` reads Twitch entries from streamers.yaml (S5).
- Tests: `uv run pytest` (15 pass) incl. saved synthetic chat `tests/fixtures/chat_spike.jsonl`.
- Rehearsal 2026-11-17 on live lirik (Tier A) + gaules (Tier B) with injected spikes: Tier A cut 60 s in <1 s, no clip (non-GTA, judge dropped) 46 s after spike;
  Tier B fetched 80 s in 53 s, 69 s after spike. Real chat detection not yet seen on live GTA chat.

## Title look change (user request this session)
Layout C title no longer a white box: big uppercase outlined text, lines alternate white/red (matches the user's Shorts examples), pop-in.
`style.yaml layout_c.title_box.style: text` (set `box` for the old one). User's reference screenshots were .heic in
a protected folder and could not be read; if they want it closer to specific examples, they should save them as PNG
in the project folder.

## Open items
- Tune `live.chat` thresholds on real GTA chat during the rehearsal (burst_x, min_burst).
- Chat spike -> clip latency target 5 min: pipeline (Whisper + 27B judge + render) not timed under load; see log.
- The Tier A ad-gap logic is tested on synthetic chunk times only (no real ad break seen).
- Captions: Whisper is the limit; WhisperX alignment is the upgrade path. No muted-radio option (music risk).
- `studio render` does not re-check the blocklist; S4 must check at post time.
- Segments/raw deleted after 24 h, so re-rendering an old live moment will fail.
