"""Pytest fixtures: settings, coupon API/scanner, and a Playwright page.

Environment values come from Settings (env-separated); code/test logic never
reads os.environ directly.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from infra.config.settings import Settings           # noqa: E402
from infra.clients.coupon_api import CouponApiClient  # noqa: E402
from engine.scanner import CouponScanner               # noqa: E402
from engine.logger import get_logger                   # noqa: E402


def pytest_addoption(parser):
    parser.addoption("--env", action="store", default=None, help="env name -> .env.<name>")
    parser.addoption("--min-odds", action="store", default=None, type=float,
                     help="override MIN_TOTAL_ODDS for the scan")
    parser.addoption("--depth", action="store", default=None, type=int,
                     help="mutation depth: 1 = edit last char, 2 = last two, 3 = last three "
                          "(progressive). Overrides MAX_MUTATION_DEPTH.")
    parser.addoption("--max-codes", action="store", default=None, type=int,
                     help="override MAX_CODES_TO_TRY (API-call cap per scan)")
    parser.addoption("--seed-code", action="store", default=None,
                     help="skip UI booking and scan from this existing code")


@pytest.fixture(scope="session")
def settings(request) -> Settings:
    cfg = Settings.load(request.config.getoption("--env"))
    min_odds = request.config.getoption("--min-odds")
    if min_odds is not None:
        cfg.min_total_odds = min_odds
    depth = request.config.getoption("--depth")
    if depth is not None:
        cfg.max_mutation_depth = depth
    max_codes = request.config.getoption("--max-codes")
    if max_codes is not None:
        cfg.max_codes_to_try = max_codes
    return cfg


@pytest.fixture(scope="session")
def logger():
    return get_logger("bet9ja")


@pytest.fixture(scope="session")
def coupon_api(settings) -> CouponApiClient:
    return CouponApiClient(settings)


@pytest.fixture(scope="session")
def scanner(coupon_api, settings) -> CouponScanner:
    return CouponScanner(coupon_api, settings)


@pytest.fixture()
def page(settings):
    """A Playwright page honoring the configured headless flag."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        _launch = {"headless": settings.headless,
                   "args": ["--disable-blink-features=AutomationControlled"]}
        if getattr(settings, "browser_channel", ""):
            _launch["channel"] = settings.browser_channel
        browser = pw.chromium.launch(**_launch)
        context = browser.new_context(
            user_agent=settings.user_agent,
            locale=getattr(settings, "locale", "en-NG"),
            timezone_id=getattr(settings, "timezone_id", "Africa/Lagos"),
        )
        pg = context.new_page()
        try:
            yield pg
        finally:
            context.close()
            browser.close()
