"""Phase 1 — the uniqueness filter (leg-level).

Collapse a harvested pool of booking codes down to the codes that are genuinely
distinct when you *open them and read the games*. Two codes are duplicates the
moment they share a single game+option (selection_key); a code is distinct only
when none of its legs appears in any other code.

This is Firefly's *resource-level* comparison, not its whole-state hash. Firefly
compares individual cloud resources across states by a composite key to see what
actually overlaps (inventoryV3Service), rather than hashing the entire state as
one blob (iaCStacksService). Here the "resource" is one leg (game+option) and its
composite key is `selection_key`. We match legs across codes, not tickets as
blobs — so a code sharing 15 of 20 legs is correctly seen as overlapping.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .analysis import label, read_json


def selection_key(selection: dict) -> str:
    """Composite key of one leg: game + market + pick. Prefer the provider's own
    selection_key, else compose it from the event/market/outcome ids."""
    key = label(selection.get("selection_key"))
    if key:
        return key
    parts = [str(selection.get(k)) for k in ("native_event_id", "market_id", "outcome_id")]
    return "$".join(p for p in parts if p and p != "None")


def event_key(selection: dict) -> str:
    """Identity of the game itself, ignoring which market/pick was taken."""
    return str(selection.get("native_event_id") or selection.get("event_id")
               or label(selection.get("event")) or "")


def load_coupons(paths):
    """All qualifying coupons across the given extract files (order preserved)."""
    coupons = []
    for path in sorted(paths):
        try:
            data = read_json(path)
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("qualifying"), list):
            continue
        for raw in data["qualifying"]:
            if isinstance(raw, dict) and isinstance(raw.get("selections"), list) and raw.get("code"):
                coupons.append(raw)
    return coupons


def leg_usage(coupons):
    """Counter: how many codes each leg (game+option) appears in."""
    use = Counter()
    for c in coupons:
        for lk in {selection_key(s) for s in c["selections"]}:
            use[lk] += 1
    return use


def analyze(coupons):
    """Phase-1 report at the leg level: which codes are genuinely distinct."""
    use = leg_usage(coupons)
    distinct = []          # codes whose every leg is unique to them
    overlapping = []       # codes that share >=1 leg with another code
    for c in coupons:
        legs = {selection_key(s) for s in c["selections"]}
        (distinct if all(use[lk] == 1 for lk in legs) else overlapping).append(c)

    # readable names + which game+option is reused the most
    name = {}
    for c in coupons:
        for s in c["selections"]:
            name.setdefault(selection_key(s),
                            f"{label(s.get('event'))} :: {label(s.get('market'))} = {label(s.get('pick'))}")

    return {
        "coupons": len(coupons),
        "distinct_legs": len(use),
        "shared_legs": sum(1 for v in use.values() if v > 1),
        "distinct_codes": len(distinct),
        "overlapping_codes": len(overlapping),
        "top_legs": [{"leg": name.get(lk, lk), "in_codes": n} for lk, n in use.most_common(10)],
        "distinct": distinct,
        "overlapping": overlapping,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1: leg-level distinctiveness of harvested codes")
    ap.add_argument("--results", required=True, help="provider results dir (reads extracts/*.json)")
    ap.add_argument("--out", default=None, help="write the fully-distinct codes here (default: results/distinct_tickets.json)")
    args = ap.parse_args()

    results = Path(args.results).resolve()
    paths = sorted((results / "extracts").glob("*.json"))
    if not paths:
        print(f"No extracts found under {results}/extracts")
        return 1
    r = analyze(load_coupons(paths))
    total, distinct = r["coupons"], r["distinct_codes"]

    print("PHASE 1 — UNIQUENESS (leg-level: game+option)")
    print("=" * 46)
    print(f"codes in pool                 : {total}")
    print(f"distinct legs (game+option)   : {r['distinct_legs']}")
    print(f"legs shared by >1 code        : {r['shared_legs']}")
    print(f"codes fully distinct (0 shared): {distinct}")
    print(f"codes overlapping (>=1 shared) : {r['overlapping_codes']} "
          f"({100 * r['overlapping_codes'] / total:.0f}%)")
    print()
    print("most-reused single game+option (leg -> # codes using it):")
    for g in r["top_legs"]:
        print(f"   {g['in_codes']:>4}  {g['leg']}")

    out = Path(args.out) if args.out else results / "distinct_tickets.json"
    payload = {"distinct_code_count": distinct,
               "codes": [label(c.get("code")) for c in r["distinct"]]}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {distinct} fully-distinct codes -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
