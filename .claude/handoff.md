# Handoff: S2 (M2, the look) built, waiting for the user's approval of 5 clips

If the user approved: next is S3 (Sonnet 5.5): "Read PLAN.md and CLAUDE.md and .claude/handoff.md, do session S3".
If they asked for look changes: stay on S2 (Opus 5.5); almost everything is a value in `config/style.yaml`.

## What S2 added
- `ffmpeg-full` (Homebrew, keg-only, has libass) installed; paths in `config/settings.yaml` `render:`.
  The plain `ffmpeg` on PATH has NO libass; render code always uses `layout.tools()`.
  `doctor` now checks libass and the caption font.
- `assets/fonts/Nunito-Black.ttf` (OFL, static 900 instance), `assets/LICENCES.md`. Pillow added (text measuring).
- `studio/render/layout.py`: layouts A/B/C as filtergraphs; facecam box from `streamers.yaml`
  (`facecam: [x,y,w,h]`, `ref: [w,h]`); cam presence = the cam box's straight border edge
  (`cam_timeline`, one sample per second). Within an A clip, stretches where the cam is off
  (>= `switch_min_s`) switch to B (`build_switching`, overlay with `enable=`); captions move too.
- `studio/render/captions.py`: ASS writer: 1-2 word pop-in captions, key words coloured
  (green/yellow in turn), seam position for A; layout C white rounded title box (ASS drawing, Pillow wrap, balanced lines).
- `studio/render/vertical.py`: `render()` = layout + captions + loudnorm -14 LUFS, 1080x1920 30 fps, x264 crf 20.
- `studio/cli.py`: `studio render [ids] [--layout A|B|C]`; `vod` now renders vertical clips directly.
  Word timings stored in `moments.words`, keywords in `moments.keywords` (judge prompt returns them;
  `judge.llm.keywords()` fills missing ones), `moments.layout`.
- `streamers.yaml`: xQc with facecam box, `allowed: null` (S5 must verify). `allowed: null` now warns, `false` refuses.
- Tests: `tests/test_render.py` (8 pass total), fixture `tests/fixtures/sample.mp4` (4 s synthetic).
- `studio/render/cut.py` (plain cut) removed.

## Test clips (data/clips, from xQc VOD, source id 1)
1_1563 (A), 1_1873 (A, B during the fall), 1_2131 (A), 1_721 (A, B at the end), 1_1092 (C forced),
also 1_1752, 1_1210 (A with switching). Checked with contact sheets only, never watched.

## Open / not built
- Automatic face detection for streamers without a facecam box (they get layout B now).
  Candidate: OpenCV YuNet (opencv-python-headless); check it works on this Python first.
- Reaction tags (`*SECURITY*`) not built (`reaction_tags: false`); needs a reliable source of what happens.
- Sound effects off. Source is 720p, so cam (~4x) and gameplay (~1.7x) look soft;
  `ingest/download.py` still hard-codes <=720p (make it a setting, 1080p, if the user wants).
- Captions carry swear words as spoken; no censor list yet.
