"""sportybet DOM selectors (desktop web), from the booking flow.

Kept out of page logic so a UI change is a one-file edit.
"""


class Selectors:
    FOOTBALL_URL = "https://www.sportybet.com/ng/sport/football/"

    # cookie / consent banners (best-effort; several spellings)
    CONSENT = [
        "button:has-text('Accept')",
        "button:has-text('OK')",
        "button:has-text('Got it')",
        ".cookie-accept, .accept-cookies",
    ]

    # a bookable outcome cell + its odds
    OUTCOME = "div.m-outcome"
    OUTCOME_ODDS = "span.m-outcome-odds"
    OUTCOME_CHECKED = "div.m-outcome--checked"
    MATCH_ROW_ANCESTOR = "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' match-row ')][1]"
    LEAGUE_ANCESTOR = "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' match-league ')][1]"

    BOOK_BET = "span[data-cms-key='book_bet']"

    # booking-code modal
    CODE_CONTAINER = ".booking-code-share-code"
    CODE_INPUT = "#copyShareCode"
    MODAL_CLOSE = "img[data-action='close']"

    # clear the betslip between bookings
    REMOVE_ALL = "span[data-cms-key='remove_all']"
    # Remove All pops a confirmation dialog -> click OK
    REMOVE_CONFIRM_OK = "a.es-dialog-btn[data-action='btn'][data-ret='1']"
    SINGLE_DELETE = "i.m-icon-delete"

    # load-a-code (for the decode-in-UI path, not used by the booker)
    BOOKING_INPUT = "input[data-op='desktop-booking-code-input']"
    LOAD_BUTTON = "button[data-op='desktop-booking-code-load-button']"
