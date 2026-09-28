import unittest

from portfolio.analysis import normalize_ticket
from portfolio.positions import summarize_positions


def raw_ticket(code="A"):
    return {"provider": "sportybet", "code": code, "num_legs": 1,
            "selections": [{"event_id": 1, "event": "A-B", "market": "1X2", "pick": "Home", "odds": 2}]}


def position(pid, status="placed", stake="100.10", currency="NGN"):
    return {"position_id": pid, "status": status, "stake": stake, "currency": currency, "ticket": raw_ticket()}


class PositionTests(unittest.TestCase):
    def test_repeated_wagers_preserved_and_currencies_not_added(self):
        values = [position("1"), position("2"), position("3", currency="USD")]
        candidate, _ = normalize_ticket(raw_ticket("B"), None)
        result = summarize_positions({"positions": values}, [candidate])
        self.assertEqual(result["totals_by_currency"]["NGN"]["outstanding_stakes"], "200.20")
        self.assertEqual(result["selection_exposure"][0]["stake_by_currency"], {"NGN": "200.20", "USD": "100.10"})
        self.assertEqual(result["candidate_links"][0]["shared_selection_positions"], ["1", "2", "3"])

    def test_selected_and_settled_not_outstanding(self):
        settled = {**position("2", "settled"), "payout": "120"}
        result = summarize_positions({"positions": [position("1", "selected"), settled]}, [])
        self.assertEqual(result["outstanding_positions"], [])
        self.assertEqual(result["totals_by_currency"]["NGN"]["settled_net_pnl"], "19.90")
        self.assertEqual(result["totals_by_currency"]["NGN"]["placed_stakes"], "100.10")

    def test_unresolved_position_money_not_hidden(self):
        broken = position("1")
        broken["ticket"]["selections"][0]["event_id"] = None
        result = summarize_positions({"positions": [broken]}, [])
        self.assertEqual(result["totals_by_currency"]["NGN"]["outstanding_stakes"], "100.10")
        self.assertEqual(len(result["unclassified_positions"]), 1)
        self.assertEqual(result["selection_exposure"], [])

    def test_invalid_stakes_and_duplicate_ids_rejected(self):
        for value in ("NaN", "-1", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                summarize_positions({"positions": [position("1", stake=value)]}, [])
        with self.assertRaises(ValueError):
            summarize_positions({"positions": [position("1"), position("1")]}, [])
