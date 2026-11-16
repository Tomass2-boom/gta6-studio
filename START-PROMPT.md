# Starting prompt for the Fable planning session

Paste everything below the line into the first message of the new session
(model: Fable, effort: high). Then talk or type your ideas freely.

---

I'm building a GTA 6 content production studio. Claude watches live streams
and YouTube videos, finds the best moments, clips them, and I post the clips
to YouTube (Shorts and long form), TikTok and other platforms. I want it
heavily automated, high quality, and cheap to run in tokens.

This session is PLANNING ONLY. Do not write pipeline code yet. Your job:

1. INTERVIEW ME first. Ask me questions in small batches (max 4 at a time)
   until you understand my goals, channel style, audience, platforms, budget,
   hardware, how many clips per day I want, and how much manual review I want.
   Don't assume. Let me dump my ideas first, then ask what is missing.

2. RESEARCH before designing. For each pipeline stage (ingest streams and
   videos, transcribe, detect highlight moments, cut and reformat to
   vertical, captions, titles and thumbnails, scheduling and posting,
   analytics), look for proven existing tools (GitHub repos, CLIs, APIs)
   before proposing custom code. Give me the top 1-3 options per stage with
   stars, last update and licence, and your recommendation.

3. DESIGN for low token use. The pipeline must run as plain scripts
   (yt-dlp, local Whisper, ffmpeg, a scheduler). Claude only reads
   transcripts, chat logs and audio/scene timestamps, never raw video. Use
   Haiku for bulk scoring and filtering, Sonnet for titles, captions and
   hooks, with prompt caching. Claude Code is for BUILDING the system, not
   running it.

5. OUTPUT two files in this folder:
   - PLAN.md: architecture, stages, chosen tools, build order in small
     testable milestones, and a rough monthly cost estimate.
   - CLAUDE.md: short project rules for the build sessions (folder layout,
     commands, conventions, which model to use for what).
   Ask me to approve the plan before you finish.

Keep replies concise. When we pass about 150k tokens, tell me to press End
so the handoff is saved.
