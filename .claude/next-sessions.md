# Next sessions (written 2026-11-17 20:20 CET)

Run them one after another. For each one: press End, press +, pick the model, then paste the prompt.

---

## 1. Rehearsal (Sonnet 5.5), now

```
Read PLAN.md (section 13), CLAUDE.md and .claude/handoff.md. Do the REHEARSAL (PLAN section 13 step 1).
Run `uv run studio live` in the background on GTA 5 streams that are live right now: one Kick, one YouTube,
two Twitch (find live ones yourself; check streamlink can open them). Do not poll; use Monitor.
Goal: each platform makes at least one clip from a REAL trigger (no injected spikes). Tune
`settings.yaml live.loud` and `live.chat` from real numbers, not guesses. Also run `uv run studio vod` on one
old Twitch stream and one old Kick stream (short ones, under 2 h) and say whether they work.
Check results with ONE contact sheet, never raw video. Fix what breaks, but only in live/ingest/detect/chat
and settings.yaml, never render/ or style.yaml (another session changes the look next).
At the end: commit, update .claude/handoff.md, and tell me in plain English what works and what does not.
```

## 2. Make the videos better: sound effects, arrows, face tracking (Opus 5.5)

```
Read PLAN.md (sections 4 and 13), CLAUDE.md and .claude/handoff.md. Do S8 part 1: the look upgrades.
The approved look stays; these are additions, all switchable in config/style.yaml:
1. Sound effects on key moments (scream, laugh, fail, "bro what"). Free-licence sounds only (CC0 or similar),
   listed in assets/LICENCES.md. Subtle, not meme-spam. Placement from the transcript words and loudness.
2. Circles/arrows that point at the thing that matters. Automatic first (e.g. where motion or the face is),
   with a way to set them by hand later. Only where it clearly helps; most clips get none.
3. Face tracking: keep the streamer's face in frame when the cam moves or there is no fixed box (YuNet is
   already in studio/render/facecam.py). Smooth, no jitter.
Re-render 4-6 existing clips from data/clips/ to show before/after. ONE contact sheet per check.
Ask me before anything that changes the approved caption or title look.
Do not edit live.py, ingest/, chat/, detect/ (live mode may be running). Commit and update the handoff.
```

## 3. Full test (Sonnet 5.5), after 2

```
Read PLAN.md (section 13), CLAUDE.md and .claude/handoff.md. Do a full end-to-end test before launch:
`uv run pytest`, `uv run studio doctor`, then one short VOD and 15 minutes of `studio live` on one Kick and one
Twitch GTA 5 stream, with the new sound effects, arrows and face tracking on. Check clips with ONE contact
sheet. Fix what breaks. Write a launch checklist for tomorrow 12:00 CET in .claude/handoff.md (exact commands,
which channels, what to watch for). Commit.
```

## 4. Warm-up clips today from GTA 6 talk videos (Sonnet 5.5), after 3

```
Read PLAN.md (sections 7 and 13), CLAUDE.md and .claude/handoff.md. Do the WARM-UP session: make clips
today from YouTube videos where people talk about GTA 6 and what to expect tomorrow, so the new accounts
start posting before launch.
1. Find 5-10 such videos published in the last 2 days (big creators first). Flag any that are mostly
   Rockstar trailer footage: skip those, the trailers get claimed.
2. The judge prompt is built for gameplay. Add a "talk" mode (config, not hard-coded) that picks strong
   claims, hot takes, predictions and funny reactions, and gives hook titles like "GTA 6 tomorrow: ...".
3. Run `uv run studio vod <url>` on each and give me the 10-15 best clips with title and hashtags in a list.
4. Nothing is posted by you. I post by hand, 5-10 per account today, spread out.
Commit and update the handoff.
```

---

After these: launch Wed 12:00 CET (checklist from session 3), then S4 (review page and posting) in the
afternoon while live mode runs.
