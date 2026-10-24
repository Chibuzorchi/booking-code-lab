#!/usr/bin/env python3
"""Book one random football outcome through the browser and return its code.

The share API decodes and scans existing codes but never mints one, so the
browser is the only source of a fresh sportybet booking code. Callers that
want a code to scan from (scripts.book_and_scan) use this flow.

Nothing here stakes money. Booking a coupon reserves a code for a selection;
placing a bet is a separate, deliberate action on the site.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # provider root

ATTEMPTS = 3


class BookingError(RuntimeError):
    """The browser flow finished without producing a booking code."""


def book_one(settings, logger) -> str:
    """Open football, pick one random priced outcome, book it, return the code.

    The listing re-renders constantly, so a failed pick reopens the page and
    tries again up to ATTEMPTS times before giving up. Raises BookingError
    rather than returning an empty string, so a caller never scans "".
    Playwright and the page objects that import it are loaded HERE, not at
    module import time: a workspace without the browser stack must fail as a
    reported booking error at the moment booking is attempted, not as an
    ImportError that kills the worker before it can record why.
    """
    try:
        from playwright.sync_api import sync_playwright

        from pages.football_page import FootballPage
        from pages.betslip_page import BetslipPage
    except ImportError as exc:                                   # pragma: no cover
        raise BookingError(
            "Playwright is not installed in this environment; fresh-seed booking "
            "needs it (pip install playwright && playwright install chromium)"
        ) from exc

    code = None
    problem = "no attempt completed"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=settings.headless)
        context = browser.new_context(
            user_agent=settings.user_agent,
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        try:
            football = FootballPage(page, settings, logger)
            slip = BetslipPage(page, settings, logger)
            football.open()
            for attempt in range(1, ATTEMPTS + 1):
                try:
                    if not football.pick_random_outcome():
                        problem = "no selectable outcome on the football listing"
                        logger.warning(f"[{attempt}/{ATTEMPTS}] {problem}; reopening")
                        football.open()
                        continue
                    code = slip.book_bet()
                    slip.close_modal()
                    if code and code.strip():
                        code = code.strip()
                        break
                    problem = "the betslip produced no booking code"
                    logger.warning(f"[{attempt}/{ATTEMPTS}] {problem}")
                except Exception as err:
                    problem = f"booking attempt failed: {err}"
                    logger.warning(f"[{attempt}/{ATTEMPTS}] {problem}")
                    try:
                        football.open()
                    except Exception:
                        break
            try:
                slip.clear_betslip()
            except Exception:
                logger.debug("betslip left as-is")
        finally:
            context.close()
            browser.close()

    if not code:
        raise BookingError(problem)
    return code
