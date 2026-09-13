r"""Load code/config.yaml -- the design.md \S3 pinned decisions live here,
not in code, so flipping one is a config edit + re-score (stack.md \S7)."""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_cache: dict | None = None


def load_config(path: Path = CONFIG_PATH) -> dict:
    global _cache
    if _cache is None:
        with open(path, "r", encoding="utf-8") as f:
            _cache = yaml.safe_load(f)
    return _cache
