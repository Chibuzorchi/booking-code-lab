"""Betslip page object: book a bet, read the booking code, manage the slip.

Covers the requested betslip flow — Book a Bet -> read code from the modal ->
close the modal -> clear the slip -> (optionally) load another booking code and
read total odds.
"""
from __future__ import annotations

from playwright.sync_api import Page, expect

from infra.config.settings import Settings
from pages.base_page import BasePage
from tests.data import Selectors


class BetslipPage(BasePage):
    def __init__(self, page: Page, settings: Settings, logger=None):
        super().__init__(page, settings, logger)
        self.sel = Selectors

    def book_a_bet(self) -> str:
        """Click 'Book a Bet', assert the modal, and return the booking code."""
        self.logger.info("Clicking Book a Bet")
        self.page.locator(self.sel.BOOK_A_BET).click()
        modal = self.page.locator(self.sel.BOOKING_MODAL)
        expect(modal).to_be_visible(timeout=15000)
        # The modal renders the code in more than one node; take the first.
        code_el = self.page.locator(self.sel.BOOKING_CODE).first
        code_el.wait_for(state="visible", timeout=10000)
        code = (code_el.inner_text() or "").strip()
        if not code:
            raise RuntimeError("Booking code element was empty")
        self.logger.success(f"Booking code: {code}")
        return code

    def close_modal(self) -> "BetslipPage":
        self.logger.info("Closing booking modal")
        self.page.locator(self.sel.MODAL_CLOSE).click()
        expect(self.page.locator(self.sel.BOOKING_MODAL)).to_be_hidden(timeout=8000)
        return self

    def clear_betslip(self) -> "BetslipPage":
        clear = self.page.locator(self.sel.CLEAR_BETSLIP)
        if clear.count() and clear.first.is_visible():
            self.logger.info("Clearing betslip")
            clear.first.click()
        return self

    def load_booking_code(self, code: str) -> "BetslipPage":
        """Type a booking code into the slip and click the blue Book: button."""
        self.logger.info(f"Loading booking code {code}")
        box = self.page.locator(self.sel.BOOKING_INPUT)
        box.wait_for(state="visible", timeout=10000)
        box.fill(code)
        self.page.locator(self.sel.LOAD_BOOKING_BTN).first.click()
        return self

    def total_odds(self) -> float:
        """Read the current 'Total Odds' value from the slip."""
        el = self.page.locator(self.sel.TOTAL_ODDS)
        el.wait_for(state="visible", timeout=10000)
        raw = (el.inner_text() or "0").strip().replace(",", "")
        try:
            return float(raw)
        except ValueError:
            self.logger.warning(f"Could not parse total odds from '{raw}'")
            return 0.0
