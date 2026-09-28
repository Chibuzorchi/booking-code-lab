"""Base settings: the scan knobs + shared runtime, common to all providers.

Provider settings subclass BaseSettings, add their own fields (URLs, endpoint,
DOM ids), and call `shared_kwargs()` to fill the shared fields from env.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine import env as _env

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
BASE62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


@dataclass
class BaseSettings:
    # --- shared runtime ---
    provider: str = ""
    user_agent: str = DEFAULT_UA
    results_dir: Path = Path("results")
    headless: bool = True
    browser_channel: str = ""      # "" = bundled chromium; "chrome" uses installed Chrome (bypasses Akamai)
    locale: str = "en-NG"
    timezone_id: str = "Africa/Lagos"

    # --- scan engine knobs (used by API scans AND UI book-then-scan) ---
    seed_code: str = ""
    min_total_odds: float = 10.0
    charset: str = BASE62
    max_mutation_depth: int = 3
    max_codes_to_try: int = 2000
    max_qualifying: int = 20
    top_n: int = 0                # output cap: rank by odds, keep best N (0 = all)
    max_total_odds: float = 0.0   # sanity: drop coupons above this (0 = no cap)
    min_legs: int = 0             # sanity: drop coupons with fewer legs (0 = no floor)
    max_legs: int = 0             # sanity: drop coupons with more legs (0 = no ceiling)
    request_delay_ms: int = 120
    request_timeout_s: float = 20.0
    request_workers: int = 8

    @staticmethod
    def shared_kwargs() -> dict:
        """Read the shared fields from env (call after engine.env.load_env)."""
        return {
            "user_agent": _env.get("USER_AGENT", DEFAULT_UA),
            "results_dir": Path(_env.get("RESULTS_DIR", "results")),
            "headless": _env.get_bool("HEADLESS", True),
            "browser_channel": _env.get("BROWSER_CHANNEL", ""),
            "locale": _env.get("LOCALE", "en-NG"),
            "timezone_id": _env.get("TIMEZONE_ID", "Africa/Lagos"),
            "seed_code": _env.get("SEED_CODE", ""),
            "min_total_odds": _env.get_float("MIN_TOTAL_ODDS", 10.0),
            "charset": _env.get("CHARSET", BASE62),
            "max_mutation_depth": _env.get_int("MAX_MUTATION_DEPTH", 3),
            "max_codes_to_try": _env.get_int("MAX_CODES_TO_TRY", 2000),
            "max_qualifying": _env.get_int("MAX_QUALIFYING", 20),
            "top_n": _env.get_int("TOP_N", 0),
            "max_total_odds": _env.get_float("MAX_TOTAL_ODDS", 0.0),
            "min_legs": _env.get_int("MIN_LEGS", 0),
            "max_legs": _env.get_int("MAX_LEGS", 0),
            "request_delay_ms": _env.get_int("REQUEST_DELAY_MS", 120),
            "request_timeout_s": _env.get_float("REQUEST_TIMEOUT_S", 20.0),
            "request_workers": _env.get_int("REQUEST_WORKERS", 8),
        }
