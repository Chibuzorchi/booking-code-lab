"""Phase 1 — the uniqueness filter (leg-level), on the shared eligible pool.

Collapse a harvested pool of booking codes down to the codes that are genuinely
distinct when you *open them and read the games*. Two codes are duplicates the
moment they share a single game+option; a code is distinct only when none of its
legs appears in any other code.

Stage 2 (this module) and Stage 3 (exposure de-correlation) consume the SAME
eligible pool from the SAME builder — decorrelation.build_pool — with the same
explicit extract, provider, and odds policy (basis + band) and the same duplicate
handling (conflicting observations of one code raise, not silently diverge). The
leg identity is contracts.identity via normalize_ticket's legs[].key. So the two
stages can never disagree about the pool or about whether two codes share a leg.

Relationship to Stage 3's output: the fully-distinct set is exactly the eligible
codes whose every leg is unique across the WHOLE eligible pool. On that same pool,
with no target limit, distinct ⊆ selected at max_exposure=1; equality is possible.
With a target limit, inclusion is not guaranteed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .analysis import canonical, label
from .decorrelation import build_pool


def leg_key_set(ticket: dict) -> set:
    """The leg-identity keys of a normalized ticket (contracts.identity)."""
    return {canonical(leg["key"]) for leg in ticket["legs"]}


def _bare_code(ticket: dict) -> str:
    """The booking code as pasted into the app (from the preserved raw coupon)."""
    raw = ticket.get("raw") or {}
    if raw.get("code"):
        return label(raw["code"])
    tid = ticket.get("id", "")
    return tid.split(":", 1)[1] if ":" in tid else tid


def analyze(tickets):
    """Phase-1 report at the leg level over a shared eligible pool.

    tickets: normalized tickets from build_pool (each carries legs[].key and raw).
    """
    use = Counter()
    for t in tickets:
        for lk in leg_key_set(t):
            use[lk] += 1

    distinct, overlapping = [], []        # distinct: every leg unique across the pool
    for t in tickets:
        legs = leg_key_set(t)
        (distinct if all(use[lk] == 1 for lk in legs) else overlapping).append(t)

    name = {}
    for t in tickets:
        for leg in t["legs"]:
            name.setdefault(canonical(leg["key"]),
                            f"{label(leg.get('event'))} :: {label(leg.get('market'))} = {label(leg.get('pick'))}")

    return {
        "coupons": len(tickets),
        "distinct_legs": len(use),
        "shared_legs": sum(1 for v in use.values() if v > 1),
        "distinct_codes": len(distinct),
        "overlapping_codes": len(overlapping),
        "top_legs": [{"leg": name.get(lk, lk), "in_codes": n} for lk, n in use.most_common(10)],
        "distinct": distinct,
        "overlapping": overlapping,
    }


def analyze_extract(extract_path, provider, *, odds_basis, min_odds, max_odds=None):
    """Stage-2 report for one run's extract, on the shared eligible pool.

    Uses decorrelation.build_pool so the pool (provider match, odds band, duplicate
    handling) is byte-for-byte what Stage 3 selects from. Returns the pool metadata
    (input_count, pool_size, excluded, policy) merged with the distinctness report.
    """
    pool = build_pool(extract_path, provider, odds_basis=odds_basis,
                      min_odds=min_odds, max_odds=max_odds)
    report = analyze(pool["tickets"])
    return {"extract_path": pool["extract_path"], "provider": pool["provider"],
            "odds_basis": pool["odds_basis"], "min_odds": pool["min_odds"],
            "max_odds": pool["max_odds"], "input_count": pool["input_count"],
            "pool_size": pool["pool_size"], "excluded": pool["excluded"], **report}


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1: leg-level distinctiveness on the shared eligible pool")
    ap.add_argument("--extract", required=True, help="one run's extract JSON (results/runs/<id>/extracts/*.json)")
    ap.add_argument("--provider", required=True, choices=["bet9ja", "sportybet"])
    ap.add_argument("--odds-basis", required=True,
                    choices=["parsed_leg_product", "site_displayed_odds", "verified_payout"])
    ap.add_argument("--min-odds", type=float, required=True, help="lower band (>= 1)")
    ap.add_argument("--max-odds", type=float, default=None, help="upper band (optional)")
    ap.add_argument("--out", default=None, help="write the fully-distinct codes here")
    args = ap.parse_args()

    r = analyze_extract(args.extract, args.provider, odds_basis=args.odds_basis,
                        min_odds=args.min_odds, max_odds=args.max_odds)
    total, distinct = r["coupons"], r["distinct_codes"]

    print("PHASE 1 — UNIQUENESS (leg-level: game+option, shared eligible pool)")
    print("=" * 46)
    print(f"input coupons in extract      : {r['input_count']}")
    print(f"eligible pool (band+provider) : {total}  (excluded {len(r['excluded'])})")
    print(f"distinct legs (game+option)   : {r['distinct_legs']}")
    print(f"legs shared by >1 code        : {r['shared_legs']}")
    print(f"codes fully distinct (0 shared): {distinct}")
    if total:
        print(f"codes overlapping (>=1 shared) : {r['overlapping_codes']} "
              f"({100 * r['overlapping_codes'] / total:.0f}%)")
    print()
    print("most-reused single game+option (leg -> # codes using it):")
    for g in r["top_legs"]:
        print(f"   {g['in_codes']:>4}  {g['leg']}")

    if args.out:
        payload = {"distinct_code_count": distinct,
                   "codes": [_bare_code(t) for t in r["distinct"]]}
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {distinct} fully-distinct codes -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
