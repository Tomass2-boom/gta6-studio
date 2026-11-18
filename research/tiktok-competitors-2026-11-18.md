# TikTok competitor study — GTA 6 launch day (2026-11-18)

Source: 11 profile screenshots from the user, the last N videos of 11 accounts (302 videos, yt-dlp metadata),
and 61 top or reference clips downloaded and studied (Whisper transcripts + 4-frame strips). All 5 links the user sent
resolve. They are all from @overtime: Kai "adaptive triggers" 582K (8 s), Lacy "mid" 515K (13 s), N3on water 110K (8 s),
N3on 2K 110K (21 s), xQc house 107K (12 s).

Accounts (handle, videos studied, median views, best video):
@itsnicktheplug 14, 20K, 465K | @thehomeofclipss 75, 7K, 921K | @onlyclipinn 66, 7.5K, 1.3M | @liuclipper 60, 14K, 832K |
@kicktocky 20, 9K, 577K | @moreclixtv (Clix) 16, 54K, 1.2M | @nixstah 7, 344K, 2.0M | @typicalgamer 12, ~1M, 3.0M |
@lacy 6, ~600K, 3.0M | @kick 6, ~11K, 2.4M | @twtchitskloa 20 (mostly Fortnite, long horizontal "Part 1/2" clips), 469K best.
Streamers' own accounts (Lacy, Clix, Typical Gamer, Nixstah) are big, so their medians say more about account size.
For where to cut, weight the clipper accounts.

## What the winning clips have in common
1. **Short and tight.** Clipper hits run 7–24 s (top-quartile median 20 s). Each one opens ON the setup line and ends ON
   the payoff, with no warm-up and no outro. Examples of setup → payoff: "Open the map" → "wait, the map is huge"
   (14 s, 1.1M). "Check the phone" → "I'm getting left on delivered" (14 s, 502K). "Hey little buddy" → "No!"
   (10 s, 3M).
2. **The streamer's verdict told to chat is a top format:**
   - "How's the game so far… it's mid" (515K)
   - "only thing I don't like…" (465K)
   - "hella traffic" (342K)
   - "it's not just a game" (3M)

   Our judge was DROPPING these as "chatter". Fixed 2026-11-18.
3. **"First time" discoveries:** water, the map, a plane or jet, a dog, the Drake radio, basketball (2K comparisons),
   auto-drive, car damage, the phone, NPC fights or road rage, getting rejected at the club, the police.
4. **Titles name the streamer and the specific thing:** "Lacy meets a Dog in GTA VI", "xqc spends the first 45 minutes
   renovating his house", "N3on TRIED to enter the CLUB and it went WRONG". Generic "X was shocked / reveals / reacts"
   titles sit lower.
5. **Same moment, many accounts.** Lacy's map was posted by @lacy at 14 s (1.1M) and by @itsnicktheplug at 28 s (73K).
   Being early and tight wins. Most hits were posted in the first 1–3 h after the unlock. The per-hour medians are
   not proof that views fade, because later posts were simply younger when scraped.
6. **Layout doesn't separate winners from losers.** Facecam-top / game-bottom with a hook box is the most common.
   Corner-cam full gameplay (Lacy, Clix) does well too. Almost none burn word-by-word captions (ours do; keep it).
7. **Streamers that drew the views:** N3on (most clips), Lacy, Clix, Adin Ross, xQc, Kai Cenat, IShowSpeed,
   Typical Gamer, Nixstah, TheBurntPeanut. We added Lacy, Clix, Nixstah, TheBurntPeanut and Typical Gamer that evening.
8. **Skip:** in-game radio songs (music copyright), story cutscenes (Clix's cutscene clips got only 9–41K anyway).

## Changes made from this
- The judge keeps verdict-to-chat and first-time moments. It drops tech trouble and songs.
- Cuts run from the setup line to the payoff, aiming at `clip.target_len_s` [8, 30], with a 45 s cap.
- The end snaps to the end of the sentence plus `clip.end_pad_s`.
- Titles name the streamer.
