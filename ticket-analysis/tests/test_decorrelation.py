"""Deterministic run-extract to selector contract tests; no provider traffic."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from portfolio.decorrelation import ExtractError, build_pool, decorrelate_extract
from portfolio.selection import ParamError


def coupon(code, event, provider="sportybet", price=4):
    return {"code": code, "provider": provider, "num_legs": 1,
            "site_displayed_odds": 10,
            "selections": [{"event_id": event, "event": event, "market": "M",
                            "pick": "P", "odds": price,
                            "kickoff": "2030-01-02T00:00:00Z"}]}


class DecorrelationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "run.json"
        clock = patch("portfolio.analysis.datetime", wraps=datetime)
        mocked = clock.start()
        self.addCleanup(clock.stop)
        mocked.now.return_value = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def write(self, rows, provider="sportybet"):
        self.path.write_text(json.dumps({"provider": provider, "qualifying": rows}))

    def pool(self, **kwargs):
        return build_pool(self.path, "sportybet", **{
            "odds_basis": "parsed_leg_product", "min_odds": 2, **kwargs})

    def test_full_pool_keeps_overlaps_and_ignores_neighbor_extract(self):
        self.write([coupon("A", "shared"), coupon("B", "shared"), coupon("C", "solo")])
        self.path.with_name("other.json").write_text("not valid JSON")
        for cap, expected in ((1, 2), (2, 3)):
            result = decorrelate_extract(self.path, "sportybet", {"max_exposure": cap},
                                        odds_basis="parsed_leg_product", min_odds=2)
            self.assertEqual(result["pool_size"], 3)
            self.assertEqual(result["selection"]["selected_count"], expected)
            if cap == 1:
                self.assertEqual(result["selection"]["rejected"][0]["reason"], "exposure_cap")
                codes = result["selection"]["selected_codes"]
                self.assertIn("C", codes)
                self.assertEqual(len(set(codes) & {"A", "B"}), 1)

    def test_provider_isolation_for_each_provider(self):
        for provider, other in (("sportybet", "bet9ja"), ("bet9ja", "sportybet")):
            self.write([coupon("A", "e", provider), coupon("B", "e", other)], provider)
            result = build_pool(self.path, provider, odds_basis="parsed_leg_product", min_odds=2)
            self.assertEqual([t["provider"] for t in result["tickets"]], [provider])
            self.assertEqual(result["excluded"][0]["reason"], "provider_mismatch")

    def test_expired_and_malformed_are_excluded(self):
        expired = coupon("OLD", "e")
        expired["selections"][0]["kickoff"] = "2029-12-31T00:00:00Z"
        self.write([expired, {"code": "BAD"}, None, coupon("OK", "e")])
        result = self.pool()
        self.assertEqual(result["pool_size"], 1)
        self.assertEqual(len(result["excluded"]), 3)
        self.assertTrue(any("expired" in issue for issue in result["excluded"][0]["issues"]))

    def test_explicit_basis_changes_band_without_fallback(self):
        self.write([coupon("A", "e")])
        self.assertEqual(self.pool(min_odds=5)["pool_size"], 0)
        result = self.pool(odds_basis="site_displayed_odds", min_odds=5)
        self.assertEqual(result["pool_size"], 1)
        self.assertEqual(result["odds_basis"], "site_displayed_odds")
        row = coupon("A", "e")
        del row["site_displayed_odds"]
        self.write([row])
        self.assertEqual(self.pool(odds_basis="site_displayed_odds")["excluded"][0]["reason"], "missing_odds")

    def test_inclusive_bounds_and_no_ceiling(self):
        self.write([coupon("A", "a", price=2), coupon("B", "b", price=4), coupon("C", "c", price=100)])
        self.assertEqual(self.pool(max_odds=4)["pool_size"], 2)
        self.assertEqual(self.pool(max_odds=None)["pool_size"], 3)

    def test_duplicate_code_not_counted_twice_and_conflict_fails(self):
        row = coupon("A", "a")
        self.write([row, copy.deepcopy(row)])
        self.assertEqual(self.pool()["pool_size"], 1)
        other = coupon("A", "b")
        for rows in ([row, other], [other, row]):
            self.write(rows)
            with self.assertRaises(ExtractError):
                self.pool()

    def test_invalid_sources_fail_instead_of_empty_success(self):
        for data in ([], {}, {"provider": "bet9ja", "qualifying": []}):
            self.path.write_text(json.dumps(data))
            with self.assertRaises(ExtractError):
                self.pool()
        self.path.write_text("broken JSON")
        with self.assertRaises(ExtractError):
            self.pool()
        with self.assertRaises(ExtractError):
            build_pool(self.path.parent, "sportybet", odds_basis="parsed_leg_product", min_odds=2)

    def test_invalid_policy_and_params_fail_before_reading(self):
        for kwargs in ({"odds_basis": "unknown"}, {"min_odds": True},
                       {"max_odds": 1}, {"max_odds": float("inf")}):
            with self.assertRaises(ParamError):
                self.pool(**kwargs)
        with self.assertRaises(ParamError):
            decorrelate_extract(self.path, "sportybet", {"max_exposure": 0},
                                odds_basis="parsed_leg_product", min_odds=2)

    def test_empty_pool_and_target(self):
        self.write([])
        self.assertEqual(self.pool()["tickets"], [])
        self.write([coupon("A", "a"), coupon("B", "b")])
        result = decorrelate_extract(self.path, "sportybet", {"target": 1},
                                    odds_basis="parsed_leg_product", min_odds=2)
        self.assertEqual(result["selection"]["selected_count"], 1)


if __name__ == "__main__":
    unittest.main()
