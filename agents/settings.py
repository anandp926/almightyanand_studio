#!/usr/bin/env python3
"""
settings.py — Single place for reading configuration.

Lookup order: process environment, then the project's .env file.
An empty environment value counts as unset, because docker-compose passes
`${VAR:-}` through as an empty string.
"""

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

STUDIO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = STUDIO_ROOT / ".env"


def _parse_dotenv(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            # Drop trailing inline comments: `KEY=value   # note`
            if value.startswith("#"):
                value = ""
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


@lru_cache(maxsize=1)
def _dotenv() -> Dict[str, str]:
    try:
        return _parse_dotenv(ENV_FILE.read_text(encoding="utf-8"))
    except OSError:
        return {}


def reload() -> None:
    """Forget the cached .env contents (used by tests)."""
    _dotenv.cache_clear()


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value:
        return value
    value = _dotenv().get(name)
    return value if value else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except (TypeError, ValueError):
        return default


def ffmpeg_bin() -> str:
    """FFMPEG_BIN if set, otherwise whatever `ffmpeg` is on PATH."""
    return env("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
