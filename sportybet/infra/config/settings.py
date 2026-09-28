"""sportybet settings: shared scan knobs (core) + sportybet-specific fields.

sportybet booking codes are 6 chars and CASE-INSENSITIVE (the API upper-cases
them), so the mutation charset is base36-uppercase, not bet9ja's base62.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from engine import env as _env
from engine.settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # the sportybet/ dir
BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class Settings(BaseSettings):
    site_url: str = "https://www.sportybet.com/ng/"
    # Unauthenticated share-code lookup: GET .../share/<CODE> -> {"bizCode","data",...}
    share_api_base: str = "https://www.sportybet.com/api/ng/orders/share"

    @classmethod
    def load(cls, env_name: str | None = None) -> "Settings":
        _env.load_env(PROJECT_ROOT, env_name or os.getenv("BET_ENV"))
        shared = BaseSettings.shared_kwargs()
        shared["charset"] = _env.get("CHARSET", BASE36)   # base36 default for sportybet
        return cls(
            provider=_env.get("PROVIDER", "sportybet"),
            **shared,
            site_url=_env.get("SITE_URL", "https://www.sportybet.com/ng/"),
            share_api_base=_env.get(
                "SHARE_API_BASE", "https://www.sportybet.com/api/ng/orders/share"),
        )
