#!/usr/bin/env python3
"""Book one random soccer game through the browser and return its code.

The browser is the only way to mint a fresh bet9ja booking code: the coupon
API decodes and scans existing codes but never creates one. Callers that want
a code to scan from (scripts.book_and_scan) and callers that just want a code
(scripts.book_random) share this one flow so both book the same way.

Nothing here stakes money. Booking a coupon reserves a code for a selection;
placing a bet is a separate, deliberate action on the site.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # provider root


class BookingError(RuntimeError):
    """The browser flow finished without producing a booking code."""


def book_one(settings, logger) -> str:
    """Open the site, pick one random priced outcome, book it, return the code.

    Raises BookingError when the flow completes without a code, so a caller
    never mistakes an empty string for a usable seed. Playwright and the page
    objects that import it are loaded HERE, not at module import time: a
    workspace without the browser stack must fail as a reported booking error
    at the moment booking is attempted, not as an ImportError that kills the
    worker before it can record why.
    """
    try:
        from playwright.sync_api import sync_playwright

        from pages.sports_page import SportsPage
        from pages.betslip_page import BetslipPage
    except ImportError as exc:                                   # pragma: no cover
        raise BookingError(
            "Playwright is not installed in this environment; fresh-seed booking "
            "needs it (pip install playwright && playwright install chromium)"
        ) from exc

    launch = {"headless": settings.headless,
              "args": ["--disable-blink-features=AutomationControlled"]}
    if getattr(settings, "browser_channel", ""):
        launch["channel"] = settings.browser_channel

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        context = browser.new_context(
            user_agent=settings.user_agent,
            locale=getattr(settings, "locale", "en-NG"),
            timezone_id=getattr(settings, "timezone_id", "Africa/Lagos"),
        )
        page = context.new_page()
        try:
            sports = SportsPage(page, settings, logger)
            sports.open().open_soccer().open_league()
            pick = sports.pick_random_odd()
            logger.info(f"Picked: {pick}")
            betslip = BetslipPage(page, settings, logger)
            code = betslip.book_a_bet()
            betslip.close_modal().clear_betslip()
        finally:
            context.close()
            browser.close()

    if not code or not str(code).strip():
        raise BookingError("the betslip produced no booking code")
    return str(code).strip()
