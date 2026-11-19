import json
import re
import requests
from ..config import load

PROMPT = """You pick moments for viral GTA gameplay Shorts from a streamer's transcript. {game_rule}{who}
Transcript lines are "[start-end] text" in seconds of the source video:
{lines}

Decide if this is a clip-worthy moment. KEEP these kinds:
- a big reaction: funny, shocking, a fail, a glitch, a fight or argument with an NPC, something going wrong;
- a "first time" discovery: the first time the streamer sees or tries a part of the game (the map, water,
  a vehicle, a sport, a phone, a feature, physics or damage, a menu, the character creator, the graphics)
  and reacts to it;
- the streamer's own verdict on the game told to chat: what they like or dislike, whether it is good, bad,
  realistic or worth the wait, how it compares to other games. These short opinions do very well;
- the game acting like real life and the streamer reacting with disbelief, anger or delight (a car needs a key,
  a club checks their age and refuses them, NPCs hear them, traffic, pumping gas, items vanish, real events
  recreated). These are the biggest hits of all;
- the streamer with the main characters in gameplay (Lucia or Jason: doing things for her, flirting,
  missing her, choosing her looks with chat), as long as it is the streamer talking, not a cutscene;
- a mini-story with a clear goal (steal a plane, free an animal, escape the police) that keeps escalating:
  these may run long, because viewers stay to see if it works.
DROP it if it is mostly story cutscene dialogue (characters talking the plot), story spoilers/endings,
dead air, songs or music lyrics, technical trouble (stream, camera, cables, settings, brightness),
or chatter that is not about the game. A big streamer reaction during a cutscene is fine only if the
streamer's own words fill most of the clip and nothing about the plot is given away.
If keep, cut it at sentence boundaries. Do NOT cut a good moment short (user ratings: clips of 7-10 s ended while viewers were still hooked):
- start ON the setup line: the first line that tells the viewer what to watch for (a question, the
  streamer saying what they are about to do or see, or the first words of the opinion). No warm-up before it.
- keep going while it stays interesting: a strong hook means viewers want to see what happens next, so let the
  moment play out to the PAYOFF (the punchline, the reaction peak, the last words of the opinion) and end right
  after it. Never end in the middle of the action, a sentence or before the payoff. Cut only where it gets
  boring or the payoff is done.
- There is no fixed length: it depends on the moment. A typical clip is about {len_min}-{len_max} s, but a clip with
  a great hook that gets more and more interesting as it plays should run longer, up to {len_cap} s. Stop at
  the payoff or the moment interest drops, not at a number.
Reply ONLY JSON: {{"keep": bool, "score": 1-10, "start": sec, "end": sec,
"title": "{title_hint}", "hashtags": ["#gta6", ...], "reason": "one line",
"keywords": ["up to 8 punchy words spoken in the clip to highlight in captions, no swear words or filler"]}}"""

GAME_RULE = "The game may be GTA 5 or GTA 6; never name the game in titles unless the transcript says so."
TITLE_HINT = "short hook title, no clickbait lies"
TITLE_HINT_WHO = ("short hook title in third person: the streamer's name, their emotion in capitals, and the exact "
                  "thing in the game, in the form '<Name> was <EMOTION> after finding out <the exact thing from this clip>'; "
                  "no clickbait lies")
WHO = "\nThe streamer is {name}."

LAUNCH_RULE = "Do not name the game in the title: a launch hook is put in front of it later."
LAUNCH_FIELD = (', "first_look": true only if the streamer sees a part of the game for the first time '
                '(menus, character creator, first spawn, first drive, the map, the graphics)')


def hook_title(title: str | None, launch: dict | None, first_look: bool, seed: int) -> str | None:
    """'Dad Grabs A Stranger' -> 'GTA 6 JUST DROPPED: Dad Grabs A Stranger'. Hooks rotate by seed so
    the feed is not one hook over and over; a first-look moment gets the first-look hook."""
    if not launch or not title or title.upper().startswith(launch["game"].upper()):
        return title
    hooks = launch["hooks"]
    hook = launch.get("first_look_hook") if first_look and launch.get("first_look_hook") else hooks[seed % len(hooks)]
    return f"{hook}: {title.strip()}"


def video_title(title: str | None, score) -> str | None:
    """Title as drawn ON the video. The launch hook (layout C hook line, layout A/B banner) only goes on the
    lower-rated clips (score <= launch.on_video_max_score, user 2026-11-18): the best clips stay clean.
    The Shorts title/description keep the hook on every clip."""
    lc = load("settings")["judge"].get("launch") or {}
    if not title or score is None or float(score) <= lc.get("on_video_max_score", 6):
        return title
    from ..render.captions import split_hook
    hook, rest = split_hook(title, lc.get("hooks", []) + [lc.get("first_look_hook") or ""])
    return rest if hook and rest else title


GAME_RE = re.compile(r"\bgta\s*-?\s*(6|six|vi)\b", re.I)


def launch_cfg(src: dict | None, segments: list[dict], when: float | None) -> dict | None:
    """The launch block from settings.yaml if this moment gets a launch-day hook title, else None.
    Only GTA 6 footage: a live stream at/after the unlock, or a transcript that names GTA 6.
    A GTA 5 video run through `studio vod` today never gets a GTA 6 title."""
    from datetime import datetime
    lc = load("settings")["judge"].get("launch") or {}
    if not lc.get("enabled"):
        return None
    t0 = datetime.fromisoformat(lc["from_utc"].replace("Z", "+00:00")).timestamp()
    t1 = datetime.fromisoformat(lc["until_utc"].replace("Z", "+00:00")).timestamp()
    live = bool(src) and "#live=" in (src.get("url") or "")
    named = any(GAME_RE.search(s["text"]) for s in segments) or bool(src and GAME_RE.search(src.get("title") or ""))
    in_window = when is not None and t0 <= when < t1
    if in_window and (live or named):
        return lc
    return None


KW_PROMPT = """These are the words spoken in a short gameplay clip:
{text}

Pick up to {n} single words from it that carry the punch (the joke, the shock, names of key things,
strong emotion words). Only words that appear in the text. No swear words, no filler (oh, man, bro, yeah). Reply ONLY JSON: {{"keywords": ["word", ...]}}"""


def _ask(prompt: str, model: str | None = None) -> dict:
    cfg = load("settings")["judge"]
    r = requests.post(cfg["ollama_url"] + "/api/chat", timeout=600, json={
        "model": model or cfg["model"], "stream": False, "think": False, "options": {"temperature": 0.3},
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    txt = r.json()["message"]["content"]
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"no JSON in reply: {txt[:200]}")
    return json.loads(m.group(0))


def as_list(v) -> list[str]:
    """Model may return a list or a comma-separated string."""
    if isinstance(v, str):
        v = re.split(r"[,;]\s*", v)
    return [str(k).strip() for k in (v or []) if str(k).strip()]


def keywords(text: str, n: int = 8, model: str | None = None) -> list[str]:
    """Words to highlight in captions, for moments judged before keywords existed."""
    for i in range(3):
        try:
            return as_list(_ask(KW_PROMPT.format(text=text, n=n), model).get("keywords"))[:n]
        except (ValueError, KeyError):
            if i == 2:
                return []


def snap_end(segments: list[dict], end: float, pad: float) -> float:
    """Never cut the payoff mid-sentence (user ratings 2026-11-18: 'he didn't finish the sentence').
    An end inside a transcript line moves to that line's end; then `pad` seconds so the last word rings out."""
    for s in segments:
        if s["start"] < end < s["end"]:
            end = s["end"]
            break
    return end + pad


def extend_short(segments: list[dict], start: float, end: float, min_len: float, max_gap: float = 3.0) -> float:
    """A clip shorter than `min_len` while the talk carries on is a cut-off clip (user ratings 2026-11-18: 7-10 s
    clips ended mid-hook). Move the end forward, one whole sentence at a time, through unbroken talk
    (gaps <= max_gap) until the clip reaches `min_len`."""
    for s in sorted(segments, key=lambda x: x["start"]):
        if end - start >= min_len:
            break
        if s["end"] > end and s["start"] - end <= max_gap:
            end = s["end"]
    return end


def judge(segments: list[dict], model: str | None = None, tries: int = 3, launch: dict | None = None,
          streamer: str | None = None) -> dict:
    for i in range(tries):
        try:
            return _judge(segments, model, launch, streamer)
        except (ValueError, KeyError):  # JSONDecodeError is a ValueError
            if i == tries - 1:
                raise


def prompt(lines: str, launch: dict | None = None, streamer: str | None = None) -> str:
    from ..config import clip_cfg
    c = clip_cfg(streamer)
    lo, hi = c.get("target_len_s", [8, 30])
    kw = dict(lines=lines, len_min=lo, len_max=hi, len_cap=c["max_len_s"],
              who=WHO.format(name=streamer) if streamer else "",
              title_hint=TITLE_HINT_WHO if streamer else TITLE_HINT)
    if launch:
        p = PROMPT.format(game_rule=LAUNCH_RULE, **kw)
        return p.replace('"reason": "one line",', '"reason": "one line"' + LAUNCH_FIELD + ",")
    return PROMPT.format(game_rule=GAME_RULE, **kw)


def _judge(segments: list[dict], model: str | None = None, launch: dict | None = None,
           streamer: str | None = None) -> dict:
    cfg = load("settings")["judge"]
    model = model or cfg["model"]
    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
    r = requests.post(cfg["ollama_url"] + "/api/chat", timeout=600, json={
        "model": model, "stream": False, "think": False,
        "options": {"temperature": 0.3},
        "messages": [{"role": "user", "content": prompt(lines, launch, streamer)}]})
    r.raise_for_status()
    txt = r.json()["message"]["content"]
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"no JSON in judge reply: {txt[:200]}")
    out = json.loads(m.group(0))
    out["start"], out["end"] = float(out.get("start", segments[0]["start"])), float(out.get("end", segments[-1]["end"]))
    return out
