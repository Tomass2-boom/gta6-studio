# GTA6 Studio

Finds the best GTA 6 stream moments and turns them into vertical captioned clips.
Details: `CLAUDE.md` (rules) and `PLAN.md` (plan).

## Opening the app (no Terminal needed)

1. Double-click **GTA6 Studio** in your Applications folder (`~/Applications`, in Finder:
   Go → Home → Applications). Drag it to the Dock once so it is always one click away.
2. The window has four tabs:
   - **▶ Clipping**: the big **Start clipping** button watches every streamer in your list,
     records the live ones and makes clips. It keeps the Mac awake. Press **Stop clipping** to
     end it. You see who is live and a plain-English list of what is happening.
   - **🎬 Clips**: newest clips first. Click one to play it and copy its title, description and
     hashtags (each has a **Copy** button). **Show in Finder** opens the file. Mark where you
     posted it, give it a grade from 1 to 10 and write what you liked or didn't like.
   - **⚙️ Settings**: switches for sound effects, face tracking and circles/arrows; the
     streamer list (add, remove, switch on/off); the blocklist. Posting mode stays "manual".
   - **🧰 Tools**: health check, make clips from a video link.
3. Closing the window does **not** stop clipping. Reopen the app to see it or stop it.

If the app ever disappears, rebuild it with `uv run studio make-app`. The same window also opens
with `uv run studio review` (add `--browser` to use the web browser instead).

The app's log is `data/logs/app-window.log`; clipping's output is `data/logs/<date>-app-live.log`.

## Setting it up on another Mac

Clone this repo and open it in Claude Code. Tell it: "Read CLAUDE.md, PLAN.md and README.md and
get this running on my Mac." Things it will need to change on a different machine:

- **Not in the repo** (make your own): `.env` with your own posting accounts/API keys, and the
  `data/` folder (recordings, clips, database). Both are git-ignored.
- **Tools to install:** `uv`, `ffmpeg`, `streamlink`, `yt-dlp`, `Ollama`. Run `uv run studio doctor`
  to see what is missing.
- **Intel Mac:** `mlx-whisper` only runs on Apple chips. Swap it for `faster-whisper` or
  `whisper.cpp` in `studio/transcribe/` and `pyproject.toml`.
- **Less than ~24 GB RAM:** the judge model in `config/settings.yaml` (`qwen3.8:27b`, 18 GB) is too
  big. Use a small Ollama model (3-5 GB) instead.
- `config/streamers.yaml` and `config/style.yaml` are the original owner's choices. Change them freely.
- `.claude/handoffs/` holds build-session notes: useful history for Claude.
