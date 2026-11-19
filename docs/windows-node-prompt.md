You are setting up this Windows PC as the always-on capture node for my GTA 6 clip pipeline. My MacBook does the heavy work (Whisper, local LLM judge, rendering, posting) but it sleeps in my bag during the day, and we have been losing moments. This PC stays at home 24/7. Its job: log every streamer's chat, note stream starts and VOD addresses, and (phase 2) save video at chat spikes, then sync all of it to the Mac with Syncthing. When I get home the Mac makes the clips from what this PC saved. It does not need to run AI models.

1. Clone https://github.com/Tomass2-boom/gta6-studio into C:\GTA6Studio (not inside OneDrive, no spaces). If git is missing, install it first (rule 4 below).
2. Read CLAUDE.md, then docs/windows-node.md. The second one is the full plan. Follow section C (the contract with the Mac) exactly and do sections W1 to W6 in order. Read other files only when a step needs them.
The PC is a laptop. It will be plugged in, on Wi-Fi, with the LID CLOSED, and nobody will touch it for weeks. Closing the lid must only turn the screen off, never sleep. Everything must restart by itself after a crash, a lost internet connection, a power cut or a Windows Update reboot. The plan's W2 section and "Self-healing" list say how, and you must prove each one with a real test before you call it done.

3. Start with W1: survey this PC and report back. Do not change anything until I approve your clean-up list and your plan for how many streams it can record and at what quality.

Rules:
4. Before installing anything: check the source, stars, last update and licence, read any install script, and tell me in one or two lines what will be installed. Prefer official winget packages. Never pipe a remote script into a shell.
5. Clean-up and Windows settings: list each change first, then do only what I approve. Never delete my personal files. Any step that needs my Windows password, BIOS or a sign-in, tell me and I do it myself.
6. Code: commit to branch windows-node, never main. The repo is public, so never commit secrets, cookies or tokens (they go in .env, which is git-ignored). Only edit the files the plan allows. The Mac session owns the rest, and we must not both edit the same files. Run uv run pytest before each commit.
7. No paid services, no Anthropic API at runtime. Prefer proven tools over custom code.
8. Save tokens without lowering quality: read only what you need, batch independent commands, run long jobs in the background and don't poll. Never watch video; check a recording with ffprobe or one small contact sheet.
9. At the end: push the branch, write what works and what doesn't in the Status section of docs/windows-node.md, and give me the Syncthing device ID of this PC so I can pair it with the Mac.
