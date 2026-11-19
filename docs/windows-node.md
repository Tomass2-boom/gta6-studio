# Windows capture node

Goal: the MacBook can sleep in a bag all day and we still lose **zero** moments.
An old Windows PC at home runs 24/7 and captures everything; the Mac does the
heavy work (Whisper, judge, render, review, posting) whenever it is awake.
When the user gets home, the Mac picks up the whole day without being told,
best moments first.

Plan written 2026-11-19 on the Mac. The Windows session builds sections W1–W6;
the Mac session builds M1–M5. Both follow the contract in section C exactly.

## 1. Who does what

| | Windows PC (always on) | MacBook (when awake) |
|---|---|---|
| Chat for every channel (Twitch, Kick, YouTube) | yes, 24/7 | only as fallback |
| VOD watcher (stream start + VOD address → `vods.jsonl`) | yes, 24/7 | only as fallback |
| Spike detection | yes | re-checks Windows' chat with its own thresholds |
| Video at spikes (Tier A from a rolling recording, Tier B from Twitch rewind) | yes (phase 2) | fallback, and from VODs |
| Whisper, judge (Ollama), render, review, posting | no | yes |
| `studio.db` | its own local one, never synced | the real one |

Why this split: capture is light (streamlink and ffmpeg only copy bytes, chat
is text) so an old PC can do it; Whisper large and the 27B judge need the Mac.

YouTube matters most: YouTube keeps no chat replay, so today a sleeping Mac
loses YouTube moments for good. Logging them live on Windows fixes that.

## 2. Phases (each one works on its own)

1. **Chat + VOD watch (build first, smallest).** Windows runs `studio.chatlog`
   and `studio.vodwatch` 24/7 and syncs `data/chat/` and `vods.jsonl` to the
   Mac. The Mac's existing `fill-gaps`/`backfill` then sees full chat for the
   day and fetches only the spike stretches from the VODs. This alone covers
   Twitch and Kick (VODs) and YouTube chat.
2. **Video at spikes.** For streams whose VOD is missing, muted, sub-only or
   deleted: Windows runs live mode in *capture-only* (detect → cut the
   segment → write it to the outbox, no Whisper/judge/render). The Mac imports
   the segments.
3. **Failover and one capturer.** When Windows' heartbeat is fresh the Mac does
   not capture (no duplicates, no double bandwidth at home); it only
   processes. If the heartbeat goes stale, the Mac's watchdog starts its own
   live mode as today.
4. **Optional:** auto-update on Windows (pull, self-test, restart); faster-whisper
   on Windows if it has a usable NVIDIA GPU.

## C. Contract between the machines (both sides follow this)

Sync tool: **Syncthing** (free, open source, no account, works over the
internet when the Mac is away, encrypted). Two folders, **Send Only** on
Windows and **Receive Only** on the Mac, so no file is ever written on both
sides:

| Syncthing folder id | Windows path | Mac path |
|---|---|---|
| `gta6-chat` | `<repo>\data\chat\` | `data/remote/win/chat/` |
| `gta6-outbox` | `<repo>\data\outbox\` | `data/remote/win/outbox/` |

Rules:
- All times are **epoch seconds UTC** (`t` in chat lines, as now). Both
  machines use the same time zone (file names carry the local date).
  Windows syncs its clock (w32time) so times line up with the VODs.
- Chat files keep today's names and line format (`{"t","u","m"}`); the Mac
  reads them from `data/remote/win/chat/` and drops messages it has twice
  (same `t`, `u`, `m`), so hours when both machines logged never count double.
- `outbox/vods.jsonl`: Windows' vodwatch output (same format as `data/vods.jsonl`).
- `outbox/heartbeat.json`, rewritten every 60 s:
  `{"node":"win","t":<epoch>,"git":"<sha>","chat_ok":[channels with a live chat connection],
  "recording":[channels being recorded],"disk_free_gb":<n>}`
- `outbox/coverage-YYYY-MM-DD.jsonl`: one line per minute,
  `{"t":<epoch>,"chat_ok":[...],"recording":[...]}`. The Mac uses this to tell
  what Windows actually covered; a stretch is a gap only if neither machine had it.
- Phase 2 moments: `outbox/moments/<node>-<channel>-<int spike epoch>.mp4` plus a
  `.json` with the same name: `{"id","node","streamer","channel","platform",
  "url","stream_url","t_start","t_end","spike_t","signals","signal_score","quality","height",
  "created"}`. Write the mp4 under a `.part` name, rename it when complete, and
  write the `.json` **last**. The Mac imports only complete pairs.
- Windows deletes outbox files older than `node.outbox_keep_days` (default 7)
  and raw recordings older than `node.raw_keep_h`. The Mac copies each imported
  mp4 into `data/segments/`, so deletions on Windows never break a clip.
- **Never sync `studio.db`** (SQLite over a sync tool corrupts).

## W. Windows session

Work in `C:\GTA6Studio` (no spaces, not inside OneDrive). Repo:
https://github.com/Tomass2-boom/gta6-studio (public; pushing publishes).
Commit to branch `windows-node`, never to `main`.

**May edit:** `pyproject.toml`, `uv.lock`, `studio/config.py`, `studio/chatlog.py`,
`studio/vodwatch.py`, `studio/chat/*`, `studio/ingest/live.py`, `studio/live.py`
(only a small capture-only switch), new `studio/node/` package, new tests,
`docs/windows-node.md` (status notes at the bottom).
**Must not edit** (the Mac session owns them): `studio/backfill.py`,
`studio/fillgaps.py`, `studio/watchdog.py`, `studio/cli.py`, `studio/review/*`,
`studio/render/*`, `studio/judge/*`, `config/*.yaml` (put Windows-only values in
the override file below).

- **W1 Survey, report, then wait.** CPU, RAM, GPU, free disk per drive, Windows
  version, wired or Wi-Fi, measured download speed, what starts at boot, what
  takes space. Report: how many Tier A streams it can record at what quality
  (1080p60 ≈ 8.5 Mbps and ≈ 3.8 GB/h per stream; `config/streamers.yaml` has 31
  channels, 13 of them Tier A), and a proposed clean-up list. Change nothing yet.
- **W2 Clean up and make it always-on (each change approved by the user).**
  Never sleep or hibernate on AC; screen may turn off. Windows Update: active
  hours set and no automatic restart while capturing. Start the node at boot
  without anyone logging in (Task Scheduler, restart on failure); any step that
  needs the Windows password the user types themselves. Trim startup apps and
  uninstall unused software only from the approved list; never touch personal
  files. Defender exclusion for `C:\GTA6Studio\data` (many small writes).
  Tell the user to set BIOS "Restore on AC power loss = On" themselves (if
  the BIOS has it).
  **It is a laptop that runs with the lid closed, plugged in, on Wi-Fi:**
  lid close action on AC = "Do nothing" (screen off only); sleep, hibernate
  and "turn off hard disk" = never on AC; turn off Modern Standby / connected
  standby if it keeps sleeping anyway (check `powercfg /a`, and prove it with
  a real lid-closed test of 15+ min, checking the log for gaps). Wi-Fi adapter:
  turn off "allow the computer to turn off this device to save power". If the
  maker's app offers a battery charge limit (e.g. 60–80 %), propose it: a
  battery kept at 100 % 24/7 swells. Warn about heat with the lid shut: keep
  it on a hard surface, check CPU temperature under load once.
- **Self-healing (it must run for weeks with nobody touching it).** Every
  layer restarts the one below it:
  - the supervisor restarts any dead or hung child (no new chat lines for a
    live channel for 5+ min = hung), with back-off so a broken stream can't
    spin in a loop;
  - Task Scheduler starts the supervisor at boot and restarts it if it exits;
  - if the internet drops, everything waits and reconnects by itself (no
    crash loop), and the drop is written to coverage as a hole;
  - after a power cut or Windows Update reboot it comes back with no login;
  - a daily health check (scheduled task) restarts the node if the heartbeat
    is stale and frees disk if below budget;
  - logs in `data\logs\` are rotated so they can't fill the disk.
  Test it for real before calling it done: kill a child, kill the
  supervisor, pull the network for 2 minutes, reboot the PC. Each time the node
  must recover on its own and the Mac must see the hole in coverage.
- **W3 Install (safety summary before each).** git, uv, ffmpeg, streamlink,
  yt-dlp, Syncthing. Prefer winget packages from the official publishers.
- **W4 Make the code run on Windows.** `mlx-whisper` gets a
  `sys_platform == 'darwin'` marker. A git-ignored `config/settings.local.yaml`
  merged over `settings.yaml` holds Windows values (ffmpeg/ffprobe paths,
  `live.quality`, keep times, the `node:` block). Do not port the Mac watchdog
  (`caffeinate`, `pgrep`, process groups): write a small Windows supervisor in
  `studio/node/` that runs chatlog + vodwatch (+ capture in phase 2), restarts a
  dead child, writes heartbeat/coverage, and enforces the disk budget.
  `uv run pytest` must stay green on Windows (skip Mac-only tests cleanly).
- **W5 Phase 1 live.** chatlog + vodwatch under the supervisor, Syncthing set
  up with the two folders. Print the Windows Syncthing device ID for the user
  to pair with the Mac. Run it, check chat files grow for live channels.
- **W6 Phase 2.** Capture-only live mode writing the outbox per contract.
  Rolling raw buffer for Tier A sized by the W1 numbers. Rehearse 30 min on 2
  live channels; confirm pairs land in the outbox and sync. Push the branch,
  write status (what works, what does not) at the bottom of this file.

## M. Mac session (after W5, and again after W6)

- **M1** Install Syncthing on the Mac (safety summary first), pair with
  Windows, accept the two folders as Receive Only into `data/remote/win/`.
- **M2** `backfill.chat_files()` and `fillgaps.holes()` also read
  `data/remote/*/chat/`, with message dedupe; `backfill.streams()` also reads
  `data/remote/*/outbox/vods.jsonl`.
- **M3** Watchdog: on wake, gaps are cut down by Windows coverage; only
  stretches neither machine covered go to replay recovery.
- **M4** `studio import`: complete outbox pairs → moments (state `fetched`,
  source info kept, `node` in signals), dedupe against Mac moments of the same
  channel within `live.merge_s`, then the normal pipeline. Run automatically by
  the watchdog on wake and every few minutes.
- **M5** Process in signal-score order so the best clips are reviewable within
  minutes of getting home. Heartbeat fresh → Mac skips its own capture;
  stale > 5 min → Mac runs live as today. End-to-end test with the W6 rehearsal.

## Status

### Windows session, 2026-11-19 (branch `windows-node`)

**PC:** Acer Nitro AN515-55 laptop, i5-10300H, 32 GB, Windows 11 Home. Plugged in, lid closed, Wi-Fi. The W1 survey found
2.4 GHz 802.11n at about 19-25 Mbps (the Mac's line is 109 Mbps), so capture is limited to `node.max_streams: 3` streams at
720p60 (about 3.5 Mbps each; measured about 11 Mbps total with 3 recorded streams plus chat). With Ethernet or 5 GHz Wi-Fi,
raise `max_streams` and `live.quality` in `config/settings.local.yaml`.
Windows Syncthing device ID: `I5CL7JR-IWBEQDT-W3EQ2OS-IKLORAF-WBTQFSB-2I2P73O-CTNCF4O-XNJSHAM`. Paired with the Mac.

**What works (tested for real)**
- W1-W5 phase 1: `studio.chatlog` (27 channels, chat files grow) and `studio.vodwatch` (`outbox/vods.jsonl`) under the
  supervisor `python -m studio.node`; Syncthing runs as its child, two Send Only folders `gta6-chat`, `gta6-outbox`.
- W6 phase 2: `studio.node.capture` (capture-only). 30 min rehearsal: 94 spikes, 19 complete moment pairs in
  `outbox/moments/` (Twitch and Kick, Tier A cut from the rolling recording and Tier B from the Twitch rewind), 6 spikes
  skipped by the GTA 6 filter, no `.part` left behind, 1.9 GB raw video at the end (budget 60 GB, `raw_keep_h: 3`).
- Boot: Task Scheduler task `GTA6Node` (as SYSTEM, no login, at boot and every 5 min, a second copy exits on the lock) and
  `GTA6NodeHealth` (hourly: restarts a stale supervisor, frees disk).
- Self-healing tests: killed chatlog -> back in 13 s; killed the supervisor -> children died with it (job object), the
  scheduler had a new supervisor up 40 s later, the gap is in `coverage-*.jsonl`; internet blocked for 2 min -> detected in
  6 s, no restart loop, `chat_ok: []` for those minutes, all channels back within seconds.
- Lid-closed test (user closed the lid about 20 min, plugged in, Wi-Fi): over 45 min of coverage there is no gap > 90 s, all 27 chat
  connections stayed up every minute, no sleep/wake event in the System log, 27 new moment pairs were written during that time.
- Windows power/clean-up changes made (user approved): never sleep or hibernate on AC, screen off after 10 min, lid = do nothing,
  Fast Startup off, Wi-Fi power saving off, Update active hours 05:00-23:00 + no auto-restart with a user signed in, Defender
  exclusion for `data`, hourly clock sync, 15 startup entries and 5 background tasks disabled, old temp files and pip cache cleared.

**Contract details the Mac side should know**
- `heartbeat.json` and `coverage-*.jsonl` list channels by plain channel name (no platform prefix). `chat_ok` = channels with a
  live chat connection (Twitch: all joined channels while the IRC connection is up; Kick/YouTube per channel).
- The chat folder holds `*.jsonl` only (`.stignore` hides yt-dlp scratch files). Windows never deletes chat files (a delete would
  sync to the Mac); the outbox is trimmed after `outbox_keep_days`.
- Windows names Tier A chunks `YYYYmmddHHMMSS.ts` (local time; ffmpeg's strftime has no `%s` there). `ingest/live.py:chunks()`
  reads both names. Moment `t_start`/`t_end` are epoch seconds of the video.
- Local time zone of this PC is UTC+1 (W. Europe). Chat `t` and moment times are epoch UTC; only file names carry the local date.

**Not done / known issues**
- Reboot test: not run yet (see below once done). CPU temperature under load was not measured: no sensor is readable from Windows on this laptop.
- Streamlink warns that `--hls-duration` is deprecated (use `--stream-segmented-duration`); left alone because the Mac's streamlink
  may be older. Change it in `ingest/live.py:fetch_rewind` once both sides have a new streamlink.
- 2.4 GHz Wi-Fi limits capture to 3 streams; a spike on a Twitch channel without a recorder uses the rewind (max 2 at once, extra
  ones are skipped and logged). Non-Twitch Tier A channels without a recorder cannot be recovered after the fact.
- Not ported: the Mac watchdog; the pre-existing tests that need the Mac renderer or review runner are skipped on Windows
  (`tests/conftest.py`).
- A new code version needs `python -m studio.node restart` (no admin); Task Scheduler starts the fresh supervisor within 5 min.
- Git identity of this checkout is a GitHub noreply address, so no personal email is in the public history.
- To remove: run `schtasks /Delete /TN GTA6Node /F` and `GTA6NodeHealth` from an admin shell; startup entries can be re-enabled
  in Task Manager > Startup apps (backup of what was disabled: `data/w2-backup.json`).

(Windows and Mac sessions add notes here.)

**Mac side, 2026-11-19 22:15 (M1–M5 done):** Syncthing paired, both folders Receive Only in
`data/remote/win/`. Backfill/fill-gaps read the node's chat (same user + message within 3 s = one message),
its `vods.jsonl`, and skip spikes the node already has video for, so replay chat is only fetched for stretches
neither machine logged (coverage file not needed for that). `studio import --watch` (started by the watchdog)
imports complete pairs, skips ones the Mac's own live already cut, and processes strongest first. Tested on the
rehearsal: 4 imported, 1 skipped as duplicate, 2 rendered. `remote.mac_capture` stays `always` while the node
records only 3 streams at 720p on 2.4 GHz Wi-Fi; switch to `when_node_down` once it can match the Mac.
