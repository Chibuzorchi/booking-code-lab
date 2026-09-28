#!/usr/bin/env python3
"""Book N fresh sportybet games via the UI, save the codes (with booking order
and timestamp), then analyse how sportybet assigns booking codes.

Usage:
  python -m scripts.book_random --count 18            # book 18, headless
  python -m scripts.book_random --count 18 --show     # watch the browser
  python -m scripts.book_random --analyse results/booked/<file>.json  # re-analyse only
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.logger import get_logger
from infra.config.settings import Settings


def refresh_report(provider: str, results_dir: Path, log) -> None:
    """Rebuild the browser report so newly booked codes show up in the UI."""
    import subprocess
    analysis_root = Path(__file__).resolve().parents[2] / "ticket-analysis"
    try:
        subprocess.run(
            [sys.executable, "-m", "portfolio.reporting", "--provider", provider,
             "--results", str(results_dir.resolve())],
            cwd=analysis_root, check=True, capture_output=True, text=True, timeout=60,
        )
        log.info(f"report refreshed -> {results_dir}/index.html")
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning(f"codes saved, but report refresh failed: {exc}")


def book_batch(settings, log, count: int, show: bool) -> list[dict]:
    from playwright.sync_api import sync_playwright
    from pages.football_page import FootballPage
    from pages.betslip_page import BetslipPage

    booked: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        ctx = browser.new_context(
            user_agent=settings.user_agent,
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()
        football = FootballPage(page, settings, log)
        slip = BetslipPage(page, settings, log)
        football.open()

        for i in range(count):
            try:
                if not football.pick_random_outcome():
                    log.warning(f"[{i+1}] could not select an outcome; retrying page")
                    football.open()
                    continue
                code = slip.book_bet()
                ts = int(time.time() * 1000)
                if code and code not in {b["code"] for b in booked}:
                    booked.append({"order": len(booked) + 1, "code": code, "ts": ts})
                    log.success(f"[{i+1}] booked {code}")
                elif not code:
                    log.warning(f"[{i+1}] booked but no code parsed")
                slip.close_modal()
                # no clear: each Book Bet mints a fresh code; we just keep adding
                # a selection so the slip (and thus the coupon) changes each round.
                time.sleep(0.5)
            except Exception as err:
                log.error(f"[{i+1}] booking failed: {err}")
                try:
                    football.open()
                except Exception:
                    break
        browser.close()
    return booked


def analyse(settings, log, booked: list[dict]) -> None:
    import string
    from infra.clients.share_api import ShareApiClient

    codes = [b["code"] for b in booked]
    if not codes:
        log.error("no codes to analyse")
        return

    print("\n================ CODE ASSIGNMENT ANALYSIS ================")
    print(f"codes ({len(codes)}), in booking order:")
    for b in booked:
        print(f"  #{b['order']:2}  {b['code']}")

    # 1) length
    lengths = {len(c) for c in codes}
    print(f"\nlength(s): {sorted(lengths)}")

    # 2) common prefix
    prefix = ""
    for chars in zip(*codes):
        if len(set(chars)) == 1:
            prefix += chars[0]
        else:
            break
    print(f"longest shared prefix: {prefix!r} ({len(prefix)} chars)")

    # 3) per-position variability (only meaningful for equal length)
    if len(lengths) == 1:
        L = lengths.pop()
        print("per-position distinct values:")
        for pos in range(L):
            vals = sorted({c[pos] for c in codes})
            print(f"  pos {pos}: {len(vals):2} distinct -> {''.join(vals)}")

    # 4) ordering: map each code base36 and see if it tracks booking time
    B36 = string.digits + string.ascii_uppercase
    def to_int(c):
        n = 0
        for ch in c:
            if ch not in B36:
                return None
            n = n * 36 + B36.index(ch)
        return n
    ints = [(b["order"], b["ts"], b["code"], to_int(b["code"])) for b in booked]
    print("\nbase36 value vs booking order (does a later booking = larger code?):")
    prev = None
    mono_up = mono_down = True
    for order, ts, code, val in ints:
        arrow = ""
        if prev is not None and val is not None:
            if val > prev: arrow = "up"
            elif val < prev: arrow = "down"
            else: arrow = "same"
            if val < prev: mono_up = False
            if val > prev: mono_down = False
        print(f"  #{order:2} {code}  = {val}  {arrow}")
        prev = val
    print(f"monotonic increasing: {mono_up} | decreasing: {mono_down}")

    # 5) confirm freshness via API
    print("\nfreshness check (API):")
    cl = ShareApiClient(settings, log)
    now = int(time.time() * 1000)
    for b in booked:
        raw = cl.fetch_raw(b["code"])
        evs = (raw or {}).get("data", {}).get("outcomes", []) or []
        fut = sum(1 for e in evs if (e.get("estimateStartTime") or 0) > now)
        print(f"  {b['code']}: legs={len(evs)} future={fut}")
    print("=========================================================")


def main() -> int:
    ap = argparse.ArgumentParser(description="Book fresh sportybet codes and analyse assignment")
    ap.add_argument("--count", type=int, default=18, help="how many codes to book")
    ap.add_argument("--show", action="store_true", help="run headed (watch the browser)")
    ap.add_argument("--analyse", default=None, help="skip booking; analyse a saved booked-*.json")
    ap.add_argument("--env", default=None, help="env name -> .env.<name>")
    args = ap.parse_args()

    log = get_logger("booker")
    settings = Settings.load(args.env)

    if args.analyse:
        booked = json.loads(Path(args.analyse).read_text())
        analyse(settings, log, booked)
        return 0

    booked = book_batch(settings, log, args.count, args.show)
    if not booked:
        log.error("booked nothing — the UI flow needs a look (selectors / bot check)")
        return 1

    out_dir = settings.results_dir / "booked"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"booked_{stamp}.json").write_text(json.dumps(booked, indent=2))
    (out_dir / f"booked_{stamp}.txt").write_text("".join(f"{b['code']}\n" for b in booked))
    log.info(f"saved {len(booked)} codes -> {out_dir}/booked_{stamp}.json")
    refresh_report("sportybet", settings.results_dir, log)

    analyse(settings, log, booked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
