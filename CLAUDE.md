# GTA6 Studio

Automated GTA 6 clip production: watch live streams and videos, find the best
moments, cut them into vertical captioned clips, post to YouTube Shorts,
TikTok and Instagram Reels. Goal: maximum views on our own channels.

Status: BUILDING. The full plan, tool choices and session list are in
PLAN.md. Read the section for your session (S1–S8) before starting, and only
that section plus what it points to.

## Hard rules

- **No paid services and no Anthropic API.** The running pipeline never calls
  Claude. All runtime AI is local: Whisper for transcription, an Ollama model
  for judging and titles.
- **The pipeline is plain scripts.** Claude Code builds and fixes it; it does
  not run it day to day.
- **Claude never watches raw video.** Work from transcripts, chat logs,
  timestamps and the database. To check a render, make ONE contact sheet
  (ffmpeg `tile`, about 1600 px wide) and open a full frame only when fine
  detail matters.
- **Prefer proven tools** over custom code. Before installing anything: check
  stars, last commit, licence, read any install script, and tell the user in
  one or two lines what will be installed.
- **Look and behaviour live in config,** never hard-coded: `config/style.yaml`
  for the look, `config/streamers.yaml` for sources, `config/settings.yaml`
  for thresholds and posting mode.
- **Flag copyright and platform-policy risks** before building each stage
  (PLAN.md section 7). No story-cutscene or story-spoiler clips; menus, character creator and
  first-look reactions are allowed (user, 2026-11-18). Honour the
  blocklist in `streamers.yaml`. The `allowed` field is a note only: the user
  decides which streamers' clips get posted (decided 2026-11-17), so code never
  refuses a streamer for it.
- **Nothing is posted publicly without the user's say-so** until they switch
  `review.mode` away from `manual`. Never use browser-automation uploaders
  unless the user explicitly chooses that.
- **Secrets** (API tokens, cookies) go in `.env`, which is git-ignored. Never
  print them or commit them.

## Layout

```
config/        style.yaml, streamers.yaml, settings.yaml
studio/        the Python package
  ingest/      streamlink recording, yt-dlp downloads, segment fetch
  chat/        twitch.py, youtube.py, kick.py  (one reader per platform)
  detect/      spike detection, loudness, merge and dedupe
  transcribe/  Whisper wrapper
  judge/       local LLM prompts and scoring
  render/      layouts, captions (ASS), sound effects
  review/      local web page
  post/        youtube.py, instagram.py, tiktok.py, scheduler
  db.py        SQLite state: detected → fetched → judged → rendered → approved → posted
assets/        fonts/, sfx/  (free-licence only; keep a LICENCES.md)
data/          git-ignored: raw/, segments/, clips/, logs/, studio.db
tests/
```

## Commands

Python is managed with `uv`. One entry point, `studio`:

```
uv run studio doctor          # check tools, model, disk, accounts
uv run studio vod <url>       # VOD mode on one video
uv run studio live            # watch everything in streamers.yaml
uv run studio review          # open the review page
uv run studio post            # post what is approved and due
uv run pytest                 # tests
```

## Conventions

- Every stage reads its work from the database and writes its result back, so
  any stage can be re-run alone and a crash loses nothing.
- Every moment keeps its source: streamer, platform, stream URL, timestamps,
  the signals that fired, and the judge's score. Analytics depends on this.
- Raw recordings are deleted after 24 h. Finished clips are kept.
- Log to `data/logs/`, one file per day per stage. No silent failures: a
  failed moment is marked `failed` with the reason.
- Test each stage on a short saved sample in `tests/fixtures/`, not on a full
  stream.
- Commit at the end of each milestone with what works and what does not.

## Which model for what

| Work | Model |
|---|---|
| Plumbing, scripts, APIs, review page (S1, S3, S4, S7) | Sonnet 5.5 |
| The look, caption styling, arrow placement design (S2, part of S8) | Opus 5.5 |
| Web lookups: streamer lists, open campaigns (S5, S6) | Haiku 5.5 |
| Changing the plan itself | Fable |

Do not switch model or effort in the middle of a session without telling the
user that it resets the cache.

## Token use

- Read only the files the task needs; search before opening.
- Batch independent tool calls.
- Long test runs and stream recordings run in the background; do not poll.
- When context passes about 150k tokens or the milestone is done, tell the
  user to press **End** so the handoff is saved.

## Health check ("check everything / make sure it runs")

Follow `docs/HEALTH-CHECK.md`. This job is routine and can be done by a cheaper
model: Haiku 5.5 is enough. Tell the user they can start a new session on Haiku
and say "run the health check" instead of asking Sonnet/Opus.
