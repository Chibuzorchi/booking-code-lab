"""Sports page object: navigate, open a sport/league, pick a match & odd.

Selectors come from tests.data.Selectors so a DOM change is a one-file edit. The
match pick is dynamic — it reads whatever fixtures are listed for the day rather
than hard-coding an event id or date.
"""
from __future__ import annotations

import random
import re

from playwright.sync_api import Page

from engine.event_policy import simulation_reason
from infra.config.settings import Settings
from pages.base_page import BasePage
from tests.data import Selectors, SIGNS


class SportsPage(BasePage):
    def __init__(self, page: Page, settings: Settings, logger=None):
        super().__init__(page, settings, logger)
        self.sel = Selectors

    def open(self) -> "SportsPage":
        self.logger.info(f"Opening {self.settings.sports_url}")
        self.page.goto(self.settings.sports_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(4000)
        self._dismiss_consent()
        return self

    def _dismiss_consent(self) -> None:
        """Best-effort dismissal of the first-load consent modal."""
        button = self.page.get_by_role(
            "button", name=re.compile(self.sel.CONSENT_BUTTON_TEXT, re.I)
        )
        if button.count():
            try:
                button.first.click(timeout=3000)
                self.logger.info("Dismissed consent modal")
            except Exception as err:  # non-fatal — layout may not show it
                self.logger.debug(f"consent dismissal skipped: {err}")

    def open_soccer(self) -> "SportsPage":
        self.logger.info("Opening Soccer toggle")
        self.page.locator(self.sel.SOCCER_TOGGLE).get_by_text("Soccer").first.click()
        self.page.wait_for_timeout(1500)
        return self

    def open_league(self) -> "SportsPage":
        # 1) Expand the country toggle (England).
        toggle = self.sel.LEAGUE_TOGGLE.format(toggle_id=self.settings.league_toggle_id)
        self.logger.info(f"Expanding country toggle {self.settings.league_name}")
        self.page.locator(toggle).get_by_text(self.settings.league_name).first.click()
        self.page.wait_for_timeout(1500)
        # 2) Click a competition anchor -> loads the sports-table match list.
        anchor = self.sel.COMPETITION_ANCHOR.format(anchor_id=self.settings.competition_anchor_id)
        self.logger.info(f"Opening competition {self.settings.competition_anchor_id}")
        self.page.locator(anchor).first.click()
        # Wait for at least one fixture to render.
        self.page.wait_for_selector(self.sel.MATCHUP, timeout=20000)
        return self

    def list_matches(self) -> list[dict]:
        """Return the fixtures currently rendered: [{eid, home, away}]."""
        rows = self.page.locator(self.sel.MATCHUP)
        matches: list[dict] = []
        for i in range(rows.count()):
            row = rows.nth(i)
            raw_id = row.get_attribute("id") or ""
            eid = raw_id.replace("prematch_event-", "").strip()
            if not eid:
                continue
            matches.append(
                {
                    "eid": eid,
                    "home": (row.locator(self.sel.HOME).inner_text() or "").strip(),
                    "away": (row.locator(self.sel.AWAY).inner_text() or "").strip(),
                }
            )
        self.logger.info(f"Found {len(matches)} fixtures")
        return matches

    def pick_random_odd(self, sign: str | None = None) -> dict:
        """Pick a random fixture and click one of its 1X2 odds. Returns the pick."""
        matches = [match for match in self.list_matches()
                   if not simulation_reason(match["home"], match["away"], self.settings.league_name)]
        if not matches:
            raise RuntimeError("No non-simulation fixtures available to book")
        match = random.choice(matches)
        chosen_sign = sign or random.choice(SIGNS)
        selector = self.sel.ODD.format(eid=match["eid"], sign=chosen_sign)
        self.logger.info(
            f"Selecting {match['home']} - {match['away']} [{chosen_sign}] (event {match['eid']})"
        )
        odd = self.page.locator(selector)
        odd.wait_for(state="visible", timeout=10000)
        price = (odd.inner_text() or "").strip()
        odd.click()
        # Confirm the odd became active.
        self.page.wait_for_selector(f"{selector}.{self.sel.ODD_ACTIVE_CLASS}", timeout=8000)
        return {**match, "sign": chosen_sign, "odds": price}
