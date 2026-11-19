from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / f"{name}.yaml").read_text()) or {}


def clip_cfg(streamer: str | None = None) -> dict:
    """settings.yaml `clip`, with a streamer's own `clip:` overrides from streamers.yaml on top
    (e.g. ohnePixel max_len_s: 120 for long goal-driven arcs, competitor study 2026-11-19)."""
    c = dict(load("settings")["clip"])
    if streamer:
        for s in load("streamers").get("streamers", []):
            if str(s.get("name", "")).lower() == streamer.lower():
                c.update(s.get("clip") or {})
                break
    return c
