# GTA6 Studio — Plan

Written 2026-11-17. GTA 6 launches **Thursday 2026-11-19 at midnight local
time**, so New Zealand gets it first at **Wed Nov 18, 11:00 UTC** and the US last
(see 4b). PS5 and Xbox only, no PC date. Build time: Tuesday and Wednesday morning.

## 1. Goal and hard constraints

- **Goal:** as many views as possible on our own new channels (YouTube Shorts,
  TikTok, Instagram Reels). Monetisation is not a goal for now.
- **Volume through breadth:** many streamers (big and mid-size), so we can post
  a lot and still keep only the best moments.
- **Sources:** live streams on Twitch, YouTube Live and Kick, plus finished
  YouTube videos and stream replays.
- **Money:** no Anthropic API key, no paid tools. Only the Claude Pro plan,
  used for building. Everything that runs daily is free and local.
- **Look:** like a normal, clean clipping Short. Nothing that looks "edited"
  or AI-made.
- **Posting:** automatic where the platforms allow it, with an approval step
  that can be switched on or off.
- **Language:** English first. The pipeline is language-neutral, so German (or
  another language) is a config change plus a separate channel.

## 2. How it works

```
streamers.yaml ──► WATCH ──► DETECT ──► FETCH ──► TRANSCRIBE ──► JUDGE ──► RENDER ──► REVIEW ──► POST
                   chat +     spikes    60-90 s    Whisper       local     vertical   web page   YouTube/
                   audio                of video   (local)       LLM       + captions            IG/TikTok
```

Two entry points share everything from TRANSCRIBE onwards:

- **Live mode:** follows streams while they are live and produces a clip a few
  minutes after the moment happens. This is the edge over other clippers.
- **VOD mode:** give it a YouTube/Twitch link of a finished video; it finds the
  moments afterwards.

One SQLite file (`data/studio.db`) tracks every moment through its states:
`detected → fetched → judged → rendered → approved → posted`. Every stage is a
plain script that can be re-run on its own.

### Two tiers of streamers (this is what makes "a lot of streamers" possible)

Recording a stream costs about 2–3 GB per hour, so recording 30 streams for
12 hours would fill the disk in a day.

- **Tier A (about 5–8 biggest streamers):** recorded continuously with
  streamlink. Fastest and safest. Raw recordings are deleted after 24 h.
- **Tier B (dozens of others):** only their chat is watched, which costs
  almost nothing. When chat spikes, the script downloads just that 60–90 s from
  the stream's rewind/replay with yt-dlp. *Must be verified in milestone M3;
  if a platform does not allow it, that streamer goes to Tier A or VOD mode.*

## 3. Stages and tools

Stars and dates checked on GitHub on 2026-11-16/17.

| Stage | Tool | Stars / last update / licence | Notes |
|---|---|---|---|
| Record live | streamlink | 11.9k / Nov 14 / BSD-2 | Has built-in Twitch, YouTube and Kick plugins |
| Download videos and segments | yt-dlp | 208k / Nov 16 / Unlicense | Also gives YouTube's "most replayed" graph, a free highlight signal |
| Twitch live chat | own small script | n/a | Twitch chat can be read anonymously, no account |
| YouTube live chat | yt-dlp live chat, or chat-downloader | 1.2k / Nov 2025 / MIT | chat-downloader is a year stale; test both |
| Kick chat | own small script | n/a | Unofficial; no solid existing tool found (best has 11 stars). Highest risk, built last |
| Twitch replay chat | TwitchDownloader | 4.1k / Nov 15 / MIT | For VOD mode on Twitch |
| Loudness peaks | ffmpeg | installed | Screams and laughter as a second signal |
| Transcribe | mlx-whisper (large-v3-turbo) | MIT | Already ran on this Mac; word-level timing. WhisperX (25k, BSD-2) is the fallback |
| Judge and title | local LLM through Ollama | 187k / Nov 16 / MIT | See section 5 |
| Cut, vertical, captions, sound | ffmpeg + ASS subtitles | installed | See section 4 |
| Review | own small local web page | n/a | Approve, reject, edit title |
| Post | official platform APIs | free | See section 6 |
| Analytics | platform APIs, later | free | Milestone M8 |

**Considered and not chosen as the core:**
- **OpenShorts** (8.7k, MIT, updated Nov 16) — the plugin from the reels. Does
  VOD only, not live, and I have not confirmed it runs without a paid AI key.
  Useful as a reference and as an emergency fallback for VOD mode.
- **pycaps** (233, MIT) — nice animated captions, but renders through a
  browser, which is slow at our volume. ffmpeg subtitles do the classic Shorts
  look directly.
- **Small live clippers** (clippy 9 stars, stream-clipper 5 stars) — too small
  to depend on. The live part is a few hundred lines of our own code.

### How a moment is detected

1. **Chat spike:** messages per 10 s compared with that channel's own normal
   level (short burst and 30 s sustained). Each channel has its own baseline,
   so small streamers work too.
2. **Chat content:** bursts of LOL / W / OMG / "clip it" / laugh emotes weigh
   more than plain volume.
3. **Loudness peak** in the audio.
4. **VOD mode only:** YouTube "most replayed" peaks.
5. Candidates are cut generously (about 20 s before the spike to 40 s after,
   because chat reacts late), transcribed, and the local LLM decides: keep or
   drop, exact start and end snapped to sentence ends, a title, and a score.
6. Duplicates of the same moment are merged.

## 4. The look

Taken from the nine example Shorts (five of them have 12–55 million views).

- **Layout A (default):** streamer facecam on top (about 35–40 % of the
  height), gameplay below. The facecam position is stored once per streamer in
  `streamers.yaml`; unknown streamers get automatic facecam detection (S2b:
  find the cam rectangle from a still face plus its straight border; until
  then they get layout B). Within a clip, stretches where the cam is off
  switch to B automatically (built in S2).
- **Layout mix:** we do not use one layout for everything. `layout_mix` in
  `style.yaml` renders a share of clips in other layouts (start: 20 % C), the
  layout is stored per clip, and M8 analytics shifts the mix to what gets views.
- **Later: face tracking** (S8): for streamers who move a lot or use a
  full-screen camera, follow the face so it stays centred in the top box.
- **Layout B:** gameplay only, full height, for streamers without a facecam or
  for YouTube gameplay videos.
- **Layout C:** the wide 16:9 picture in the middle with a white rounded title
  box on top (the Kai Cenat example).
- **Captions:** one to two words at a time, heavy rounded font, white with a
  thick black outline, the key word in green or yellow. Placed at the seam
  between facecam and gameplay, never over the face. Reaction tags like
  `*SECURITY*` where nobody is speaking.
- **Length:** 15–50 s typical, 60 s maximum.
- **Sound effects:** very few and quiet (one hit on the punchline, one whoosh
  at the start at most), from free-licence libraries. Off by default until you
  approve how it sounds.
- **Circles and arrows:** not in the launch version. Placing them needs to
  know where things are on screen. Stage 1: you click the spot in the review
  page. Stage 2: a local vision model suggests the spot (free, runs on the
  Mac).
- **Style lives in one file** (`config/style.yaml`): font, sizes, colours,
  position, words per caption. Changing the look never means changing code.

You approve the look in milestone M2 on real GTA 5 stream footage before
anything is posted.

## 4b. Launch timing: be first (checked 2026-11-17)

GTA 6 unlocks at **midnight local time on Nov 19**, a rolling launch by
country, so the first streams are hours before Europe and the US:

| Region | Unlock (UTC) |
|---|---|
| New Zealand | **Wed Nov 18, 11:00** |
| Australia east (Sydney/Melbourne) | Wed Nov 18, 13:00 |
| Australia west (Perth) | Wed Nov 18, 16:00 |
| UK | Thu Nov 19, 00:00 |
| Central Europe | Wed Nov 18, 23:00 |
| US/Canada (stores unlock at midnight Eastern) | Thu Nov 19, 05:00 |

Xbox timing is disputed (some sources say midnight GMT everywhere), so watch
NZ/AU PlayStation streamers first. Sources are secondary blogs quoting
PlayStation/Rockstar support; re-check on the day (S6).

What this changes:
- **The real launch window opens Wednesday 11:00 UTC**, not Thursday. Live
  mode (S3) and Tier B watching must run by then; the "rehearsal" becomes the
  real thing on NZ/AU streams.
- **S5 must list NZ/AU GTA streamers first** (Twitch, Kick, YouTube), then
  Europe, then US, ordered by when they go live.
- Clips posted in the 11-18 hours before the US unlock face the least
  competition. Quality still comes first: a bad first clip does not help.
- Story/cutscene spoilers are most sensitive in this window; the judge already
  drops cutscene moments.

**Update 2026-11-17 evening (who streams in the NZ window):**
- **Twitch reportedly warned creators** that using the "NZ trick" (NZ account
  or console region) can get them suspended. Expect big US Twitch streamers
  to wait for their own unlock. The NZ window (11:00 UTC) will mostly be
  **Kick and YouTube** streamers plus people really in NZ/AU.
- **N3on (Kick)** is the user's top pick; he said he will stream from NZ
  (not confirmed in news). **Adin Ross (Kick)** is the other big Kick name.
- **IShowSpeed (YouTube)** announced his GTA VI stream for **9 PM ET Nov 18 =
  Thu 02:00 UTC (03:00 CET)**. Reports of an NZ trip clash with that and are
  unconfirmed. Plan for 02:00 UTC, watch for earlier.
- **Kai Cenat (Twitch):** NZ trip is a rumour from one YouTube Short.
- So **live mode must follow Kick and YouTube before 11:00 UTC Wednesday**:
  S7 moves ahead of S4 (section 10).
- **First hour is thin:** streams open with menus and intro cutscenes, which
  the judge drops on purpose. Expect few clips until real gameplay starts.
- **Zero-build fallback** if S7 is not ready: open the Twitch GTA VI
  category, pick who is live (most viewers first) and run
  `uv run studio live --channels name1:A,name2:B,...`. Twitch only.

## 5. AI: what runs where

| Job | Runs on | Cost |
|---|---|---|
| Transcription | Whisper, local | free |
| Keep/drop, clip boundaries, titles, hashtags | local LLM in Ollama | free |
| Later: arrow placement, thumbnail frame choice | local vision model | free |
| Building and fixing the system | Claude Code on the Pro plan | plan limit only |

The Mac (M5 Pro, 48 GB) can hold Whisper and a roughly 20 GB model at the same
time. Model candidates from search results, **not yet verified**: a Qwen 3.x
model around 27–35B and Gemma 4 around 26–31B. Milestone M1 tests two of them
on a real transcript and keeps the one that returns clean, sensible answers
faster. Claude is never called by the running pipeline.

## 6. Posting

| Platform | Route | Status |
|---|---|---|
| YouTube | Official YouTube Data API with our own free Google Cloud project | Sources disagree on whether uploads from a new, unaudited project are forced to private. **Test with one upload first thing in M5.** Reported limit about 100 uploads a day |
| Instagram | Official API, our own Meta app in development mode with the account added as tester | Reported to work for your own account without review. Needs a Professional (Creator) account. Reported limit 100 posts a day. Verify in M5 |
| TikTok | Official API | Posts from an unaudited app are **forced private**. Apply for the audit on day 1; until it passes, clips land in a synced phone folder and you post them by hand |

- TikTok alternative: browser-automation uploaders exist (tiktok-uploader 787
  stars MIT, TiktokAutoUploader 1.2k AGPL). They break TikTok's rules and can
  get a fresh account banned, so they are a last resort and your decision.
- **Postiz:** you already have the skill. If your Postiz is the paid cloud
  version, it posts to all three through Postiz's already-approved apps and
  removes the TikTok problem. Self-hosted Postiz has the same limits as our
  own apps. M5 checks which one you have.
- **Approval:** `review.mode` is `manual` (everything waits for you),
  `auto` (posts on its own) or `threshold` (posts on its own above a score,
  the rest waits). Start on `manual`, move to `threshold` once the scores
  prove trustworthy.
- **New accounts:** posting dozens of clips on day one from a brand-new
  account can trigger spam filters. Start around 5–10 a day per account and
  ramp up.

## 7. Risks

- **YouTube reach:** since Oct 1 2026 Shorts reportedly gives less feed reach
  to channels that mostly re-upload other people's clips. Our hook titles and
  tight cuts help, but TikTok and Instagram may carry more of the views. M8
  measures this so we shift effort to what works.
- **Rockstar:** written policy covers non-commercial fan use; we are not
  monetising, which fits. Avoid cutscenes, story spoilers and endings, which
  are reportedly taken down. The judge step drops moments that are mostly
  cutscene dialogue.
- **In-game radio music** can cause copyright claims. Prefer moments where the
  streamer is talking; a muted-music option is a later improvement.
- **Streamers:** every streamer in `streamers.yaml` has an `allowed` note.
  Take down anything on request, and keep a blocklist.
- **Twitch ads:** recordings contain ad breaks for non-subscribers. Streamlink
  can skip them, leaving gaps; the detector ignores those windows.
- **Kick:** chat reading is unofficial and may break.
- **Launch-day load:** platforms and tools can fail under load. Wednesday morning
  rehearsal on live GTA 5 streams is the test, then the NZ/AU launch streams.

## 8. Clipping campaigns: verdict

Real, but small. Whop / Content Rewards campaigns typically pay about **$1 per
1,000 views**, minus a 10 % fee, with per-video caps. Brands can reject clips
after the views happened, and legitimate clippers report payout delays and
false "bot" flags. No GTA 6-specific campaign from a big streamer could be
confirmed. All of this comes from blogs with something to sell, so treat it as
approximate.

Decision: do not build around campaigns. Since the clips exist anyway,
submitting those from streamers who run a campaign is free extra. A short
cheap session on launch day (S6) lists the campaigns that are actually open.

## 9. Build order

Each milestone ends with something you can see or run.

| # | Milestone | Done when |
|---|---|---|
| M0 | Setup: git, Python project with uv, folders, install yt-dlp, streamlink, Ollama, model | `studio doctor` prints all green |
| M1 | VOD mode, raw: link in → scored moments → plain cut clips | 10+ sensible raw clips from one GTA 5 stream replay |
| M2 | The look: layouts A/B/C, captions, style file | You approve 5 finished clips |
| M3 | Live mode on Twitch: chat watcher, spike detector, Tier A recording, Tier B segment fetch | A clip is ready within 5 minutes of a live chat spike |
| M4 | Review page | You approve/reject/retitle clips in the browser |
| M5 | Posting: YouTube, Instagram, TikTok route, scheduler | An approved clip appears publicly on YouTube and Instagram |
| — | **Launch rehearsal Wednesday morning** on live GTA 5 streams, then the real NZ/AU launch streams from 11:00 UTC | Runs 2 h unattended |
| M6 | YouTube Live, then Kick | Same result as M3 on each |
| M7 | Sound effects, click-to-place circles and arrows, German | Per feature |
| M8 | Analytics loop: views per clip, per streamer, per signal | Weekly table that changes the streamer list |

**If time runs short before Thursday:** M0, M1, M2 and M3 are the minimum.
Posting by hand from a folder replaces M4/M5 for the first day.

## 10. Sessions and which model

One session per row, each started fresh with **Start**, each told
"Read PLAN.md and CLAUDE.md, do session S_n". Run them one after another, not
in parallel: parallel sessions drain the Pro limit faster and can overwrite
each other's files.

| Session | Milestones | Model | Why |
|---|---|---|---|
| S1 | M0 + M1 | Sonnet 5.5 | Standard plumbing with well-known tools |
| S2 | M2 | Opus 5.5 | Visual judgement and back-and-forth with you on the look |
| S2b | Automatic facecam detection for streamers without a stored box | Sonnet 5.5 | Needed for launch-day streamers we have never seen; layout B is the fallback |
| S3 | M3 | Sonnet 5.5 | Clear spec; live timing logic needs care, not creativity |
| S4 | M4 + M5 | Sonnet 5.5 | Standard web page and API work. You do the account clicks |
| S5 | Streamer list: who streams GTA 6, where, who allows clipping | Haiku 5.5 | Web lookups only |
| S6 | Open campaigns check on launch day | Haiku 5.5 | Web lookups only |
| S7 | M6, **now before S4** | Sonnet 5.5 | Kick + YouTube live for launch (N3on, Speed). Scope in order: (a) Tier A recording for any platform + loudness-only trigger when no chat reader exists, (b) Kick chat, (c) YouTube Live chat. **Status 2026-11-17: (a) and (c) done (c via yt-dlp live_chat); (b) Kick chat done too (Pusher), loudness stays as backup** |
| S8 | M7 + M8 | Sonnet 5.5, Opus only for arrow placement design | |

Order from Tuesday evening 2026-11-17 (S1, S2, S2b, S3 are done):
1. **S7 tonight (Sonnet 5.5):** Kick + YouTube live. Must work by Wed 11:00 UTC.
2. **S5 (Haiku 5.5), can run right after S7 or Wednesday morning:** fill
   `streamers.yaml`. Find AU/NZ names from Twitch/Kick GTA viewer rankings
   (TwitchTracker, StreamsCharts, Kick category pages) filtered to AU/NZ,
   not from news articles. Verify every channel slug.
3. **Wednesday morning:** rehearsal on live Kick and Twitch GTA 5 streams.
4. **Wed 11:00 UTC (12:00 CET):** `uv run studio live` in the background on
   the NZ/AU list. Day 1 posting is by hand from `data/clips/`.
5. **S4 (Sonnet 5.5) after the launch window opens:** review page + posting.
   While `studio live` runs it must not edit `live.py`, `ingest/`, `pipeline.py`.
6. **Thursday:** S6 (Haiku) campaigns + unlock check, fix what broke.

Come back to Fable only if the plan itself needs to change.

## 11. What you need to do yourself

These need your accounts and cannot be done by Claude:

1. Create the YouTube channel, TikTok account and Instagram account (switch
   Instagram to Professional/Creator). Pick the channel name.
2. In S4: create a free Google Cloud project and a Meta developer app
   (the session walks you through it), and send the TikTok audit application.
3. Keep the Mac plugged in and awake through launch week.
4. Tell S5 any streamers you definitely want or do not want.

## 12. Monthly cost

| Item | Cost |
|---|---|
| Tools, models, APIs | $0 |
| Claude | Existing Pro plan |
| Electricity, Mac running all day | a few euros |
| Disk | Raw recordings auto-delete after 24 h; finished clips about 5–10 MB each |
