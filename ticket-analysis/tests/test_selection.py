"""Exposure-constrained subset selector: guarantees and explainability."""
import json
import tempfile
import unittest
from pathlib import Path

from portfolio.analysis import analyze
from portfolio.selection import select_subset


def native(event, market="18", spec="total=2.5", outcome="12", price=2.0):
    return {"event_id": 0, "native_event_id": event, "market_id": market,
            "specifier": spec, "outcome_id": outcome, "event": event, "market": "M",
            "pick": "P", "odds": price}


def ticket(code, events, provider="sportybet"):
    legs = [native(e) for e in events]
    return {"provider": provider, "code": code, "num_legs": len(legs), "selections": legs}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def analyze_data(self, data, select, provider="sportybet"):
        path = self.root / "sample.json"
        path.write_text(json.dumps({"provider": provider, "qualifying": data}))
        return analyze([path], {}, select=select)

    def exposure(self, selection, data):
        """Independent recompute of per-selection exposure across selected codes."""
        by_code = {t["code"]: t for t in data}
        counts = {}
        for code in selection["selected_codes"]:
            for s in by_code[code]["selections"]:
                key = (s["native_event_id"], s["market_id"], s["specifier"], s["outcome_id"])
                counts[key] = counts.get(key, 0) + 1
        return counts

    def test_cap_is_never_exceeded(self):
        # One leg shared by 10 tickets; cap of 3 must keep at most 3 of them.
        data = [ticket(f"T{i}", ["shared", f"uniq{i}"]) for i in range(10)]
        result = self.analyze_data(data, {"max_exposure": 3})
        sel = result["selection"]
        counts = self.exposure(sel, data)
        self.assertLessEqual(max(counts.values()), 3)
        self.assertEqual(sel["realized_max_exposure"], 3)

    def test_shared_leg_keeps_exactly_cap_many(self):
        data = [ticket(f"T{i}", ["shared", f"uniq{i}"]) for i in range(10)]
        result = self.analyze_data(data, {"max_exposure": 3})
        sel = result["selection"]
        shared_key = ("shared", "18", "total=2.5", "12")
        self.assertEqual(self.exposure(sel, data)[shared_key], 3)

    def test_rejected_by_cap_names_the_blocking_selection(self):
        data = [ticket(f"T{i}", ["shared", f"uniq{i}"]) for i in range(6)]
        result = self.analyze_data(data, {"max_exposure": 2})
        capped = [r for r in result["selection"]["rejected"] if r["reason"] == "exposure_cap"]
        self.assertTrue(capped)
        blocking = capped[0]["blocking"][0]
        self.assertEqual(blocking["event"], "shared")
        self.assertEqual(len(blocking["at_capacity_with"]), 2)

    def test_target_caps_selected_count(self):
        data = [ticket(f"T{i}", [f"u{i}"]) for i in range(20)]  # all independent
        result = self.analyze_data(data, {"max_exposure": 5, "target": 7})
        sel = result["selection"]
        self.assertEqual(sel["selected_count"], 7)
        self.assertTrue(all(r["reason"] == "target_reached"
                            for r in sel["rejected"]))

    def test_fully_independent_pool_is_kept_entirely(self):
        data = [ticket(f"T{i}", [f"u{i}"]) for i in range(12)]
        sel = self.analyze_data(data, {"max_exposure": 1})["selection"]
        self.assertEqual(sel["selected_count"], 12)
        self.assertEqual(sel["rejected"], [])

    def test_deterministic(self):
        data = [ticket(f"T{i}", ["shared", f"u{i % 4}"]) for i in range(15)]
        a = self.analyze_data(data, {"max_exposure": 2})["selection"]["selected_codes"]
        b = self.analyze_data(data, {"max_exposure": 2})["selection"]["selected_codes"]
        self.assertEqual(a, b)

    def test_cap_is_enforced_per_provider_independently(self):
        # Same event label under two providers must not pool: cross-provider keys differ.
        data = ([ticket(f"S{i}", ["shared"], "sportybet") for i in range(5)]
                + [ticket(f"B{i}", ["shared"], "bet9ja") for i in range(5)])
        path = self.root / "mixed.json"
        # provider is per-ticket here; loader honors the ticket's own provider field.
        path.write_text(json.dumps({"provider": "sportybet", "qualifying": data}))
        sel = analyze([path], {}, select={"max_exposure": 2})["selection"]
        by_prov = sel["by_provider"]
        # each provider caps its own "shared" leg at 2 -> 2 + 2 = 4 selected total
        self.assertEqual(by_prov["sportybet"]["selected"], 2)
        self.assertEqual(by_prov["bet9ja"]["selected"], 2)

    def test_direct_call_validates_arguments(self):
        with self.assertRaises(ValueError):
            select_subset([], 0)
        with self.assertRaises(ValueError):
            select_subset([], 3, target=0)


    def test_reports_odds_range_and_uniqueness_buckets(self):
        # code A: 2 independent legs; codes sharing "shared" get bucketed by overlap.
        data = [ticket("SOLO", ["a", "b"]),
                ticket("X1", ["shared", "c"]), ticket("X2", ["shared", "d"])]
        sel = self.analyze_data(data, {"max_exposure": 2})["selection"]
        rng = sel["odds_range"]
        self.assertEqual(rng["basis"], "parsed_leg_product")
        # every leg priced at 2.0; products are 4.0 for each 2-leg code
        self.assertEqual(rng["min"], 4.0)
        self.assertEqual(rng["max"], 4.0)
        tiers = {b["tier"]: b for b in sel["uniqueness_buckets"]}
        self.assertIn("fully independent — no shared games", tiers)
        self.assertIn("SOLO", tiers["fully independent — no shared games"]["codes"])
        # X1/X2 share one game -> 1-2 shared bucket
        self.assertEqual(tiers["1-2 shared games"]["code_count"], 2)
        # buckets carry their own odds range
        self.assertEqual(tiers["1-2 shared games"]["odds_range"]["max"], 4.0)

    def test_each_selected_row_labels_its_tier(self):
        data = [ticket("SOLO", ["a"]), ticket("Y1", ["shared", "c"]), ticket("Y2", ["shared", "d"])]
        sel = self.analyze_data(data, {"max_exposure": 2})["selection"]
        rows = {r["code"]: r for r in sel["selected"]}
        self.assertEqual(rows["SOLO"]["uniqueness_tier"], "fully independent — no shared games")
        self.assertEqual(rows["Y1"]["uniqueness_tier"], "1-2 shared games")


if __name__ == "__main__":
    unittest.main()