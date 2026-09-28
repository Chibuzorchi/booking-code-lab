import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from portfolio.analysis import analyze, changes, compare, normalize_ticket, validate_policy
from portfolio.__main__ import main


def leg(event=1, pick="Home", market="1X2"):
    return {"event_id": event, "event": f"Match {event}", "market": market,
            "pick": pick, "odds": 2.0}


def ticket(code, legs=None, odds=4, provider="sportybet"):
    legs = legs if legs is not None else [leg()]
    return {"code": code, "provider": provider, "ok": True,
            "num_legs": len(legs), "total_odds": odds, "selections": legs}


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def source(self, tickets, name="extract.json"):
        path = self.root / name
        path.write_text(json.dumps({"provider": "sportybet", "qualifying": tickets}))
        return path

    def test_hand_calculated_overlap_and_event_warning(self):
        path = self.source([ticket("a", [leg(), leg(2)]), ticket("b", [leg(), leg(3)]),
                            ticket("c", [leg(2, "Away")]), ticket("d", [leg(4)])])
        result = analyze([path], {}, top_n=1, long_odds=2)
        view = result["combined"]
        self.assertEqual(view["category_A"], ["sportybet:C", "sportybet:D"])
        self.assertEqual(view["category_B"], ["sportybet:A", "sportybet:B"])
        self.assertEqual(view["tickets"][0]["shared_leg_fraction"], .5)
        self.assertEqual(len(view["same_event_exposure"]), 2)
        self.assertEqual(len(view["R1_shared"]), 1)
        self.assertEqual(len(view["R3_long_odds_proxy"]), 1)

    def test_profiles_union_and_cross_profile_overlap(self):
        path = self.source([ticket("a", odds=3), ticket("b", odds=100), ticket("c", [leg(9)], odds=20)])
        result = analyze([path], {"profiles": {"low": {"odds_basis": "site_displayed_odds", "max_total_odds": 20},
                                              "high": {"odds_basis": "site_displayed_odds", "min_total_odds": 20}}})
        self.assertEqual(result["combined"]["candidate_count"], 3)
        self.assertEqual(result["profiles"]["low"]["category_B"], [])
        self.assertEqual(result["combined"]["category_B"], ["sportybet:A", "sportybet:B"])

    def test_missing_identity_duplicate_leg_and_conflict_quarantined(self):
        broken = ticket("a")
        broken["selections"][0]["event_id"] = 0
        path = self.source([broken, ticket("b", [leg(), leg()]), ticket("c"), ticket("c", odds=8)])
        result = analyze([path], {})
        self.assertEqual(result["combined"]["candidate_count"], 0)
        self.assertEqual(len(result["unclassified"]), 3)

    def test_repeat_observation_and_content_aliases(self):
        a = ticket("a")
        path = self.source([a, a, ticket("b")])
        result = analyze([path, path], {})["combined"]
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["distinct_ticket_contents"], 1)
        self.assertEqual(result["R1_shared"][0]["distinct_ticket_contents"], 1)

    def test_provider_namespacing_and_case_rules(self):
        path = self.source([ticket("a"), ticket("A", provider="bet9ja"), ticket("a", provider="bet9ja")])
        view = analyze([path], {})["combined"]
        self.assertEqual(view["category_A"], ["sportybet:A"])
        self.assertEqual(view["category_B"], ["bet9ja:A", "bet9ja:a"])

    def test_filter_changes_membership_and_exclusion_wins(self):
        path = self.source([ticket("a", odds=3), ticket("b", odds=100)])
        policy = {"profiles": {"low": {"odds_basis": "site_displayed_odds", "max_total_odds": 20}},
                  "include_codes": ["sportybet:a", "sportybet:b", "sportybet:missing"],
                  "exclude_codes": ["sportybet:a"]}
        result = analyze([path], policy)
        self.assertEqual(result["combined"]["candidate_count"], 0)
        self.assertEqual(result["missing_requested_codes"], ["sportybet:MISSING"])
        policy["exclude_codes"] = []
        self.assertEqual(analyze([path], policy)["combined"]["category_A"], ["sportybet:A"])
        policy["profiles"]["low"]["max_total_odds"] = 0
        self.assertEqual(len(analyze([path], policy)["combined"]["category_B"]), 2)

    def test_policy_validation(self):
        for config in ({"min_total_odds": 10, "max_total_odds": 2}, {"max_legs": 1.5},
                       {"max_legs": float("nan")}, {"max_legs": True}, {"typo": 1}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_policy({"profiles": {"p": config}})

    def test_order_invariance_of_comparison(self):
        tickets = [normalize_ticket(ticket("a", [leg(), leg(2)]), "sportybet")[0],
                   normalize_ticket(ticket("b"), "sportybet")[0]]
        self.assertEqual(compare(tickets), compare(list(reversed(tickets))))

    def test_market_lines_and_periods_stay_separate(self):
        path = self.source([ticket("a", [leg(pick="Over 2.5", market="Over/Under")]),
                            ticket("b", [leg(pick="Over 3.5", market="Over/Under")]),
                            ticket("c", [leg(pick="Over 2.5", market="1st Half - Over/Under")])])
        view = analyze([path], {})["combined"]
        self.assertEqual(len(view["category_A"]), 3)
        self.assertEqual(len(view["same_event_exposure"]), 1)

    def test_refresh_explains_category_transition(self):
        before = analyze([self.source([ticket("a")], "before.json")], {})
        after = analyze([self.source([ticket("a"), ticket("b")], "after.json")], {})
        diff = changes(before, after)["combined"]
        self.assertEqual(diff["added"], ["sportybet:B"])
        self.assertEqual(diff["category_changes"], [{"id": "sportybet:A", "before": "A", "after": "B"}])

    def test_cli_snapshot_replay_and_no_overwrite(self):
        source = self.source([ticket("a"), ticket("b")])
        output = self.root / "run"
        args = ["--from", str(source), "--output", str(output)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 0)
            saved = (output / "report.json").read_bytes()
            self.assertEqual(main(args), 2)
            self.assertEqual(saved, (output / "report.json").read_bytes())
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertTrue(manifest["complete"])
        replay = analyze(list((output / "inputs").glob("*.json")), manifest["policy"])
        self.assertEqual(replay["combined"], json.loads(saved)["combined"])

    def test_invalid_json_has_no_output(self):
        source = self.root / "bad.json"
        source.write_text('{"qualifying": [NaN]}')
        output = self.root / "run"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--from", str(source), "--output", str(output)]), 2)
        self.assertFalse(output.exists())

    def test_position_cli_archives_input_and_links_candidates(self):
        source = self.source([ticket("a")])
        positions = self.root / "positions.json"
        positions.write_text(json.dumps({"positions": [{"position_id": "p1", "status": "placed",
                                                       "stake": "200", "currency": "NGN", "ticket": ticket("b")}]}))
        output = self.root / "with-positions"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--from", str(source), "--positions", str(positions), "--output", str(output)]), 0)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["positions"]["candidate_links"][0]["shared_selection_positions"], ["p1"])
        self.assertEqual(json.loads((output / "positions_input.json").read_text()), json.loads(positions.read_text()))

    def test_malformed_previous_report_rejected(self):
        with self.assertRaises(ValueError):
            changes({"schema_version": 1, "combined": {}}, {})


if __name__ == "__main__":
    unittest.main()
