"""Parse bet9ja's GetBookABetCouponV2 payload into a core Coupon.

Response shape (unauthenticated GET):
    {"R": "OK"|"ERROR", "D": {"O": {<key>: <selection>, ...},
                              "BOOKABET_COUPONID", "BTYPE", "STAKE", "DATE"}}
Each selection under D.O carries the documented bet9ja keys (E_ID, E_NAME, GN,
GID, SGID, M_NAME, SGN, V, STARTDATE, ...). Total odds = product of the V values.
"""
from __future__ import annotations

import re
from typing import Any

from engine.coupon import Coupon, Selection, as_float, as_int

# A bet9ja odds key is "<E_ID>$<selector>" where the selector after "$" is the
# native, stable per-outcome token, e.g.
#   "835737049$S_OU@2.5_O" -> selector "S_OU@2.5_O", specifier "2.5"
#   "836018520$S_DC_1X"    -> selector "S_DC_1X",     specifier ""
_SPEC_RE = re.compile(r"@([^_$]+)")


def _native_outcome(key: str) -> tuple[str, str]:
    """Return (native_outcome_selector, specifier) parsed from an odds key."""
    sel = key.split("$", 1)[1] if "$" in key else key
    m = _SPEC_RE.search(sel)
    return sel, (m.group(1) if m else "")


def _selection(key: str, o: dict[str, Any]) -> Selection:
    outcome_sel, specifier = _native_outcome(key)
    market_id = "" if o.get("marketId") in (None, "") else str(o.get("marketId"))
    return Selection(
        key=key,
        native_event_id=str(o.get("E_ID", "")),
        market_id=market_id,
        outcome_id=outcome_sel,
        specifier=specifier,
        event_id=as_int(o.get("E_ID")) or 0,
        event_name=str(o.get("E_NAME", "")).strip(),
        league=str(o.get("GN", "")).strip(),
        league_id=as_int(o.get("GID")),
        subgroup_id=as_int(o.get("SGID")),
        sport=str(o.get("sportName", o.get("SPORT_ID", ""))).strip(),
        market=str(o.get("M_NAME", "")).strip(),
        sign=str(o.get("SGN", "")).strip(),
        odds=as_float(o.get("V")),
        start_date=str(o.get("STARTDATE", "")).strip(),
        is_bet_builder=bool(o.get("isBetBuilder", False)),
    )


def parse_coupon(code: str, payload: dict[str, Any] | None, provider: str = "bet9ja") -> Coupon:
    if not payload or payload.get("R") != "OK":
        err = str(payload.get("R")) if payload else "NO_RESPONSE"
        return Coupon(code=code, ok=False, error=err, provider=provider)
    data = payload.get("D", {}) or {}
    legs = data.get("O", {}) or {}
    selections = [_selection(k, v) for k, v in legs.items()]
    return Coupon(
        code=code,
        ok=True,
        selections=selections,
        coupon_id=as_int(data.get("BOOKABET_COUPONID")),
        stake=as_float(data.get("STAKE")),
        created=str(data.get("DATE")) if data.get("DATE") else None,
        provider=provider,
        bet_type=str(data["BTYPE"]) if data.get("BTYPE") is not None else None,
    )
