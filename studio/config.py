from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _merge(base: dict, over: dict) -> dict:
    """Deep merge: `over` wins; nested dicts are merged key by key, everything else is replaced."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load(name: str) -> dict:
    """config/<name>.yaml, with config/<name>.local.yaml (git-ignored, per machine) merged over it if present.
    The Windows capture node keeps its tool paths, quality and `node:` block in settings.local.yaml."""
    base = yaml.safe_load((ROOT / "config" / f"{name}.yaml").read_text(encoding="utf-8")) or {}
    local = ROOT / "config" / f"{name}.local.yaml"
    if local.exists():
        base = _merge(base, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    return base


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
