#!/usr/bin/env python3
"""Book a fresh seed code in the browser, then scan from it — one process.

This is the fresh-seed entrypoint the run registry launches (worker.py
BOOK_SCAN_MODULE). It takes every scan_code flag except the seed itself,
books one code, records it, and then hands straight over to scan_code.main()
so the scan phase behaves exactly as it does for a typed seed.

Usage:
  python -m scripts.book_and_scan --scan --run-dir results/runs/<run>
  python -m scripts.book_and_scan --scan --min-odds 5000 --max-codes 2000

Seed record: with --run-dir, <run-dir>/seed.json is written atomically before
the scan starts, and on failure too:
  {"schema_version": 1, "provider": ..., "run_id": ..., "seed": "AbC1234",
   "booked_at": "..."}                                        (booked)
  {"schema_version": 1, "provider": ..., "run_id": ..., "seed": null,
   "error": "...", "booked_at": "..."}                        (not booked)
The registry reads it to show the booked code mid-run and to report a booking
failure as something better than an exit code.

The twin of this file lives in the other provider's scripts/ directory:
providers cannot import each other (each inserts its own root on sys.path and
loads its own .env), so the flow is duplicated rather than shared.

Nothing here stakes money: it books a coupon, it does not place a bet.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # provider root

from infra.config.settings import Settings
from engine.logger import get_logger
from scripts.booking import BookingError, book_one

EXIT_BOOKING_FAILED = 2


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_seed_record(run_dir: Path, provider: str, seed, error=None) -> None:
    """Atomically record the booking outcome next to the run's other evidence."""
    record = {"schema_version": 1, "provider": provider, "run_id": run_dir.name,
              "seed": seed, "booked_at": _now()}
    if error is not None:
        record["error"] = str(error)[:500]
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / ".seed.json.tmp"
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(tmp, run_dir / "seed.json")
    except OSError as exc:                                       # pragma: no cover
        print(f"could not record the seed: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Book a fresh booking code, then scan from it")
    # Every scan_code flag except the positional code, forwarded untouched.
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--min-odds", type=float, default=None)
    parser.add_argument("--depth", type=int, default=None)
    parser.add_argument("--max-codes", type=int, default=None)
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--max-odds-cap", type=float, default=None)
    parser.add_argument("--min-legs", type=int, default=None)
    parser.add_argument("--max-legs", type=int, default=None)
    parser.add_argument("--env", default=None)
    parser.add_argument("--max-qualifying", type=int, default=None)
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args()

    settings = Settings.load(args.env)
    logger = get_logger("book-scan")
    run_dir = Path(args.run_dir) if args.run_dir else None

    # Booking owns a browser, so a cancellation during this phase has to unwind
    # the context managers instead of killing the process where it stands.
    # The default disposition is restored before the scan phase, which must
    # keep behaving exactly as it does for a typed seed.
    def stop(signum, frame):
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        code = book_one(settings, logger)
    except BookingError as exc:
        logger.error(f"fresh-seed booking failed: {exc}")
        if run_dir is not None:
            write_seed_record(run_dir, settings.provider, None, error=exc)
        return EXIT_BOOKING_FAILED
    except SystemExit:
        if run_dir is not None:
            write_seed_record(run_dir, settings.provider, None,
                              error="cancelled while booking a fresh seed")
        raise
    except Exception as exc:                                     # pragma: no cover
        logger.error(f"fresh-seed booking failed: {exc}")
        if run_dir is not None:
            write_seed_record(run_dir, settings.provider, None, error=exc)
        return EXIT_BOOKING_FAILED
    finally:
        signal.signal(signal.SIGTERM, previous)

    print(f"\nBOOKING CODE: {code}")
    if run_dir is not None:
        write_seed_record(run_dir, settings.provider, code)

    # Hand over to the ordinary scan path with the freshly booked seed.
    forwarded = [sys.argv[0], code]
    if args.scan:
        forwarded.append("--scan")
    for flag, value in (("--min-odds", args.min_odds), ("--depth", args.depth),
                        ("--max-codes", args.max_codes), ("--top", args.top),
                        ("--max-odds-cap", args.max_odds_cap),
                        ("--min-legs", args.min_legs), ("--max-legs", args.max_legs),
                        ("--env", args.env), ("--max-qualifying", args.max_qualifying),
                        ("--run-dir", args.run_dir)):
        if value is not None:
            forwarded += [flag, str(value)]

    from scripts.scan_code import main as scan_main
    sys.argv = forwarded
    return scan_main()


if __name__ == "__main__":
    raise SystemExit(main())
