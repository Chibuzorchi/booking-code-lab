"""User-supplied positions, distinct from candidates and provider settlement."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .analysis import canonical, normalize_ticket


def money(value, field):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {field}")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {field}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"Invalid {field}")
    return amount


def summarize_positions(document, candidates):
    if not isinstance(document, dict) or set(document) != {"positions"} or not isinstance(document["positions"], list):
        raise ValueError("Position input must contain a positions array")
    seen, valid, invalid = set(), [], []
    totals = defaultdict(lambda: {"placed_stakes": Decimal(0), "outstanding_stakes": Decimal(0),
                                  "settled_stakes": Decimal(0), "reported_payouts": Decimal(0)})
    for record in document["positions"]:
        if not isinstance(record, dict) or not isinstance(record.get("position_id"), str) or not record["position_id"]:
            raise ValueError("Each position requires a nonempty string position_id")
        pid = record["position_id"]
        if pid in seen:
            raise ValueError(f"Duplicate position_id: {pid}; repeated wagers need distinct IDs")
        seen.add(pid)
        status = record.get("status")
        if status not in ("selected", "placed", "settled"):
            raise ValueError(f"Invalid status for {pid}")
        currency = record.get("currency")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
            raise ValueError(f"Three-letter currency required for {pid}")
        currency = currency.upper()
        stake = money(record.get("stake"), f"stake for {pid}")
        if stake <= 0:
            raise ValueError(f"Positive stake required for {pid}")
        payout = money(record.get("payout"), f"payout for {pid}") if status == "settled" else None
        if status != "settled" and "payout" in record:
            raise ValueError(f"Payout is only valid for settled position {pid}")
        normalized, errors = normalize_ticket(record.get("ticket"), None)
        if status in ("placed", "settled"):
            totals[currency]["placed_stakes"] += stake
        if status == "placed":
            totals[currency]["outstanding_stakes"] += stake
        if status == "settled":
            totals[currency]["settled_stakes"] += stake
            totals[currency]["reported_payouts"] += payout
        if errors or normalized is None:
            invalid.append({"position_id": pid, "status": status, "stake": str(stake),
                            "currency": currency, "reasons": errors})
            continue
        valid.append({"position_id": pid, "status": status, "stake": stake, "currency": currency,
                      "ticket": normalized, "profile": record.get("profile")})

    selections, events = defaultdict(list), defaultdict(list)
    for position in valid:
        if position["status"] != "placed":
            continue
        for key in {canonical(leg["key"]) for leg in position["ticket"]["legs"]}:
            selections[key].append(position)
        for key in {canonical(leg["event_key"]) for leg in position["ticket"]["legs"]}:
            events[key].append(position)

    def exposure(index):
        import json
        rows = []
        for key, positions in sorted(index.items()):
            amounts = defaultdict(Decimal)
            for position in positions:
                amounts[position["currency"]] += position["stake"]
            rows.append({"key": json.loads(key), "position_ids": sorted(p["position_id"] for p in positions),
                         "stake_by_currency": {c: str(v) for c, v in sorted(amounts.items())}})
        return rows

    candidate_links = []
    for candidate in candidates:
        linked = {p["position_id"] for leg in candidate["legs"]
                  for p in selections.get(canonical(leg["key"]), [])}
        event_linked = {p["position_id"] for leg in candidate["legs"]
                        for p in events.get(canonical(leg["event_key"]), [])}
        if linked or event_linked:
            candidate_links.append({"id": candidate["id"], "shared_selection_positions": sorted(linked),
                                    "same_event_positions": sorted(event_linked)})
    return {
        "basis": "user_supplied_unverified_positions",
        "warnings": ["Exposure uses provisional identities and includes only supplied positions.",
                     "Selection/event stake exposures overlap; never sum rows as independent losses.",
                     "Exposure is stake attached to selections, not a modeled loss for system tickets.",
                     "Reported settled P&L uses user-entered payouts; no provider settlement is inferred."],
        "position_count": len(seen), "unclassified_positions": invalid,
        "selected_positions": [p["position_id"] for p in valid if p["status"] == "selected"],
        "outstanding_positions": [p["position_id"] for p in valid if p["status"] == "placed"],
        "totals_by_currency": {currency: {**{k: str(v) for k, v in values.items()},
                                             "settled_net_pnl": str(values["reported_payouts"] - values["settled_stakes"])}
                               for currency, values in sorted(totals.items())},
        "selection_exposure": exposure(selections), "event_exposure": exposure(events),
        "candidate_links": sorted(candidate_links, key=lambda r: r["id"]),
    }
