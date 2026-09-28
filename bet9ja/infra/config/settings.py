"""bet9ja settings: shared scan knobs (from engine) + bet9ja-specific fields."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from engine import env as _env
from engine.settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # the bet9ja/ dir


@dataclass
class Settings(BaseSettings):
    # --- API path ---
    sports_url: str = "https://sports.bet9ja.com/"
    coupon_api_base: str = "https://coupon.bet9ja.com/desktop/feapi/CouponAjax"
    cache_version: str = "1.326.2.248"
    # --- UI path (navigation: Soccer -> country toggle -> competition anchor) ---
    sport: str = "soccer"
    league_toggle_id: str = "left_prematch_sport-1_soccer_sg-11058_england_label-toggle"
    league_name: str = "England"
    competition_anchor_id: str = (
        "left_prematch_sport-1_soccer_sg-11058_england_g-170880_premier_league"
    )

    @classmethod
    def load(cls, env_name: str | None = None) -> "Settings":
        _env.load_env(PROJECT_ROOT, env_name or os.getenv("BET_ENV"))
        return cls(
            provider=_env.get("PROVIDER", "bet9ja"),
            **BaseSettings.shared_kwargs(),
            sports_url=_env.get("SPORTS_URL", "https://sports.bet9ja.com/"),
            coupon_api_base=_env.get(
                "COUPON_API_BASE", "https://coupon.bet9ja.com/desktop/feapi/CouponAjax"),
            cache_version=_env.get("CACHE_VERSION", "1.326.2.248"),
            sport=_env.get("SPORT", "soccer"),
            league_toggle_id=_env.get(
                "LEAGUE_TOGGLE_ID",
                "left_prematch_sport-1_soccer_sg-11058_england_label-toggle"),
            league_name=_env.get("LEAGUE_NAME", "England"),
            competition_anchor_id=_env.get(
                "COMPETITION_ANCHOR_ID",
                "left_prematch_sport-1_soccer_sg-11058_england_g-170880_premier_league"),
        )
