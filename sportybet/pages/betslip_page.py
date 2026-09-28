"""sportybet betslip: book the current selection, read the code, clear the slip."""
from __future__ import annotations

import re

from pages.base_page import BasePage
from tests.data import Selectors as S

CODE_RE = re.compile(r"[A-Z0-9]{5,8}")


class BetslipPage(BasePage):
    def book_bet(self) -> str | None:
        self.page.locator(S.BOOK_BET).first.click(timeout=15000)
        self.page.wait_for_selector(S.CODE_CONTAINER, timeout=15000)
        code = None
        # try the (invisible) input value first, then the container text
        try:
            code = self.page.locator(S.CODE_INPUT).first.input_value(timeout=2000).strip()
        except Exception:
            code = None
        if not code:
            txt = self.page.locator(S.CODE_CONTAINER).first.inner_text(timeout=3000)
            m = CODE_RE.search(txt.upper())
            code = m.group(0) if m else None
        return code

    def close_modal(self) -> None:
        try:
            self.page.locator(S.MODAL_CLOSE).first.click(timeout=5000)
        except Exception:
            self.logger.debug("modal close not found (already closed?)")

    def clear_betslip(self) -> None:
        # multi-selection -> Remove All; single -> the delete icon
        try:
            ra = self.page.locator(S.REMOVE_ALL).first
            if ra.is_visible(timeout=1500):
                ra.click(timeout=3000)
                # a "Remove Betslip" confirmation dialog appears -> confirm
                try:
                    self.page.locator(S.REMOVE_CONFIRM_OK).first.click(timeout=3000)
                except Exception:
                    self.logger.debug("no remove-all confirmation dialog")
                return
        except Exception:
            pass
        try:
            self.page.locator(S.SINGLE_DELETE).first.click(timeout=3000)
        except Exception:
            self.logger.debug("nothing to clear")
