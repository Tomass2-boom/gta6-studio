# Health check runbook (for Haiku or any model)

Use when the user says "check everything", "make sure everything runs", "fix the errors".
Work from the project folder. Do not watch video. Do not post anything. Do not change
`review.mode`. Never print `.env`. Keep replies short: what was wrong, what you fixed, what is still broken.

## 1. Check (run these in parallel)

```bash
uv run studio doctor                 # every line must say OK
uv run pytest -q                     # all tests must pass
ps aux | grep -E "studio live|studio.watchdog|studio.review.window|ollama serve" | grep -v grep
ls -t data/logs | head
```

Then scan today's logs for real problems (replace the date):

```bash
cd data/logs && for f in $(date +%F)-*.log app-window.log; do echo "== $f"; grep -iE "traceback|error|exception|crash" "$f" | tail -8 | cut -c1-200; done
```

## 2. What is normal (do NOT "fix")

- `recorder stopped (offline?)`: the streamer went offline.
- `No playable streams found`, `cut failed` for an offline streamer.
- `Kick chat connection lost ... retry in 2s`: auto-retries.
- Twitch `replay chat failed ... service error`: Twitch outage, fill-gaps retries later.
- `WATCHDOG live is NOT running`: the watchdog restarts it; only a problem if it keeps repeating
  AND `studio live` is still not in `ps` afterwards.
- Old log lines from earlier hours: only care about errors newer than the last code change.

## 3. What is a real problem

| Symptom | Fix |
|---|---|
| `doctor` line not OK | Install/start what it names (`ollama serve`, `brew install ...`). Tell the user before installing. |
| pytest failure | Read the test, fix the code (not the test) unless the test is wrong. |
| Python traceback in a log from a `studio/` file | Fix that file, add a test if cheap. |
| `studio live` not running and watchdog not running | Start: `caffeinate -i uv run studio live` and `uv run python -m studio.watchdog` in the background (the app's Start button does the same). |
| YAML ParserError | Validate: `uv run python -c "import yaml;yaml.safe_load(open('config/X.yaml'))"`. Fix the file. |
| Disk under ~30 GB free | Raw recordings older than 24 h are safe to delete (`data/raw/`). Never delete `data/clips/` or `data/studio.db`. |
| Moments stuck in a stage | `uv run studio moments`; re-run only that stage. |

## 4. Rules while fixing

- Smallest fix that works; follow CLAUDE.md hard rules (no paid services, no Anthropic API, look in config).
- Ask the user before: deleting clips/DB, installing software, posting, changing config thresholds.
- After fixing: re-run `uv run pytest -q` and `uv run studio doctor`.
- Commit only if the user asked, with a plain message of what was fixed.

## 5. Report format

1. Status: running / not running (live, watchdog, review app, ollama).
2. Errors found and fixed (file + one line each).
3. Errors found and left alone, with the reason (normal or needs the user).
4. Anything the user must do.

Last updated: 2026-11-19.
