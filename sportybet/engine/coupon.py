"""Provider-agnostic coupon model + total-odds / dedupe logic.

Each provider parses its own API response into these types (see the provider's
client). Nothing here knows about bet9ja's R/D shape or sportybet's bizCode/data
shape — that lives in the provider parser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import math


def as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _finite(value: Any) -> float | None:
    """null-out non-finite numbers so exports stay valid JSON."""
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return None


def _sanitize(value: Any) -> Any:
    """Recursively replace non-finite floats with None (JSON-safe export)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _sanitize(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(val) for val in value]
    return value


@dataclass
class Selection:
    key: str                 # stable per-selection key, used for dedupe
    event_id: int
    event_name: str          # "Home - Away"
    league: str
    market: str              # "1X2", "Over/Under", ...
    sign: str                # picked outcome: "1" | "X" | "2" | "Over" | ...
    odds: float
    start_date: str = ""
    league_id: int | None = None
    subgroup_id: int | None = None
    sport: str = ""
    is_bet_builder: bool = False
    native_event_id: str = ""
    market_id: str = ""
    outcome_id: str = ""
    specifier: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "selection_key": self.key,
            "native_event_id": self.native_event_id,
            "market_id": self.market_id,
            "outcome_id": self.outcome_id,
            "specifier": self.specifier,
            "sport": self.sport,
            "is_bet_builder": self.is_bet_builder,
            "event_id": self.event_id,
            "event": self.event_name,
            "league": self.league,
            "market": self.market,
            "pick": self.sign,
            "odds": _finite(self.odds),
            "kickoff": self.start_date,
        }

    def __str__(self) -> str:
        return f"{self.event_name} | {self.league} | {self.market} {self.sign} @ {self.odds}"


@dataclass
class Coupon:
    code: str
    ok: bool
    selections: list[Selection] = field(default_factory=list)
    coupon_id: int | str | None = None
    stake: float = 0.0
    created: str | None = None
    error: str | None = None
    provider: str = ""
    # Source-displayed value. Its payout meaning is not verified; legacy total_odds
    # still falls back to the product for compatibility with existing scanners.
    site_total_odds: float | None = None
    bet_type: str | None = None
    selected_systems: Any = None
    num_unavailable: int = 0
    mapping_errors: list[str] = field(default_factory=list)
    observed_at: str | None = None
    raw_response_path: str | None = None
    retrieval_completeness: str = "unknown"
    odds_context: dict = field(default_factory=dict)
    available: bool = True        # all legs still upcoming (sportybet: set by parser)

    @property
    def computed_odds(self) -> float:
        """Straight accumulator product of every leg's odds."""
        total = 1.0
        for sel in self.selections:
            total *= sel.odds or 1.0
        return round(total, 2)

    @property
    def parsed_leg_product(self) -> float | None:
        values = [s.odds for s in self.selections]
        if not values or any(not math.isfinite(v) or v <= 1 for v in values):
            return None
        value = math.prod(values)
        return value if math.isfinite(value) else None

    @property
    def total_odds(self) -> float:
        if self.site_total_odds is not None:
            return round(self.site_total_odds, 2)
        return self.computed_odds

    @property
    def event_ids(self) -> frozenset[int]:
        return frozenset(s.event_id for s in self.selections)

    @property
    def fingerprint(self) -> frozenset[str]:
        """Coupon identity by its exact selections — used to dedupe."""
        return frozenset(s.key for s in self.selections)

    @property
    def num_legs(self) -> int:
        return len(self.selections)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "site_displayed_odds": _finite(self.site_total_odds),
            "parsed_leg_product": _finite(self.parsed_leg_product),
            "payout_semantics": {"status": "unknown", "multiplier": None},
            "retrieval_completeness": self.retrieval_completeness,
            "odds_context": _sanitize(self.odds_context),
            "bet_type": self.bet_type,
            "selected_systems": _sanitize(self.selected_systems),
            "num_unavailable": self.num_unavailable,
            "mapping_errors": self.mapping_errors,
            "observed_at": self.observed_at,
            "raw_response_path": self.raw_response_path,
            "code": self.code,
            "ok": self.ok,
            "provider": self.provider,
            "coupon_id": self.coupon_id,
            "num_legs": self.num_legs,
            "total_odds": _finite(self.total_odds),
            "computed_odds": _finite(self.computed_odds),
            "available": self.available,
            "selections": [s.as_dict() for s in self.selections],
            "error": self.error,
        }

    def __str__(self) -> str:
        if not self.ok:
            return f"[{self.code}] ERROR ({self.error})"
        legs = "\n    ".join(str(s) for s in self.selections)
        return f"[{self.code}] {self.num_legs} legs, total odds {self.total_odds}\n    {legs}"
