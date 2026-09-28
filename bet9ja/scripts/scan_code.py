#!/usr/bin/env python3
"""Decode and/or scan a bet9ja booking code via the coupon API — no browser.

Usage:
  python -m scripts.scan_code 5S2HFVg                 # decode one code
  python -m scripts.scan_code 5S2HFVg --scan          # scan mutated variants
  python -m scripts.scan_code 5S2HFVg --scan --min-odds 20 --env prod
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # provider root

from infra.config.settings import Settings
from infra.clients.coupon_api import CouponApiClient
from engine.scanner import CouponScanner
from engine.logger import get_logger


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode / scan a bet9ja booking code")
    parser.add_argument("code", nargs="?", default=None,
                        help="the seed booking code (falls back to SEED_CODE in .env)")
    parser.add_argument("--scan", action="store_true", help="scan mutated variants of the code")
    parser.add_argument("--min-odds", type=float, default=None, help="override MIN_TOTAL_ODDS")
    parser.add_argument("--depth", type=int, default=None, help="override MAX_MUTATION_DEPTH (1-3)")
    parser.add_argument("--max-codes", type=int, default=None, help="override MAX_CODES_TO_TRY")
    parser.add_argument("--top", type=int, default=None, help="output only the top N by odds (0 = all)")
    parser.add_argument("--max-odds-cap", type=float, default=None, help="drop coupons above this odds (0 = no cap)")
    parser.add_argument("--min-legs", type=int, default=None, help="drop coupons with fewer legs (0 = no floor)")
    parser.add_argument("--max-legs", type=int, default=None, help="drop coupons with more legs (0 = no ceiling)")
    parser.add_argument("--env", default=None, help="env name -> .env.<name>")
    parser.add_argument("--max-qualifying", type=int, default=None,
                        help="stop after N qualifying coupons (0 = uncapped)")
    parser.add_argument("--run-dir", default=None,
                        help="write this run's extract/codes/report under PATH (self-contained run)")
    args = parser.parse_args()

    settings = Settings.load(args.env)
    code = args.code or settings.seed_code
    if not code:
        parser.error("no booking code given: pass one as an argument or set SEED_CODE in .env")
    if args.min_odds is not None:
        if not math.isfinite(args.min_odds):
            parser.error("--min-odds must be finite")
        settings.min_total_odds = args.min_odds
    if args.depth is not None:
        settings.max_mutation_depth = args.depth
    if args.max_codes is not None:
        settings.max_codes_to_try = args.max_codes
    if args.top is not None:
        settings.top_n = args.top
    if args.max_odds_cap is not None:
        if not math.isfinite(args.max_odds_cap):
            parser.error("--max-odds-cap must be finite")
        settings.max_total_odds = args.max_odds_cap
    if args.min_legs is not None:
        settings.min_legs = args.min_legs
    if args.max_legs is not None:
        settings.max_legs = args.max_legs
    if args.max_qualifying is not None:
        if args.max_qualifying < 0:
            parser.error("--max-qualifying must be >= 0 (0 = uncapped)")
        settings.max_qualifying = args.max_qualifying
    if args.run_dir is not None:
        run_dir = Path(args.run_dir)
        existing = sorted((run_dir / "extracts").glob("*.json"))
        if existing:
            parser.error(f"--run-dir {run_dir} already holds {len(existing)} extract(s); "
                         "use a fresh dir so Stage 2 globs exactly one scan")
        settings.results_dir = run_dir

    logger = get_logger("scan")
    api = CouponApiClient(settings, logger)

    coupon = api.decode(code)
    print(coupon)

    if not args.scan:
        return 0 if coupon.ok else 1

    if not coupon.ok:
        logger.warning("Seed code is not a valid coupon; scanning anyway.")
    scanner = CouponScanner(api, settings, logger)
    result = scanner.scan(code)
    scanner.save(result)

    ranked = sorted(result.qualifying, key=lambda x: x.parsed_leg_product, reverse=True)
    if settings.top_n and settings.top_n > 0:
        ranked = ranked[:settings.top_n]
    shown = f"top {len(ranked)}" if settings.top_n else f"{len(ranked)}"
    print(f"\n=== {shown} coupon(s) with recorded leg-product odds >= {settings.min_total_odds}  (code | odds | legs) ===")
    print("     odds = recorded leg-product odds (product of leg prices), NOT verified payout")
    for c in ranked:
        print(f"  {c.code} | {c.parsed_leg_product:,.2f} | {c.num_legs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
