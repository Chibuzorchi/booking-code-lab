"""Parse sportybet's share-code payload into a core Coupon.

Real envelope (GET /api/ng/orders/share/<CODE>):
    {"bizCode":10000,"message":"Success","data":{
        "shareCode":"WXP8AY",
        "ticket":{"selections":[...],            # the picks (ids only)
                  "displayTotalOdds":"8.52",     # authoritative total (system-aware)
                  "bets":[{"selectedSystems":[6],"stake":...}],
                  "betType"/"potentialWinnings"/"totalStake"...},
        "outcomes":[{                            # rich per-event data
            "eventId":"sr:match:74158496","gameId":"38061",
            "homeTeamName":...,"awayTeamName":...,
            "estimateStartTime":<epoch ms>,"matchStatus":...,"bookingStatus":"Booked",
            "sport":{"name":"Football","category":{"name":<country>,
                     "tournament":{"id":...,"name":<league>}}},
            "markets":[{"id":"18","specifier":"total=3.5","desc":"Over/Under",
                        "outcomes":[{"id":"12","odds":"1.18","desc":"Over 3.5",
                                     "isWinning":1}]}]}],
        "betType":"multiple"}}

Each `ticket.selections[]` reference must resolve to exactly one outcome,
including its specifier when supplied. Ambiguous/missing references fail closed;
multiple selections on one event are preserved.
NB: sportybet codes are case-INSENSITIVE — the API upper-cases them (WXP8Ay ->
WXP8AY); the client normalizes before lookup.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from engine.coupon import Coupon, Selection, as_float, as_int

BIZ_OK = 10000


def _event_id_int(sr_id: str) -> int:
    # "sr:match:74158496" -> 74158496 ; keeps big live ids intact
    tail = str(sr_id).rsplit(":", 1)[-1]
    return as_int(tail) or 0


def _iso(epoch_ms: Any) -> str:
    ms = as_int(epoch_ms)
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _selection(ev: dict[str, Any], market: dict, outcome: dict) -> Selection:
    eid = str(ev.get("eventId", ""))
    sport = ev.get("sport", {}) or {}
    category = sport.get("category", {}) or {}
    tournament = category.get("name") and category.get("tournament", {}) or {}

    specifier = str(market.get("specifier", ""))
    key = f"{eid}|{market.get('id','')}|{specifier}|{outcome.get('id','')}"
    country = str(category.get("name", ""))
    league = str(tournament.get("name", "")).strip()
    return Selection(
        key=key,
        native_event_id=eid,
        market_id=str(market.get("id", "")),
        outcome_id=str(outcome.get("id", "")),
        specifier=specifier,
        event_id=_event_id_int(eid),
        event_name=f"{ev.get('homeTeamName','')} - {ev.get('awayTeamName','')}".strip(" -"),
        league=f"{country} · {league}".strip(" ·") if country or league else "",
        league_id=_event_id_int(tournament.get("id", "")) or None,
        subgroup_id=_event_id_int(category.get("id", "")) or None,
        sport=str(sport.get("name", "")),
        market=str(market.get("desc", "")),
        sign=str(outcome.get("desc", "")),
        odds=as_float(outcome.get("odds")),
        start_date=_iso(ev.get("estimateStartTime")),
    )


def parse_share(code: str, payload: dict[str, Any] | None,
                provider: str = "sportybet") -> Coupon:
    if not payload or payload.get("bizCode") != BIZ_OK:
        msg = (payload or {}).get("message") or "NO_RESPONSE"
        return Coupon(code=code, ok=False, error=str(msg), provider=provider)
    data = payload.get("data", {}) or {}
    ticket = data.get("ticket", {}) or {}
    outcomes = data.get("outcomes", []) or []
    selections, errors = [], []
    refs = ticket.get("selections", []) or []
    if not refs:
        errors.append("Missing explicit selected references")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or any(ref.get(k) in (None, "") for k in ("eventId", "marketId", "outcomeId")):
            errors.append(f"Invalid selected reference {index}")
            continue
        matches = []
        for ev in outcomes:
            if not isinstance(ev, dict) or str(ev.get("eventId", "")) != str(ref["eventId"]):
                continue
            for market in ev.get("markets", []) or []:
                if str(market.get("id", "")) != str(ref["marketId"]):
                    continue
                if "specifier" in ref and str(market.get("specifier", "")) != str(ref["specifier"]):
                    continue
                for outcome in market.get("outcomes", []) or []:
                    if str(outcome.get("id", "")) == str(ref["outcomeId"]):
                        matches.append((ev, market, outcome))
        if len(matches) != 1:
            errors.append(f"Selected reference {index} resolved to {len(matches)} outcomes")
        else:
            selections.append(_selection(*matches[0]))
    if len({s.key for s in selections}) != len(selections):
        errors.append("Duplicate selected references")
    now_ms = int(time.time() * 1000)
    starts = [as_int(ev.get("estimateStartTime")) or 0 for ev in outcomes if isinstance(ev, dict)]
    available = bool(starts) and all(st > now_ms for st in starts)
    site_odds = as_float(ticket.get("displayTotalOdds")) or None
    bets = ticket.get("bets") or []
    coupon = Coupon(
        code=str(data.get("shareCode", code)),
        ok=not errors,
        selections=selections,
        coupon_id=str(data.get("shareCode", code)),
        stake=as_float(ticket.get("totalStake")),
        created=None,
        provider=provider,
        site_total_odds=site_odds,
        available=available,
        mapping_errors=errors,
        error="; ".join(errors) if errors else None,
    )
    # stash sportybet-only context for the extract / compare engine
    coupon.bet_type = data.get("betType") or ticket.get("betType")
    coupon.selected_systems = [b.get("selectedSystems") for b in bets if isinstance(b, dict)]
    coupon.num_unavailable = len(data.get("unavailableOutcomes", []) or [])
    coupon.retrieval_completeness = "complete" if not errors and not coupon.num_unavailable else "incomplete"
    coupon.odds_context = {k: ticket[k] for k in ("displayTotalOdds", "betType", "bets", "potentialWinnings", "totalStake") if k in ticket}
    return coupon
