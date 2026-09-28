"""End-to-end: book a random game via the UI to get a seed booking code, then
scan mutated codes via the coupon API to discover higher-odds coupons.

Run the whole flow:      pytest tests/test_book_and_scan.py -s
Skip UI, scan a code:    pytest tests/test_book_and_scan.py::test_scan_from_seed -s --seed-code=5S2HFVg
"""
import pytest

from pages.sports_page import SportsPage
from pages.betslip_page import BetslipPage


@pytest.mark.e2e
def test_book_random_then_scan(page, settings, scanner, logger):
    sports = SportsPage(page, settings, logger)
    sports.open().open_soccer().open_league()
    pick = sports.pick_random_odd()
    assert pick["eid"], "expected to pick a fixture"

    betslip = BetslipPage(page, settings, logger)
    seed_code = betslip.book_a_bet()
    assert seed_code, "expected a booking code from the modal"
    betslip.close_modal().clear_betslip()

    result = scanner.scan(seed_code)
    scanner.save(result)

    # The seed itself is a valid coupon; the scan should have tried variants.
    assert result.tried >= 0
    best = result.best()
    if best:
        logger.success(f"Best coupon {best.code} @ total odds {best.total_odds}")


@pytest.mark.scan
def test_scan_from_seed(request, settings, scanner, logger):
    """API-only scan from an existing code (no browser).

    Seed resolution: --seed-code=<code> wins, else SEED_CODE from .env.
    """
    seed = request.config.getoption("--seed-code") or settings.seed_code
    if not seed:
        pytest.skip("provide a code via --seed-code=<code> or SEED_CODE in .env")
    result = scanner.scan(seed)
    scanner.save(result)
    assert result.found_ok >= 1, "seed code did not resolve to any valid coupon"
