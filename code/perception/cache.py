r"""diskcache wrappers (design.md \S11 "Caching"). Keyed by image_id for
images and by (user_id, message-set hash) for messages, so the dev loop is
seconds on a warm cache and a byte-identical re-run is asserted, not hoped
for.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import diskcache

from config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
_cache_cfg = load_config().get("cache", {})
_DIR_ENV_VAR = _cache_cfg.get("dir_env", "BUYORWAIT_CACHE_DIR")
_DEFAULT_DIR = _cache_cfg.get("default_dir", ".cache")


def _cache_dir() -> Path:
    return Path(os.environ.get(_DIR_ENV_VAR, str(REPO_ROOT / _DEFAULT_DIR)))


_image_cache: diskcache.Cache | None = None
_message_cache: diskcache.Cache | None = None
_explanation_cache: diskcache.Cache | None = None


def image_cache() -> diskcache.Cache:
    global _image_cache
    if _image_cache is None:
        _image_cache = diskcache.Cache(str(_cache_dir() / "images"))
    return _image_cache


def message_cache() -> diskcache.Cache:
    global _message_cache
    if _message_cache is None:
        _message_cache = diskcache.Cache(str(_cache_dir() / "messages"))
    return _message_cache


def explanation_cache() -> diskcache.Cache:
    global _explanation_cache
    if _explanation_cache is None:
        _explanation_cache = diskcache.Cache(str(_cache_dir() / "explanations"))
    return _explanation_cache


def message_set_key(user_id: str, message_ids: list[str]) -> str:
    """(user_id, message-set hash) -- stable regardless of input order, so
    the same set of messages for a user always hits the same cache entry."""
    digest = hashlib.sha256("|".join(sorted(message_ids)).encode("utf-8")).hexdigest()[:16]
    return f"{user_id}:{digest}"


def factsheet_batch_key(request_ids: list[str]) -> str:
    digest = hashlib.sha256("|".join(sorted(request_ids)).encode("utf-8")).hexdigest()[:16]
    return f"batch:{digest}"
