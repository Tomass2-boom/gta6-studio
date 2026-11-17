# Handoff: GTA6 Studio, S8 part 1 (look upgrades) done, next is the full test

## Goal
Automated GTA 6 clip production: the pipeline watches live streams and VODs, finds the best moments, and cuts vertical captioned 1080p clips for the user's own YouTube Shorts, TikTok and Instagram Reels. The aim is maximum views. Be first: the NZ unlock is **Wed Nov 18 11:00 UTC (12:00 CET, the user's time zone)**. N3on (Kick) is the top priority. Quality comes first: "good clips, good high quality ones."

## How the person wants things done
- **Token saving (global CLAUDE.md), never at the cost of quality:**
  - Read only what's needed and search first.
  - Batch tool calls; don't poll (use Monitor or a background until-loop).
  - No filler.
  - Images: ONE contact sheet (ffmpeg `tile`, about 1600 px); open a full frame only when detail matters.
  - Suggest pressing **End** at about 150k tokens or when the task changes.
  - Say so before changing model or effort (it resets the cache).
- **Project hard rules (CLAUDE.md):**
  - No paid services and no Anthropic API at runtime (local Whisper and Ollama only).
  - Claude never watches raw video.
  - Prefer proven tools: check stars, last commit and licence, and tell the user before installing.
  - Look and behaviour live in `config/*.yaml`.
  - Secrets go in `.env`.
  - Nothing posts while `review.mode: manual`.
  - No browser-automation uploaders.
  - No cutscene or spoiler clips.
  - Honour the blocklist. `allowed` is a note only and never blocks.
  - Commit at each milestone, with explicit paths (never `.DS_Store`, `phones/` or `.claude/handoffs/`).
- **Look:** approved. Plumbing sessions must not change `render/` or `style.yaml`.
- **Talking to the user:** they write casually and by voice. Use plain English: say what works and what doesn't, how to run each step, and which session and model to run next.

## S8 part 1 (2026-11-17, Opus 5.5): look upgrades, all OFF until the user approves
Switches in `config/style.yaml` (all `enabled: false`; with all off the ffmpeg command and filtergraphs are byte-identical to before, guarded by `tests/test_look_upgrades.py`):
- **`sound_effects`** (`studio/render/sfx.py`): 10 CC0 WAVs from wyzowl/sfx-kit in `assets/sfx/` (metadata in `assets/sfx/sounds.yaml`, licence rows in `assets/LICENCES.md`). Kinds: bro_what (boom), fail (womp), laugh (pop, only written "haha/lol", Whisper drops real laughs), scream (hit as a loud burst ends). Word triggers need the voice ≥6 dB over the clip median; a lone "what" needs a 0.3 s pause around it. Max 2 per clip, 6 s apart. Mixed AFTER loudnorm (voice level unchanged, measured ±0.1 dB), then a limiter. On 9 test clips: 4 got effects. Levels are a guess: measured energy only +0.1 to +2.5 dB, so they may be too quiet. **The user must listen** to `data/clips/preview/{22_10,1_721,1_1752,28_30}.mp4`.
- **`marks`** (`studio/render/marks.py`): circle or arrow, drawn in the ASS file. Auto = one compact blob that moves on its own (camera pan removed) and travels ≥1× its size; first version circled grass/a TV/a wall on 3 of 9, the strict version fires on 0 of 9. By hand: `uv run studio mark <clip> T X Y [--kind arrow] [--size 0.1]` (X, Y = 0–1 on the finished clip), `--none`, `--clear`; stored in `data/marks.json`, overrides auto. Demo: circle on `1_1563`, arrow on `1_109`.
- **`face_track`** (`studio/render/facetrack.py`): YuNet at 5 fps, median filter, hold on misses, deadband, smoothing, speed cap, driven by `sendcmd` on a named crop. Layout A: the cam crop follows the face inside the box (xQc's box only leaves ~15 px of room). No box + a big face in ≥40% of samples (full-screen camera): the B crop follows the face. Simulated moving cam: face off-centre median 285 → 93 px, max step 7 px/frame.
- **Clip 53 fix:** auto B now becomes C when the source has a black band ≥15% of its height (`layout_b.black_band_to_c`; `L.black_band`). This is ON by default (only OpTic multi-POV sources hit it so far).
- Demo renders and three contact sheets: `data/clips/preview/` (originals in `data/clips/` untouched; the DB was not changed).
- Not done: profanity masking in captions (asked the user; it changes the caption look).

## Current state
- Commits on `main`:
  - `a30255c`: rehearsal.
  - `bc10ff4`: emote spam is never capped.
  - `b81ffa8`: handoff note on the Kick decision.
- 29 tests pass.
- **Rehearsal (PLAN §13 step 1) is DONE.** Each platform made a clip from a real trigger:

| Platform | Moment | Clip | Trigger | Title |
|---|---|---|---|---|
| Kick (ramee) | 36 | `data/clips/5_11.mp4` | loudness | "100 Subs Launches You 1000 Feet In The Air", score 8 |
| YouTube (OpTic) | 45 | `data/clips/14_0.mp4` | loudness | score 8 |
| YouTube (OpTic) | 53 | `data/clips/22_10.mp4` | chat | score 8 |
| Twitch (formal) | 59 | `data/clips/28_30.mp4` | chat (Tier B rewind) | "When the Heist Setup Goes Wrong" |

- Spike to finished clip: about 43 s mean.
- **VOD mode** works on YouTube, Twitch and Kick. The Twitch and Kick tests gave 0 clips: loudness alone picks chatter on RP streams, and 12 moments judged were all dropped.
- The full details are in the top block of `.claude/handoff.md`. The PLAN §13 status table is updated.

## Decisions and dead ends
- **Copy-paste raid cap** (`detect/chat.py`, `live.chat.repeat_cap: 3`, `repeat_min_words: 3`).
  - A message of 3+ words with the same first words counts at most 3 times per 10 s bin.
  - Exempt: one repeated token, or all hype words ("KEKW KEKW KEKW" is the real spike in big chats).
  - Reason: a "fuck your mod" raid made a fake 38× spike.
  - Fixture: `tests/fixtures/chat_raid.jsonl`.
- **Kick chunk gaps were false.** Kick delivers in bursts, so chunk names lag the content by up to 30 s and then catch up. Nothing is lost.
  - Fix: `ingest/live.chunk_len()` (ffprobe audio packets) + `contiguous_run(lengths=)`. A hole is now a lag that stays raised.
  - Setting: `live.hole_s: {kick: 10}`; others keep the fixed 10 s grid.
  - 5 s was too tight, because normal jitter is ±5 s.
- **yt-dlp's kick:vod extractor is broken** (Kick v1 API 404).
  - `ingest/download.kick_vod()` reads `api/v2/channels/<slug>/videos` via curl, with 3 retries, because Kick returns 403 to Python and the API sometimes answers empty.
  - It downloads the HLS `source` and only finds a channel's latest videos.
- **`--channels kick/x`** now looks up its chatroom itself: `chat/kick.lookup_chatroom`.
- **Tuning**, from an offline replay of saved chat (`data/chat/*.jsonl`) and chunk loudness through the real detector classes:
  - Chat `burst_x` 3.0 → 4.0. A busy Kick chat went from 15/h to 8/h, and the 6–9× spikes are kept.
  - Loud: `z` binds, not `min_ratio` (real bursts are 3.5–6×). z stays at 4.0; z 4.5 lost a kept clip. `cooldown_s` 20 → 90.
  - The replay script lived in the scratchpad (not saved).
- **The user decided: keep both chat and loudness triggers on Kick.**
  - This gives about 17 fires/h on ramee, against a budget of 6–10 per channel (one worker handles about 80/h in total).
  - The fallback, if the queue backs up: add `kick` to `CHAT_READERS` in `studio/live.py`.
- **Don't run `studio vod` while `studio live` runs:** `db.connect()` has no WAL/busy_timeout, and both use the GPU.

## Next steps
1. ~~S8 part 1~~ DONE (see the block above). Before the full test, the user decides: which switches to turn on (`sound_effects.enabled`, `marks.enabled`, `face_track.enabled`), whether the effect levels sound right, and whether to mask profanity.
   - Sound effects, circles/arrows, face tracking, all switchable in `style.yaml`. Free-licence sounds only, listed in `assets/LICENCES.md`.
   - Also fix:
     - (a) Clip 53 (`22_10.mp4`, OpTic multi-POV overlay): a large empty black band on top. The facecam/layout B misread the overlay.
     - (b) Uncensored profanity in captions ("FUCKING" in clip 59): decide whether to mask it, which is a platform risk.
2. **Full test (Sonnet 5.5)** with the new look on, plus a launch checklist.
3. **Warm-up clips (Sonnet 5.5)** from YouTube GTA 6 talk videos: the judge gets a "talk" mode.
4. **Wed 11:00 UTC:** start `uv run studio live` in the background.
   - Fallback: `--channels kick/n3on:A,...` with whoever is live.
5. **S4 (Sonnet 5.5), after launch starts:** review page and posting. It must not edit `live.py`, `ingest/` or `pipeline.py` while live runs.
6. **Later:**
   - S9: chat replay as a VOD signal.
   - S6: campaigns.
   - S8 part 2: analytics, German.
   - The judge's handling of RP moments: it drops most for having "no gameplay action".

## Waiting on the person
- Approval to make posting public (`review.mode` is still `manual`).
- Platform account setup (during S4).
- Older items:
  - whether the layout C title look is good;
  - whether IShowSpeed or the YouTube launch show URL is known (the show entry has `live: false`).

## Key facts
- **Project:** `/Users/tomasspesikaspetersons/Claude Projects/GTA6 Studio`.
- **ffmpeg:** use ffmpeg-full at `/opt/homebrew/opt/ffmpeg-full/bin/` via `render.layout.tools()`.
- **Models:** judge `qwen3.8:27b` (Ollama); Whisper `mlx-community/whisper-large-v3-turbo`.
- **Commands:** `uv run studio doctor | vod <url> [--top N] | live [--channels kick/x:A,youtube/Handle:A,twitchname:B] | render | review | post`. `review` and `post` are stubs.
- **Finding live streams:**
  - Twitch: GQL `https://gql.twitch.tv/gql` with Client-ID `kimne78kx3ncx6brgo4mv6wki5h1ko`, `game(name:"Grand Theft Auto V"){streams…}`.
  - Kick: `curl -A Mozilla/5.0 https://kick.com/api/v2/channels/<slug>` (`livestream.categories`, `chatroom.id`). The Kick subcategory filter doesn't work.
  - YouTube: `yt-dlp --flat-playlist 'https://www.youtube.com/results?search_query=gta+5+live&sp=EgJAAQ%253D%253D'`.
- **Live logs:**
  - `data/logs/YYYY-MM-DD-live.log` (local date).
  - The rehearsal stdout went to `data/logs/rehearsal*-stdout.log`.
  - The "CLIP READY" line names the channel.
- **Unlocks (UTC):** NZ Wed 11:00, AU east 13:00, Perth 16:00, CET 23:00, UK Thu 00:00, US Thu 05:00.
- **Session models:** Sonnet 5.5 for plumbing, Opus 5.5 for the look, Haiku 5.5 for web lookups, Fable for plan changes. The rehearsal ran on Opus 5.5.
- **Shell gotchas:**
  - Foreground `sleep` is blocked.
  - Kick API needs curl, not Python.
  - Monitor tailing two log files gives duplicate lines.
