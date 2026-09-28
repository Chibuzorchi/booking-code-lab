"""Exposure-constrained subset selection.

Turns an overlapping candidate pool into a de-correlated shortlist of booking
codes: choose as many codes as possible such that no single selection identity
appears in more than ``max_exposure`` selected codes. That cap bounds the blast
radius of any one game — if it loses, at most ``max_exposure`` staked codes are
affected.

The selector is deterministic and reproducible. It never books, stakes, or ranks
by probability or expected value; it only limits shared-selection exposure across
the codes it hands back. Identity keys already embed the provider and cross-provider
identities never collide, so the exposure cap is enforced independently per provider
even when a pool mixes both.
"""
from __future__ import annotations

from collections import defaultdict

from .analysis import canonical


def _bare_code(ticket_id):
    """'bet9ja:5SH7HBJ' -> '5SH7HBJ' (the string you paste into the app)."""
    return ticket_id.split(":", 1)[1] if ":" in ticket_id else ticket_id


# Uniqueness tiers, by how many of a code's legs are shared with other selected codes.
_TIERS = [(0, 0, "fully independent — no shared games"),
          (1, 2, "1-2 shared games"),
          (3, 5, "3-5 shared games"),
          (6, None, "6+ shared games")]


def _tier(shared_count):
    for rank, (lo, hi, label) in enumerate(_TIERS):
        if shared_count >= lo and (hi is None or shared_count <= hi):
            return rank, label
    return len(_TIERS) - 1, _TIERS[-1][2]


def _odds_range(rows):
    values = sorted(r["parsed_leg_product"] for r in rows if r["parsed_leg_product"] is not None)
    if not values:
        return {"basis": "parsed_leg_product", "min": None, "max": None, "median": None,
                "priced_codes": 0, "unpriced_codes": len(rows)}
    mid = values[len(values) // 2] if len(values) % 2 else (values[len(values)//2 - 1] + values[len(values)//2]) / 2
    return {"basis": "parsed_leg_product", "min": values[0], "max": values[-1], "median": mid,
            "priced_codes": len(values), "unpriced_codes": len(rows) - len(values)}


def select_subset(tickets, max_exposure, target=None):
    """Greedily select codes under a per-selection exposure cap.

    tickets: classifiable ticket dicts (id, provider, num_legs, odds, legs[{key,...}]).
    max_exposure: integer >= 1; the most selected codes any one selection may span.
    target: optional integer >= 1; stop once this many codes are selected.

    Ordering admits the most independent tickets first — ascending by the pool
    popularity of a ticket's most-shared leg, then total leg popularity, then leg
    count, then id — so that scarce shared legs are spent on as few codes as
    possible and the retained set stays maximally de-correlated. This yields a
    maximal (not provably maximum) feasible set; the choice rule is stated so the
    output is explainable and reproducible.
    """
    if not isinstance(max_exposure, int) or isinstance(max_exposure, bool) or max_exposure < 1:
        raise ValueError("max_exposure must be an integer >= 1")
    if target is not None and (not isinstance(target, int) or isinstance(target, bool) or target < 1):
        raise ValueError("target must be an integer >= 1 when given")

    # Pool-wide popularity of each selection identity, plus a display label per key.
    pool_freq = defaultdict(int)
    meta = {}
    keyed = []
    for ticket in tickets:
        keys = [canonical(leg["key"]) for leg in ticket["legs"]]
        keyed.append((ticket, keys))
        for leg, key in zip(ticket["legs"], keys):
            pool_freq[key] += 1
            meta.setdefault(key, {"event": leg["event"], "market": leg["market"], "pick": leg["pick"]})

    def order_key(item):
        ticket, keys = item
        peak = max((pool_freq[k] for k in keys), default=0)
        total = sum(pool_freq[k] for k in keys)
        return (peak, total, ticket["num_legs"], ticket["id"])

    ordered = sorted(keyed, key=order_key)

    exposure = defaultdict(int)          # selected-so-far count per key
    holders = defaultdict(list)          # key -> selected code ids holding it
    selected, rejected = [], []
    for ticket, keys in ordered:
        if target is not None and len(selected) >= target:
            rejected.append({"id": ticket["id"], "code": _bare_code(ticket["id"]),
                             "provider": ticket["provider"], "reason": "target_reached",
                             "blocking": []})
            continue
        blocking = [k for k in keys if exposure[k] + 1 > max_exposure]
        if blocking:
            rejected.append({
                "id": ticket["id"], "code": _bare_code(ticket["id"]),
                "provider": ticket["provider"], "reason": "exposure_cap",
                "blocking": [{**meta[k], "at_capacity_with": list(holders[k])} for k in blocking]})
            continue
        for key in keys:
            exposure[key] += 1
            holders[key].append(ticket["id"])
        selected.append((ticket, keys))

    selected_ids = {t["id"] for t, _ in selected}
    selected_rows, realized_max = [], 0
    for ticket, keys in selected:
        shared = []
        for leg, key in zip(ticket["legs"], keys):
            others = [cid for cid in holders[key] if cid != ticket["id"]]
            realized_max = max(realized_max, len(holders[key]))
            if others:
                shared.append({**meta[key], "shared_with_selected": sorted(others)})
        odds = ticket["odds"]
        selected_rows.append({
            "id": ticket["id"], "code": _bare_code(ticket["id"]), "provider": ticket["provider"],
            "num_legs": ticket["num_legs"],
            "parsed_leg_product": odds["parsed_leg_product"],
            "site_displayed_odds": odds["site_displayed_odds"],
            "payout_status": odds["payout_semantics"]["status"],
            "shared_leg_count": len(shared),
            "shared_legs": sorted(shared, key=canonical)})
    selected_rows.sort(key=lambda r: r["id"])
    rejected.sort(key=lambda r: (r["reason"], r["id"]))

    by_provider = defaultdict(lambda: {"pool": 0, "selected": 0})
    for ticket in tickets:
        by_provider[ticket["provider"]]["pool"] += 1
    for row in selected_rows:
        by_provider[row["provider"]]["selected"] += 1

    # Classify the output by uniqueness: which codes are fully independent, which
    # carry 1-2 shared games, etc. Each bucket reports its own odds range.
    bucketed = defaultdict(list)
    for row in selected_rows:
        rank, tier_label = _tier(row["shared_leg_count"])
        row["uniqueness_tier"] = tier_label
        bucketed[(rank, tier_label)].append(row)
    buckets = [{"tier": tier_label, "code_count": len(rows),
                "codes": [r["code"] for r in rows], "odds_range": _odds_range(rows)}
               for (rank, tier_label), rows in sorted(bucketed.items())]

    return {
        "max_exposure": max_exposure,
        "target": target,
        "objective": "maximize retained codes subject to per-selection exposure cap",
        "order_rule": "ascending (peak leg pool-popularity, total leg pool-popularity, num_legs, id); most independent first",
        "pool_size": len(tickets),
        "selected_count": len(selected_rows),
        "rejected_count": len(rejected),
        "realized_max_exposure": realized_max,
        "odds_range": _odds_range(selected_rows),
        "uniqueness_buckets": buckets,
        "by_provider": {k: dict(v) for k, v in sorted(by_provider.items())},
        "selected_codes": [row["code"] for row in selected_rows],
        "selected": selected_rows,
        "rejected": rejected,
        "caveats": [
            "Codes describe candidate bookings, not placed stakes, probabilities, or expected returns.",
            "The exposure cap bounds shared-selection blast radius; it is not a probability of winning.",
            "Selection maximizes retained codes under the cap by a stated greedy rule, not a proven optimum.",
            "Payout semantics remain unknown; odds shown are the parsed leg product unless a site value was captured.",
            "Historical extracts do not establish current bookability or decision-time prices.",
        ],
    }
