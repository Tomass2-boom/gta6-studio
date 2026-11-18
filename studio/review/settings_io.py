"""Change config/*.yaml from the app, keeping every comment (ruamel.yaml round-trip)."""
import io
import os
import re
import tempfile
from pathlib import Path

from ruamel.yaml import YAML

from ..config import ROOT

SWITCHES = {  # app name -> (file, key path, English label, explanation)
    "sound_effects": ("style", ["sound_effects", "enabled"], "Sound effects",
                      "Whooshes, risers and booms on the big moments."),
    "face_track": ("style", ["face_track", "enabled"], "Face tracking",
                   "The webcam crop follows the streamer's face."),
    "marks": ("style", ["marks", "enabled"], "Circles and arrows",
              "Automatic circles/arrows on the hook or on \"look at that\". Off by your choice (they drifted)."),
}
PLATFORMS = ("twitch", "kick", "youtube")


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    y.representer.add_representer(type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    return y


def path(name: str) -> Path:
    return ROOT / "config" / f"{name}.yaml"


def read(name: str):
    return _yaml().load(path(name).read_text())


def dumps(doc) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def write(name: str, doc):
    """Atomic: a running `studio live` never sees a half-written file."""
    _write_text(name, dumps(doc))


def get_switch(key: str) -> bool:
    f, keys, *_ = SWITCHES[key]
    d = read(f)
    for k in keys:
        d = d[k]
    return bool(d)


def _write_text(name: str, text: str):
    p = path(name)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.stem}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, p)


def set_switch(key: str, on: bool):
    """Edits only the one `enabled:` value in the text, so style.yaml keeps its exact layout
    (ruamel would re-flow the long phrase lists)."""
    f, (block, field), *_ = SWITCHES[key]
    text = path(f).read_text()
    m = re.search(rf"^{block}:[^\n]*\n((?:[ #][^\n]*\n|\n)*?)  {field}: *(true|false)", text, re.M)
    if not m:
        raise KeyError(f"{block}.{field} not found in {f}.yaml")
    i = m.start(2)
    _write_text(f, text[:i] + ("true" if on else "false") + text[m.end(2):])


def parse_link(text: str) -> tuple[str | None, str]:
    """'https://kick.com/n3on' -> ('kick', 'n3on'); 'youtube.com/@Jelly' -> ('youtube', 'Jelly'); 'xqc' -> (None, 'xqc')."""
    t = text.strip()
    m = re.search(r"(twitch\.tv|kick\.com|youtube\.com)/(@?[\w.-]+)", t, re.I)
    if not m:
        return None, t.lstrip("@")
    plat = {"twitch.tv": "twitch", "kick.com": "kick", "youtube.com": "youtube"}[m[1].lower()]
    return plat, m[2].lstrip("@")


def streamers() -> list[dict]:
    doc = read("streamers")
    return [{"name": e.get("name"), "platform": e.get("platform", "twitch"), "channel": e.get("channel"),
             "tier": str(e.get("tier", "B")).upper(), "live": e.get("live", True) is not False,
             "note": e.get("note") or ""} for e in doc.get("streamers") or []]


def _find(seq, name: str) -> int:
    for i, e in enumerate(seq):
        if str(e.get("name", "")).lower() == name.lower():
            return i
    raise KeyError(name)


def add_streamer(name: str, link: str, platform: str | None = None, tier: str = "A"):
    from ruamel.yaml.comments import CommentedMap
    plat, ch = parse_link(link or name)
    plat = (platform or plat or "twitch").lower()
    if plat not in PLATFORMS:
        raise ValueError(f"platform must be one of {PLATFORMS}")
    name = (name or ch).strip()
    if not name or not ch:
        raise ValueError("give a name or a link")
    doc = read("streamers")
    seq = doc["streamers"]
    try:
        _find(seq, name)
        raise ValueError(f"{name} is already in the list")
    except KeyError:
        pass
    e = CommentedMap()
    e["name"], e["platform"], e["channel"] = name, plat, ch
    e["tier"] = "A" if str(tier).upper() == "A" else "B"
    e["allowed"] = None
    e["note"] = "added in the app"
    seq.append(e)
    write("streamers", doc)


def remove_streamer(name: str):
    doc = read("streamers")
    seq = doc["streamers"]
    del seq[_find(seq, name)]
    write("streamers", doc)


def set_streamer(name: str, live: bool | None = None, tier: str | None = None):
    doc = read("streamers")
    e = doc["streamers"][_find(doc["streamers"], name)]
    if live is not None:
        if live:
            e.pop("live", None)  # live.py treats a missing key as on
        else:
            e["live"] = False
    if tier is not None:
        e["tier"] = "A" if str(tier).upper() == "A" else "B"
    write("streamers", doc)


def blocklist() -> list[str]:
    return [str(x) for x in read("streamers").get("blocklist") or []]


def set_blocked(name: str, on: bool):
    from ruamel.yaml.comments import CommentedSeq
    doc = read("streamers")
    bl = doc.get("blocklist")
    if bl is None:
        bl = doc["blocklist"] = CommentedSeq()
    have = [str(x).lower() for x in bl]
    if on and name.lower() not in have:
        bl.append(name.strip())
    elif not on and name.lower() in have:
        del bl[have.index(name.lower())]
    write("streamers", doc)


def review_mode() -> str:
    return str(read("settings").get("review", {}).get("mode", "manual"))
