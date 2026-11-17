import json
import re
import requests
from ..config import load

PROMPT = """You pick moments for viral GTA gameplay Shorts (the game may be GTA 5 or GTA 6; never name the game in titles unless the transcript says so) from a streamer's transcript.
Transcript lines are "[start-end] text" in seconds of the source video:
{lines}

Decide if this is a clip-worthy moment (funny, shocking, skillful, a big reaction, a glitch).
DROP it if it is mostly in-game cutscene dialogue, story spoilers/endings, dead air, or chatter.
If keep, choose start/end snapped to sentence boundaries (15-50 s long, 60 max) so the clip has
a hook at the start and the payoff before the end.
Reply ONLY JSON: {{"keep": bool, "score": 1-10, "start": sec, "end": sec,
"title": "short hook title, no clickbait lies", "hashtags": ["#gta6", ...], "reason": "one line",
"keywords": ["up to 8 punchy words spoken in the clip to highlight in captions, no swear words or filler"]}}"""

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


def judge(segments: list[dict], model: str | None = None, tries: int = 3) -> dict:
    for i in range(tries):
        try:
            return _judge(segments, model)
        except (ValueError, KeyError):  # JSONDecodeError is a ValueError
            if i == tries - 1:
                raise


def _judge(segments: list[dict], model: str | None = None) -> dict:
    cfg = load("settings")["judge"]
    model = model or cfg["model"]
    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
    r = requests.post(cfg["ollama_url"] + "/api/chat", timeout=600, json={
        "model": model, "stream": False, "think": False,
        "options": {"temperature": 0.3},
        "messages": [{"role": "user", "content": PROMPT.format(lines=lines)}]})
    r.raise_for_status()
    txt = r.json()["message"]["content"]
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"no JSON in judge reply: {txt[:200]}")
    out = json.loads(m.group(0))
    out["start"], out["end"] = float(out.get("start", segments[0]["start"])), float(out.get("end", segments[-1]["end"]))
    return out
