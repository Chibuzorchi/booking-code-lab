#!/usr/bin/env python3
"""Decode and/or scan a sportybet share/booking code via the share API — no browser.

Usage:
  python -m scripts.scan_code WXP8AY                     # decode one code
  python -m scripts.scan_code WXP8AY --raw              # dump raw JSON
  python -m scripts.scan_code WXP8AY --scan             # scan base36 variants
  python -m scripts.scan_code WXP8AY --scan --depth 2 --min-odds 5000
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # provider root

from engine.logger import get_logger
from engine.scanner import CouponScanner
from infra.clients.share_api import ShareApiClient
from infra.config.settings import Settings


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode / scan a sportybet share code")
    ap.add_argument("code", nargs="?", default=None,
                    help="the share/booking code (falls back to SEED_CODE in .env)")
    ap.add_argument("--raw", action="store_true", help="dump the raw JSON payload")
    ap.add_argument("--scan", action="store_true", help="scan base36 variants of the code")
    ap.add_argument("--min-odds", type=float, default=None, help="override MIN_TOTAL_ODDS")
    ap.add_argument("--depth", type=int, default=None, help="override MAX_MUTATION_DEPTH (1-3)")
    ap.add_argument("--max-codes", type=int, default=None, help="override MAX_CODES_TO_TRY")
    ap.add_argument("--top", type=int, default=None, help="output only the top N by odds (0 = all)")
    ap.add_argument("--max-odds-cap", type=float, default=None, help="drop coupons above this odds (0 = no cap)")
    ap.add_argument("--min-legs", type=int, default=None, help="drop coupons with fewer legs (0 = no floor)")
    ap.add_argument("--max-legs", type=int, default=None, help="drop coupons with more legs (0 = no ceiling)")
    ap.add_argument("--env", default=None, help="env name -> .env.<name>")
    ap.add_argument("--max-qualifying", type=int, default=None,
                    help="stop after N qualifying coupons (0 = uncapped)")
    ap.add_argument("--run-dir", default=None,
                    help="write this run's extract/codes/report under PATH (self-contained run)")
    args = ap.parse_args()

    log = get_logger("sportybet")
    settings = Settings.load(args.env)
    code = args.code or settings.seed_code
    if not code:
        log.error("no code given and SEED_CODE is empty in .env")
        return 2
    if args.min_odds is not None:
        settings.min_total_odds = args.min_odds
    if args.top is not None:
        settings.top_n = args.top
    if args.max_odds_cap is not None:
        settings.max_total_odds = args.max_odds_cap
    if args.min_legs is not None:
        settings.min_legs = args.min_legs
    if args.max_legs is not None:
        settings.max_legs = args.max_legs
    if args.depth is not None:
        settings.max_mutation_depth = args.depth
    if args.max_codes is not None:
        settings.max_codes_to_try = args.max_codes
    if args.max_qualifying is not None:
        if args.max_qualifying < 0:
            ap.error("--max-qualifying must be >= 0 (0 = uncapped)")
        settings.max_qualifying = args.max_qualifying
    if args.run_dir is not None:
        run_dir = Path(args.run_dir)
        existing = sorted((run_dir / "extracts").glob("*.json"))
        if existing:
            ap.error(f"--run-dir {run_dir} already holds {len(existing)} extract(s); "
                     "use a fresh dir so Stage 2 globs exactly one scan")
        settings.results_dir = run_dir

    client = ShareApiClient(settings, log)
    if args.raw:
        print(json.dumps(client.fetch_raw(code), indent=2, ensure_ascii=False))
        return 0

    coupon = client.decode(code)
    print(coupon)
    if not args.scan:
        return 0 if coupon.ok else 1

    scanner = CouponScanner(client, settings, log)
    result = scanner.scan(code)
    scanner.save(result)
    ranked = sorted(result.qualifying, key=lambda x: x.total_odds, reverse=True)
    if settings.top_n and settings.top_n > 0:
        ranked = ranked[:settings.top_n]
    shown = f"top {len(ranked)}" if settings.top_n else f"{len(ranked)}"
    print(f"\n=== {shown} coupon(s) with total odds >= {settings.min_total_odds}  (code | odds | legs) ===")
    for c in ranked:
        print(f"  {c.code} | {c.total_odds:,.2f} | {c.num_legs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
