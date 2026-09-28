"""sportybet football listing: open the page and pick a random outcome."""
from __future__ import annotations

import random

from engine.event_policy import simulation_reason
from pages.base_page import BasePage
from tests.data import Selectors as S


class FootballPage(BasePage):
    def open(self) -> None:
        self.page.goto(S.FOOTBALL_URL, wait_until="domcontentloaded",
                       timeout=int(self.settings.request_timeout_s * 3000))
        self._dismiss_consent()
        # wait for at least one odds cell to render
        self.page.wait_for_selector(S.OUTCOME_ODDS, timeout=30000)

    def _dismiss_consent(self) -> None:
        for sel in S.CONSENT:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=1500):
                    el.click(timeout=1500)
                    self.logger.debug(f"dismissed consent via {sel}")
                    return
            except Exception:
                continue

    def pick_random_outcome(self) -> bool:
        """Click a random un-checked outcome; return True if one got selected."""
        outcomes = self.page.locator(f"{S.OUTCOME}:not({S.OUTCOME_CHECKED})")
        n = outcomes.count()
        if n == 0:
            self.logger.warning("no outcomes found on the page")
            return False
        indices = list(range(n))
        random.shuffle(indices)
        for idx in indices:
            cell = outcomes.nth(idx)
            try:
                row = cell.locator(S.MATCH_ROW_ANCESTOR)
                if not row.count():
                    continue  # Never choose an outcome whose fixture cannot be inspected.
                fixture = row.inner_text(timeout=3000).strip()
                if not fixture:
                    continue
                league = cell.locator(S.LEAGUE_ANCESTOR)
                league_name = league.inner_text(timeout=3000).splitlines()[0] if league.count() else ""
                if simulation_reason(fixture, league_name):
                    continue
                cell.scroll_into_view_if_needed(timeout=3000)
                cell.click(timeout=3000)
                return True
            except Exception:
                continue
        self.logger.warning("no eligible non-simulation outcome could be selected")
        return False
