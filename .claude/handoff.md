# NEXT: see .claude/next-sessions.md. Rehearsal DONE; next = S8 part 1, the look upgrades (Opus 5.5)

# Handoff: rehearsal done 2026-11-17 (PLAN 13 step 1)

**Live, real triggers only (no fakes), streams found live via Twitch GQL / Kick API / yt-dlp live search:**
`--channels kick/ramee:A,youtube/OpTicGaming:A,deansocool:A,youney:B`, then tuned rerun with `formal:B` instead of youney.

| Platform | Trigger that fired | Clip |
|---|---|---|
| Kick (ramee, GTA RP) | loud + chat (Kick Pusher chat works) | moment 36, score 8, `data/clips/5_11.mp4` |
| YouTube (OpTic heist marathon) | loud + chat | moments 45, 53 (score 8): `14_0.mp4`, `22_10.mp4` |
| Twitch (formal, Tier B rewind) | chat | moment 59, `28_30.mp4` (Tier A deansocool: triggers fine, judge dropped all) |

Spike to finished clip: mean 43 s (one worker, so about 80 moments an hour in total).

**Fixed (live/ingest/detect/chat/settings only):**
- Copy-paste raid: deansocool chat pasted "fuck your mod <emote>" about 60 times, which gave a 38x "spike". Fix: `live.chat.repeat_cap: 3` / `repeat_min_words: 3`. A message of 3+ words with the same first words counts at most 3 times per bin. Never capped: "LOL" x50, and emote spam where the first words are one repeated token or all hype words ("KEKW KEKW KEKW", which is the real spike in big launch chats). Test: `tests/fixtures/chat_raid.jsonl`.
- Kick chunk "holes" were false. Kick delivers in bursts, so chunk names lag the content by up to 30 s and then catch up (799 s of content in 798 s wall time). The old 13 s gap rule threw away good Kick moments ("only 10s of unbroken video").
  - Fix: `ingest/live.chunk_len()` (ffprobe audio packets) + `contiguous_run(lengths=)`. A hole is now a lag that stays up, not one that recovers.
  - Setting: `live.hole_s: {kick: 10}`. Twitch and YouTube keep the fixed 10 s grid.
  - Trade-off: a Kick reconnect hole under 10 s now gives a small jump cut instead of a failed moment.
- `--channels kick/x` now looks up the chat id itself (`chat/kick.lookup_chatroom`, curl). Before, ad-hoc Kick channels got no chat.
- Kick VODs: yt-dlp's kick:vod extractor is broken (Kick v1 API 404). `ingest/download.kick_vod()` reads `api/v2/channels/<slug>/videos` (curl, 3 retries), downloads the HLS `source`, and fills the title/channel in info.json. It only finds a channel's latest videos.
- The CLIP READY log line now names the channel.

**Tuning (offline replay of saved chat + chunks through the real detector classes, 45 min, 4 channels):**
- Chat: `burst_x` 3.0 -> 4.0. A busy Kick chat went from 15/h to 8/h; Twitch's real 6-9x spikes are all kept. `min_burst` 12 kept.
- Loud: `z` is the knob that binds, not `min_ratio` (real bursts are 3.5-6x). z 4.0 kept, `cooldown_s` 20 -> 90: Kick 9/h, YouTube 4/h. z 4.5 lost a kept clip. Chat reacted to only about 1 in 4 loud fires, so loud is the noisier trigger.
- The replay script is not in the repo (scratchpad). Rebuild it if needed: feed `data/chat/*.jsonl` and `data/raw/live/<chan>/*.ts` loudness into `ChatDetector` / `LoudDetector` and count fires per hour after the first 10 min.

**VOD test:** Twitch v2931512553 (34 min RP) and Kick ramee c4702eba (17 min RP) download and run end to end (`--top 6` each, 12 judged). 0 clips: loudness alone picks chatter on RP streams. S9 (chat replay as a signal) is the fix.

**Not done / for other sessions:**
- Judge drops most RP moments ("no gameplay action"). Good for action clips; RP-heavy streamers will give few clips. Judge prompt is outside this session's area.
- Render (look session): clip 53 (OpTic multi-POV overlay) has a big empty black band on top with a small facecam+game strip. Facecam/layout B misread the overlay. Clip 59 caption shows "FUCKING" uncensored (platform risk?).
- Kick runs both triggers (`CHAT_READERS` is still {"twitch"}): on ramee chat + loud is about 17 fires/h, above the 6-10/h per-channel budget. If the worker queue backs up at launch, add `kick` to `CHAT_READERS` (chat only) or set `loud_trigger`. Not changed: the user decides.
- Tonight's streams had hundreds to a few thousand viewers. Ratios carry over; at launch scale `burst_x` binds, not `min_burst`.
- The Kick/YouTube clips came from the pre-tuning run; the replay confirms the tuned settings still catch moments 36, 45 and 53. The tuned live run was short (it made the Twitch clip).
- `db.connect()` has no WAL/busy_timeout. Don't run `studio vod` while `studio live` runs.
- Kick/YouTube Tier B still not built (recorded as A).

---

# Handoff: S7 done (a, c); Kick chat and Tier B for Kick/YouTube not built

**Done 2026-11-17 (commits b7946e3 + next):** `studio live` records Twitch, Kick and YouTube. Channels without a
chat reader get a loudness trigger (`detect/loud.py`, `settings.yaml live.loud`, `loud_trigger: no_chat|always|off`).
YouTube chat = `studio/chat/youtube.py`, tails `yt-dlp --write-subs --sub-langs live_chat` (no new dependency;
chat-downloader is broken on YouTube right now). YouTube keeps loudness on too (second trigger). `live: false`
entries are skipped. `--channels kick/n3on:A,youtube/IShowSpeed:A,kaicenat:B`. Non-Twitch Tier B is recorded as A.
Tested: Kick/destiny full chain (record, loud spike, cut, Whisper, judge); YouTube chat read 65 msgs in 45 s.
**Not tested / open:**
- Loud thresholds never fired on real GTA audio; on 5 min of talk the 6 s/10 min ratio was p99 1.48, max 1.51 and
  the config needs 1.6 (+ z 4). Tune in Wednesday's rehearsal on a live Kick GTA stream (scream ratio should be >2).
- Kick chat (b): DONE, studio/chat/kick.py (Pusher key 34bf7a0ff419a2a775b9, chatroom ids stored in streamers.yaml as kick_chatroom; Python gets 403 from Kick API, use curl). Loudness stays as second trigger.
- Tier B rewind for Kick/YouTube (d): not tried.
- `signals.loudness_per_second` calls bare `ffmpeg` (works, not `tools()`).
**Next:** S5 (Haiku 5.5) streamer list, then Wednesday rehearsal, then S4.

---

# Handoff: GTA6 Studio, launch prep done, next S7 (Kick + YouTube live)

## Goal
Automated GTA 6 clip production. The pipeline watches live streams and VODs, finds the best moments, and cuts them into vertical captioned 1080p clips for YouTube Shorts, TikTok and Instagram Reels on the user's own channels. The aim is maximum views.

Be first: the game unlocks at midnight local time, so New Zealand gets it on **Wed Nov 18 at 11:00 UTC (12:00 CET, the user's time zone)**. The user wants to clip the streamers who play from NZ and post as soon as possible, "especially N3on, as he's claimed he's going to be streaming from New Zealand". Quality still comes first: "good clips, good high quality ones."

## How the person wants things done
- **Token saving (global CLAUDE.md), never at the cost of quality:**
  - Read only what is needed; search first.
  - Batch tool calls; don't poll.
  - No filler or recaps.
  - Images: ONE contact sheet (ffmpeg `tile`, about 1600 px wide); open a full frame only when detail matters.
  - Suggest pressing **End** at about 150k tokens or when the task changes.
  - Say so before changing model or effort mid-session (it resets the cache).
- **Hard rules (project CLAUDE.md):**
  - No paid services and no Anthropic API at runtime (local Whisper and Ollama only).
  - Claude never watches raw video.
  - Prefer proven tools: check stars, last commit and licence, read any install script, and tell the user in 1–2 lines before installing.
  - Look and behaviour live in `config/*.yaml`, never hard-coded.
  - Secrets go in `.env`, never printed.
  - Nothing posts publicly while `review.mode: manual`.
  - No browser-automation uploaders unless the user explicitly chooses one.
  - No cutscene or story-spoiler clips.
  - Honour the blocklist. `allowed` is a note only and never blocks: "I'll manage if we can post or not."
  - Commit at each milestone.
- **Look:** approved ("caption style and everything is really good"). Don't change it in plumbing sessions. Vary layouts and let analytics pick winners.
- **Talking to the user:** they write casually and by voice. Explain things in plain English and say how to run each step. They start sessions themselves and want to be told which session to run next and on which model.

## Current state
Everything is committed on `main`. The latest commit is `b70c8eb`: "Launch prep: NZ window is Kick/YouTube, S7 before S4, launch streamers in streamers.yaml".

- **Sessions done:** S1, S2, S2b and S3.
  - `uv run pytest`: 23 passed.
  - `uv run studio doctor`: all green.
  - Disk: 611 GB free.
- **Live mode is Twitch-only.** `studio/live.py` `resolve_channels` (about line 43) skips every non-Twitch entry with a warning.
  - `studio/ingest/live.py` `stream_url()` already builds `kick.com/<slug>` and `youtube.com/@<handle>/live`.
  - `start_recording` and `fetch_rewind` take a platform argument.
  - Streamlink 8.6.3 has both `kick` and `youtube` plugins. yt-dlp is 2026.10.26.
- **`config/streamers.yaml`** now lists N3on (kick, `n3on`, tier A), Adin Ross (kick, `adinross`, A), Kai Cenat (twitch, `kaicenat`, B) and IShowSpeed (youtube, `IShowSpeed`, A).
  - Each entry has a `note` saying what is confirmed and what is rumour.
  - xQc has `live: false` (VOD test source). The code does not read `live:` yet; S7 must add that.
  - Channels are lowercased by the loader.
  - Right now only `kaicenat` resolves for live mode.
- **`PLAN.md`:**
  - Section 4b has an "Update 2026-11-17 evening" paragraph: Twitch ban risk, who streams when, a thin first hour, and the zero-build fallback.
  - The S7 row in section 10 now says "now before S4", and the new order list replaced "Suggested timing".
- **`.claude/handoff.md`:** the top block is the S7 brief, with scope (a)–(d). Below it are the older S2/S3 handoff notes, which are still valid.
- **Judge prompt** (`studio/judge/llm.py`): already accepts GTA 5 or GTA 6 and drops cutscenes and spoilers. No change needed.

## Decisions and dead ends
- **S7 comes before S4.** The NZ window will mostly be Kick (N3on, Adin Ross) and YouTube.
  - Twitch reportedly warned that using the NZ trick can get a streamer suspended, so big US Twitch streamers will probably wait for their own unlock.
  - Day 1 posting is by hand from `data/clips/`.
- **IShowSpeed** announced 9 PM ET Nov 18, which is Thu 02:00 UTC (03:00 CET). His NZ trip is reported, unconfirmed, and clashes with that announcement.
- **Kai Cenat's NZ trip** is a rumour from one YouTube Short.
- **N3on's NZ plan** is not confirmed in the news, but he is the user's top priority.
- **No AU/NZ Twitch names were found in news articles.** S5 must use viewer rankings (TwitchTracker, StreamsCharts, Kick category pages) filtered to AU/NZ.
- **S4 timing:** S4 runs after launch starts and must not edit `live.py`, `ingest/` or `pipeline.py` while `studio live` is running.
- **Earlier technical dead ends** (ffmpeg-full for libass, the Whisper settings, Tier B rewind of about 30–50 s, epoch-named chunks, ffprobe being wrong on `.ts`, YAML `no` parsing as a boolean): see the older section of `.claude/handoff.md`.

## Next steps
1. **S7 (Sonnet 5.5), tonight.** Prompt: "Read PLAN.md, CLAUDE.md and .claude/handoff.md, do session S7."
   - a. Tier A recording for Kick and YouTube, plus a **loudness-only trigger** when a channel has no chat reader. Thresholds go in the `settings.yaml` `live:` block. Skip entries with `live: false`. Test on a live Kick channel in the background and produce one clip. This part alone covers the launch.
   - b. Kick chat in `studio/chat/kick.py` (Pusher websocket, unofficial). Check for a proven library first.
   - c. YouTube Live chat in `studio/chat/youtube.py` (e.g. chat-downloader or pytchat). Check stars and licence.
   - d. Tier B for Kick and YouTube only if a rewind works.
   - Keep pytest green and commit.
2. **S5 (Haiku 5.5):** fill `streamers.yaml` with AU/NZ GTA streamers on Twitch, Kick and YouTube, ordered by go-live time. Verify every slug, including the ones already added.
3. **Wednesday morning:** rehearse `uv run studio live` on live Kick and Twitch GTA 5 streams. Tune `live.chat` and the loudness thresholds. Run it in the background and don't poll.
4. **Wed 11:00 UTC (12:00 CET):** run `uv run studio live` in the background on the list.
   - **Fallback (Twitch only):** `uv run studio live --channels name1:A,name2:B` with whoever is live in the Twitch GTA VI category.
5. **S4 (Sonnet 5.5):** review page and posting, after launch starts.
6. **Thursday:** S6 (Haiku 5.5), open campaigns and an unlock-time check. Then fix what broke.
7. **Later:** S8 (face tracking, sound effects, layout analytics), WhisperX if caption timing complaints return, a muted-radio option.

## Waiting on the person
- Whether to start S7 now; the last turn asked.
- From the S3 handoff, the user hasn't answered whether the layout C title look is good (white and red lines, no box), or whether they want only the key word coloured.
- Approval to make posting public (`review.mode` is still `manual`).
- Platform account setup, during S4.

## Key facts
- **Project:** `/Users/tomasspesikaspetersons/Claude Projects/GTA6 Studio`. The user's machine is on CET (UTC+1).
- **Unlocks (UTC):**

| Region | Unlock (UTC) |
|---|---|
| NZ | Wed Nov 18 11:00 |
| AU east | Wed 13:00 |
| Perth | Wed 16:00 |
| Central Europe | Wed 23:00 |
| UK | Thu 00:00 |
| US | Thu Nov 19 05:00 |

- **Commands:** `uv run studio doctor|vod <url>|render [ids] [--layout A|B|C]|moments|live [--channels a:A,b:B]|review|post`. `review` and `post` are still stubs.
- **ffmpeg:** always use ffmpeg-full at `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`, via `layout.tools()`.
- **Models:**
  - Judge: `qwen3.8:27b` on Ollama.
  - Whisper: `mlx-community/whisper-large-v3-turbo`.
- **Session models:**
  - Sonnet 5.5: plumbing (S3, S4, S7).
  - Haiku 5.5: web lookups (S5, S6).
  - Opus 5.5: the look.
  - Fable: changes to the plan.
- **How the user starts a session:** press End, then +, pick the model, press Start (or paste the session prompt).
- **Shell gotchas:** foreground `sleep` is blocked (use Monitor with an until-loop). `rm` with a relative glob after `cd` gets blocked.
- `.claude/handoffs/` (snapshots) is untracked on purpose. So is `.DS_Store`.
