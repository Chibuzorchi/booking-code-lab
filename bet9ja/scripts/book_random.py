#!/usr/bin/env python3
"""Book a random soccer game via the browser and print the booking code.

Usage:
  python -m scripts.book_random                 # headless, default league
  HEADLESS=false python -m scripts.book_random  # watch it run
  python -m scripts.book_random --scan          # book then scan the seed
"""
import argparse
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # provider root

from infra.config.settings import Settings
from infra.clients.coupon_api import CouponApiClient
from engine.scanner import CouponScanner
from engine.logger import get_logger
from scripts.booking import BookingError, book_one



def refresh_report(provider: str, results_dir, logger) -> None:
    """Rebuild the browser report so newly booked codes show up in the UI."""
    import subprocess
    analysis_root = Path(__file__).resolve().parents[2] / "ticket-analysis"
    try:
        subprocess.run(
            [sys.executable, "-m", "portfolio.reporting", "--provider", provider,
             "--results", str(results_dir.resolve())],
            cwd=analysis_root, check=True, capture_output=True, text=True, timeout=60,
        )
        logger.info(f"report refreshed -> {results_dir}/index.html")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"code saved, but report refresh failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Book a random bet9ja game")
    parser.add_argument("--scan", action="store_true", help="scan the seed code after booking")
    parser.add_argument("--env", default=None, help="env name -> .env.<name>")
    args = parser.parse_args()

    settings = Settings.load(args.env)
    logger = get_logger("book")

    # Same browser flow the fresh-seed runs use (scripts.book_and_scan), so a
    # code booked here and a code booked by the console come from one path.
    try:
        code = book_one(settings, logger)
    except BookingError as exc:
        logger.error(f"booking failed: {exc}")
        return 1

    print(f"\nBOOKING CODE: {code}")

    # Persist the code so it appears in the browser report (source of truth = booked/).
    out_dir = settings.results_dir / "booked"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    record = [{"order": 1, "code": code, "ts": int(time.time() * 1000)}]
    (out_dir / f"booked_{stamp}.json").write_text(json.dumps(record, indent=2))
    (out_dir / f"booked_{stamp}.txt").write_text(f"{code}\n")
    logger.info(f"saved code -> {out_dir}/booked_{stamp}.json")
    refresh_report("bet9ja", settings.results_dir, logger)

    if args.scan:
        api = CouponApiClient(settings, logger)
        scanner = CouponScanner(api, settings, logger)
        result = scanner.scan(code)
        scanner.save(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
