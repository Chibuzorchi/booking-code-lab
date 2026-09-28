"""Environment loading helpers, shared by every provider's Settings.

A provider passes its own project root so the right .env / .env.<name> is read.
Nothing outside a Settings.load() should read os.environ directly.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env(project_root: Path, env_name: str | None = None) -> None:
    """Load .env.<env> (if named) then a plain .env, without overriding real env vars."""
    if env_name:
        candidate = project_root / f".env.{env_name}"
        if candidate.exists():
            load_dotenv(candidate, override=False)
    default = project_root / ".env"
    if default.exists():
        load_dotenv(default, override=False)


def get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default
