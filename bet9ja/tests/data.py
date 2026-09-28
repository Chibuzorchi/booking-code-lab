"""Selectors and static test data, kept separate from page/test logic.

All bet9ja DOM selectors live here so a UI change is a one-file edit. IDs that
embed an event id use {eid}; sign is one of "1" | "X" | "2".
"""


class Selectors:
    # --- Consent modal (shown on first load) ---
    # Dismissed best-effort by button text; "OK" on the live site.
    CONSENT_BUTTON_TEXT = r"^(OK|Accept|I Agree|Agree|Got it)$"

    # --- Left sidebar navigation ---
    SOCCER_TOGGLE = "#left_prematch_sport-1_soccer_label-toggle"
    # Country toggle id is configurable (any country works); default England.
    LEAGUE_TOGGLE = "#{toggle_id}"
    # Competition anchor under the expanded country (loads the sports-table).
    COMPETITION_ANCHOR = "#{anchor_id}"

    # --- Matches list ---
    # Every fixture row is a matchup cell whose id starts with this prefix.
    MATCHUP = "div.sports-table__matchup[id^='prematch_event-']"
    HOME = ".sports-table__home"
    AWAY = ".sports-table__away"
    DATE_HEADER = ".sports-head__date span"

    # --- Odds for a given event / sign (1X2 market) ---
    ODD = "#prematch_event-{eid}_event-{eid}_odds_market-1x2_sign-{sign}"
    ODD_ACTIVE_CLASS = "sports-table__odds-item--active"

    # --- Betslip / booking ---
    BOOK_A_BET = "#betslip_buttons_bookabet"
    BOOKING_MODAL = ".share-coupon-modal-book"
    BOOKING_CODE = ".booking-code-display"
    MODAL_CLOSE = ".share-coupon-modal_header-btnClose"
    CLEAR_BETSLIP = "#betslip_head_removeall"
    BOOKING_INPUT = "input.input[placeholder='Booking Number']"
    TOTAL_ODDS = ".betslip__row strong.txt-primary"
    # The blue "Book:" button next to the booking-number input.
    LOAD_BOOKING_BTN = "button.btn-blue-s"


SIGNS = ("1", "X", "2")
