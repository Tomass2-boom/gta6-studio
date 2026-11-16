from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / f"{name}.yaml").read_text()) or {}
