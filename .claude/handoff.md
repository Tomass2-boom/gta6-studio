# Handoff: GTA6 Studio — S8 part 1b DONE (look upgrades, round 2); waiting on the user's verdict

## Goal
Automated GTA 6 clip production. The pipeline watches live streams and VODs, finds the best moments, and cuts vertical 1080p captioned clips for the user's own YouTube Shorts, TikTok and Instagram Reels. The aim is maximum views and being first. The NZ unlock is **Wed Nov 18, 11:00 UTC (12:00 CET, the user's time zone)**, and N3on (Kick) is the top priority. The quality bar is "good clips, good high quality ones." This session's job is the look upgrades: sound effects, circles/arrows and face tracking, all switchable in `config/style.yaml`.

## How the person wants things done
- **Token saving (global CLAUDE.md), never at the cost of quality:**
  - Read only what's needed and search first. Batch tool calls; never poll.
  - No filler.
  - Images: ONE contact sheet (ffmpeg `tile`, about 1600 px); open a full frame only when detail matters.
  - Suggest pressing **End** at about 150k tokens or when the task changes.
  - Say so before changing model or effort, because it resets the cache.
- **Project rules (CLAUDE.md):**
  - No paid services and no Anthropic API at runtime. Use local Whisper and Ollama.
  - Claude never watches raw video.
  - Prefer proven tools. Check stars, last commit and licence, and tell the user in 1–2 lines before installing.
  - Look and behaviour live in `config/*.yaml`. Secrets go in `.env`.
  - Nothing posts while `review.mode: manual`. No browser-automation uploaders.
  - No cutscene or spoiler clips. Honour the blocklist; `allowed` is a note only.
  - Commit with explicit paths, never `.DS_Store`, `phones/` or `.claude/handoffs/`.
- **This task:**
  - Ask before changing the approved caption or title look.
  - Don't edit `live.py`, `ingest/`, `chat/` or `detect/` (live mode may run).
- **The user's feedback on the previews:**
  - "Captions everything look very good." Webcam + video layout is good, and long clips are fine.
  - **Arrows/circles:** "we don't want arrows like appearing randomly." Use them only:
    - (1) as a **hook at the very start**: the opening line names a subject ("oh he's running"), so put a circle and arrow on that person and **follow them** for the first seconds, "so the viewers know what to look for";
    - (2) when the streamer says "look here", "look at that" and similar.
    - Otherwise none: "mostly you don't actually need to even use them." A zoom-in on the spot is an acceptable alternative.
  - **Sound effects:**
    - The "what" boom on `1_721` is "really good", but should be slightly louder.
    - Scream hits on `22_10` and `28_30`: "I didn't hear anything."
    - "We generally use a lot more sound effects": risers building tension, whooshes, "basic sound effect that everyone uses". Only "in the right place, because using them random places is not gonna work."
    - They want research on how GTA Shorts do it. They offered to send example Shorts.
  - **Face tracking:** the synthetic `fc2_*` demo was confusing and "doesn't follow his face." Test only on real footage.
  - **Test clips:** they want **5–10 NEW clips from new content** (a GTA 5 YouTube video or a live stream now), not the old clips "over and over". Show captions, effects, and arrows/circles and tracking where they belong. They will say which work.
- They write casually and by voice. Reply in plain English: say what works and what doesn't, and how to run it.

## Current state
- **Commits on `main`:**
  - `5560e55`: S8 part 1.
  - `d12da0a`: handoff checks.
  - `11064dc`: profanity masking, plus the feedback written into `.claude/handoff.md`.
- 40 tests pass (`uv run pytest`).
- **Built, all `enabled: false` in `config/style.yaml`.** With everything off, the ffmpeg command and filtergraphs are byte-identical to before; `tests/test_look_upgrades.py` guards this.
  - `studio/render/sfx.py`: placement from words + a 100 ms loudness envelope, kinds bro_what/fail/laugh/scream. The audio graph mixes the effects after loudnorm with `amix normalize=0`, then `alimiter`.
  - `studio/render/marks.py`: motion-blob auto (wrong concept; drop it), a manual sidecar `data/marks.json` (currently empty), and ASS circle/arrow drawing. `to_output` maps source or `space:"out"` coordinates.
  - `studio/render/facetrack.py`: YuNet at 5 fps, median filter, hold on misses, deadband, smoothing and a speed cap, output as a `sendcmd` script driving `crop@cam` / `crop@game`.
  - `studio/render/vertical.py`: `render(..., style=, out_dir=, info=)` and `ffmpeg_cmd()`.
  - `studio/render/layout.py`: `build(..., track=)` and geo keys `game`, `game_b`, `cam_crop`, `game_crop`.
  - `studio/cli.py`: `studio mark <clip> T X Y [--kind arrow] [--size] [--none|--clear]`.
- **ON by default:**
  - `captions.mask_profanity: true` with `mask_roots`. FUCKING becomes F***ING (`captions.mask()`). Approved by the user.
  - `layout_b.black_band_to_c: 0.15`: an auto-B clip from a source with a black band becomes layout C (`L.black_band`). Only clip 53 (OpTic) trips it across all 20 rendered clips.
- **Sounds:** 10 CC0 WAVs from wyzowl/sfx-kit (46★, pushed 2026-10-20) in `assets/sfx/`, metadata (sync, lufs) in `assets/sfx/sounds.yaml`, licence rows in `assets/LICENCES.md`.
- **Preview renders** are in `data/clips/preview/`, with sheets 1–3 and the fake `fc2_off/on.mp4` (fc2 can be deleted). The originals in `data/clips/` and the DB are untouched.
- The demo script was in the scratchpad (may be gone). It re-rendered DB moments with a style override (all three switches on) into `preview/` without `db.update`.

## Decisions and dead ends
- **Motion-blob auto marks: dead end.**
  - The first version circled grass, a TV and a wall on 3 of 9 clips.
  - The strict version (with a "must travel" test) fired on 0 of 9.
  - The user rejects random marks anyway. Replace it with hook/speech-cue marks.
- **Hand-placed arrow on `1_109`** looked random to the user. Don't place marks from guessed coordinates.
- **SFX levels:**
  - Effects mixed after loudnorm keep the voice level unchanged (measured ±0.1 dB).
  - Effects placed at a scream's peak are masked. Moving the hit to the scream's end and raising the gain to 0 dB was still inaudible: the measured energy rose only +0.1 to +0.7 dB.
  - Current gains: bro_what +8, fail +7, laugh +1, scream 0.
  - A lone word ("what") needs a 0.3 s pause on both sides (`isolate_s`); without it, "what" fired on most clips.
- **Face tracking:**
  - With xQc's cam box there is only about 15 px of room, so it is nearly invisible.
  - The full-screen-face mode gates at `presence: 0.4`, because xQc looks away and 0.6 failed.
  - The synthetic test confused the user. Never show synthetic demos.
- **Loudness:** one-pass loudnorm leaves some originals short of target (`1_1752` −17.0, `28_30` −19.1 LUFS). This was already the case before S8. Fixing it is a later job (two-pass); don't change it before launch.
- **Rehearsal decisions:**
  - Keep both chat and loudness triggers on Kick.
  - Don't run `studio vod` while `studio live` runs (no WAL, shared GPU).

## S8 part 1b results (2026-11-18)
- **Swear masking was never broken.** `mask()` gives F\*\*\*ING / S\*\*\*, and the video shows the stars (checked on the preview2 sheet). Chat Markdown ate the `***`. Always write masked words in backticks in replies.
- **Marks** (`studio/render/marks.py`, rewritten; motion-blob auto removed):
  - Triggers are only (1) the hook = words in the first 4 s and (2) cue phrases ("look at that", "right there"...).
  - gemma4:26b gets one frame of the gameplay crop and the words. It must return a `quote` that is really in the transcript and that names the target (a pronoun, or a word shared with the target). Then come the sure ≥ 0.7 and box-size gates.
  - TrackerMIL follows the target for 3 s at 10 fps. Circle + arrow move with `\move`.
  - Without the quote check, gemma copied the prompt's example ("the running character" for "what is wrong with this"), so never put examples in that prompt.
  - Result on 7 clips: 1 mark (31_883, butler at 15.7 s, on target). The other 6 correctly got none.
- **SFX** (`sfx.py`): intro whoosh, a whoosh on layout switches, a riser ending on the payoff, a payoff boom (strongest reaction or the loud onset), kind `hype` (808), scream hit `at: after` (in the gap). A phone-audible `layer` (impact-heavy-hard) goes on every boom, and `duck_db: 5` dips the voice for 0.25 s under hits.
  - Why the layer: booms are 30-150 Hz. Measured +13 to 23 dB there but ~0 dB in 250-2000 Hz, the band phone speakers play. That explains "didn't hear" the scream hits.
  - Metric to use: effect vs voice in 250-2000 Hz over the hit's 0.3 s (`evr`). LUFS and 0.6 s windows undercount. Now: reaction layers -1 to +21 dB, payoff layers -1 to -4 dB.
- **Face tracking on real footage** (Kai, cam box): followed on 2 of 7 clips (range 25 and 53 px, max step 2 px). Held still on 5, where he didn't move. Full-screen-face mode is still unverified on real footage (no source with one yet).
- **Bug fixes:**
  - Title-box balance loop hung forever when one word is wider than the box (`w2 >= widest` break).
  - Over-long titles shrink (down to 60%) instead of being cut at max_lines.
  - Titles are masked too.
- **New source** id 31 (Kai Cenat, zwSKvEUhSjw). Moments 86-106 are judged; 7 kept, with state left at `judged` (rendered only into preview2, no DB clip_path).
- **Streamers:** added Jelly (youtube @Jelly) and Slogo (@Slogo), tier A. The user says Jelly flies to NZ to stream early. The web only shows a joke Short plus YouTube's launch show line-up.
- **Scripts** (scratchpad, may be gone): stage1 (vod without render), stage2 (render with all extras ON into preview2), check (sheet), evr/band (levels).

## Next steps
1. The user listens to `data/clips/preview2/` (see LISTEN.md there) and says which switches go on (`sound_effects`, `marks`, `face_track` in style.yaml).
2. Then the full test (Sonnet 5.5) and launch Wed 11:00 UTC with `uv run studio live`.
3. Later: AI-voiceover commentary videos (the user's other format; parked until after clipping works).

## Waiting on the person
- Verdicts on the new 5–10 clip batch, then which S8 switches to turn on.
- Older items:
  - approval to make posting public (`review.mode` is still `manual`);
  - platform account setup (S4);
  - the YouTube launch show URL.

## Key facts
- **Project:** `/Users/tomasspesikaspetersons/Claude Projects/GTA6 Studio`.
- **ffmpeg:** use ffmpeg-full at `/opt/homebrew/opt/ffmpeg-full/bin/` via `render.layout.tools()`.
- **Shell:** zsh doesn't word-split, so use `bash -c` for loops. Foreground `sleep` is blocked. `rm` inside `bash -c` is blocked by a safety check.
- **Ollama models:** `qwen3.8:27b` (judge) and `gemma4:26b`. Whisper: `mlx-community/whisper-large-v3-turbo`.
- **Clip name** = `{source_id}_{int(j_start)}`. Moments have `words` and `keywords` JSON in the DB.
- **Moments used in the previews:** 4, 5, 13, 14, 18, 22, 36, 53, 59 (clips `1_1873`, `1_1752`, `1_109`, `1_721`, `1_1563`, `1_1969`, `5_11`, `22_10`, `28_30`).
- **Finding live streams:**
  - Twitch: GQL `https://gql.twitch.tv/gql`, Client-ID `kimne78kx3ncx6brgo4mv6wki5h1ko`.
  - Kick: `curl -A Mozilla/5.0 https://kick.com/api/v2/channels/<slug>`.
  - YouTube live search: `yt-dlp --flat-playlist 'https://www.youtube.com/results?search_query=gta+5+live&sp=EgJAAQ%253D%253D'`.
- **Session models:**
  - Opus 5.5: the look (this work).
  - Sonnet 5.5: plumbing.
  - Haiku 5.5: web lookups.
  - Fable: plan changes.
