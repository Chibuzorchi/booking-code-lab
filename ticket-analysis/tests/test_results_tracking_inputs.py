"""F9 population audit: freezes/inventories a run's committed extract.

Builds a succeeded run directory by hand (status.json + extracts/) rather
than running a fixture scanner subprocess, since the audit only ever reads
through RunRegistry.committed_extract_snapshot() and never touches the
scan/worker lifecycle.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from portfolio.registry import RunRegistry, RunStateError
from portfolio.results_tracking.inputs import PopulationAuditError, audit_run


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class PopulationAuditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.registry = RunRegistry(self.workspace)

    def _make_run(self, provider, run_id, extract, state="succeeded"):
        run_dir = self.workspace / "results" / "runs" / provider / run_id
        extract_path = run_dir / "extracts" / "extract.json"
        raw = json.dumps(extract).encode("utf-8")
        extract_path.parent.mkdir(parents=True, exist_ok=True)
        extract_path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        _write_json(run_dir / "status.json", {
            "schema_version": 1, "run_id": run_id, "provider": provider,
            "state": state, "request": {"seed": "ABC1234"}, "seed": "ABC1234",
            "started_at": "2026-10-23T00:00:00+00:00", "finished_at": "2026-10-23T00:01:00+00:00",
            "updated_at": "2026-10-23T00:01:00+00:00", "stop_reason": "exhausted",
            "counters": {"tried": 1, "found_ok": 1, "qualifying": len(extract.get("qualifying", [])),
                         "excluded_simulations": 0, "unpriced": 0},
            "error": None, "cancel_requested_at": None, "cancel_granted_at": None,
            "supervisor": {"pid": None, "exit_code": None}, "worker": {"pid": None},
            "extract": {"path": str(extract_path.resolve()), "sha256": digest, "size": len(raw),
                        "validated_at": "2026-10-23T00:01:00+00:00", "policy": {}} if state == "succeeded" else None,
            "recovery_note": None,
        })
        return run_id

    def _selection(self, **overrides):
        base = {"native_event_id": "100", "market_id": "1", "outcome_id": "2", "specifier": "",
                "event_id": 100, "event": "Alpha - Beta", "league": "L1", "market": "1X2",
                "pick": "1", "odds": 1.5, "sport": "Soccer", "kickoff": "2026-10-25 15:45:00"}
        base.update(overrides)
        return base

    def _ticket(self, code, selections, **overrides):
        raw_path = self.workspace / "raw" / f"{code}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text("{}", encoding="utf-8")
        base = {"code": code, "ok": True, "provider": "bet9ja", "num_legs": len(selections),
                "selections": selections, "retrieval_completeness": "complete",
                "bet_type": "0", "raw_response_path": str(raw_path)}
        base.update(overrides)
        return base

    def test_audits_simple_run(self):
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [self._selection()])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000000-aaaaaaaa", extract)
        report = audit_run(self.registry, run_id, expected_provider="bet9ja", expected_rows=1)
        self.assertEqual(report["row_count"], 1)
        self.assertEqual(report["provider"], "bet9ja")
        self.assertEqual(report["unique_fixtures"], 1)
        self.assertEqual(report["identity_modes"], {"structured_native": 1})
        self.assertEqual(report["conflicting_observations"], [])
        self.assertEqual(report["raw_response_missing"], [])
        self.assertEqual(report["rows"][0]["row_id"], f"{report['extract_sha256']}:0")

    def test_wrong_provider_raises(self):
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [self._selection()])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000000-bbbbbbbb", extract)
        with self.assertRaises(PopulationAuditError):
            audit_run(self.registry, run_id, expected_provider="sportybet")

    def test_wrong_row_count_raises(self):
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [self._selection()])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000000-cccccccc", extract)
        with self.assertRaises(PopulationAuditError):
            audit_run(self.registry, run_id, expected_rows=2)

    def test_wrong_odds_basis_raises(self):
        extract = {"provider": "bet9ja", "odds_basis": "site_displayed_odds",
                   "qualifying": [self._ticket("Q1", [self._selection()])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000000-dddddddd", extract)
        with self.assertRaises(PopulationAuditError):
            audit_run(self.registry, run_id)

    def test_nonsucceeded_run_refused(self):
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product", "qualifying": []}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000000-eeeeeeee", extract, state="failed")
        with self.assertRaises(RunStateError):
            audit_run(self.registry, run_id)

    def test_conflicting_observation_detected(self):
        leg_a = self._selection(event="Alpha - Beta", kickoff="2026-10-25 15:45:00")
        leg_b = self._selection(event="Alpha - Beta FC", kickoff="2026-10-25 16:00:00")
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [leg_a]), self._ticket("Q2", [leg_b])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000000-ffffffff", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(len(report["conflicting_observations"]), 1)
        self.assertEqual(report["unique_fixtures"], 1)

    def test_duplicate_observation_not_flagged_as_conflict(self):
        leg = self._selection()
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [leg]), self._ticket("Q2", [dict(leg)])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000001-aaaaaaaa", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(report["conflicting_observations"], [])
        self.assertEqual(report["unique_fixtures"], 1)

    def test_missing_selection_field_recorded(self):
        leg = self._selection(league="")
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [leg])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000001-bbbbbbbb", extract)
        report = audit_run(self.registry, run_id)
        self.assertIn("league", report["missing_fields"])
        self.assertEqual(len(report["missing_fields"]["league"]), 1)

    def test_naive_and_offset_aware_kickoffs_kept_separate(self):
        naive = self._selection(native_event_id="1", kickoff="2026-10-25 15:45:00")
        aware = self._selection(native_event_id="2", kickoff="2026-10-25T15:45:00+00:00")
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [naive]), self._ticket("Q2", [aware])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000001-cccccccc", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(report["kickoff_timezones"], {"naive": 1, "offset_aware": 1})
        self.assertEqual(set(report["first_last_kickoff_by_timezone"]), {"naive", "offset_aware"})

    def test_raw_response_missing_recorded(self):
        ticket = self._ticket("Q1", [self._selection()])
        ticket["raw_response_path"] = None
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product", "qualifying": [ticket]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000001-dddddddd", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(len(report["raw_response_missing"]), 1)
        self.assertEqual(report["raw_response_missing"][0]["reason"], "path_absent")

    def test_raw_response_nonexistent_file_recorded_as_missing(self):
        # F9_IMPLEMENTATION_REVIEW.md R06: a nonempty but nonexistent path
        # must be flagged, not taken on faith.
        ticket = self._ticket("Q1", [self._selection()])
        ticket["raw_response_path"] = str(self.workspace / "raw" / "does-not-exist.json")
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product", "qualifying": [ticket]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000001-eeeeeeee", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(len(report["raw_response_missing"]), 1)
        self.assertEqual(report["raw_response_missing"][0]["reason"], "file_not_found")

    def test_raw_response_existing_file_not_flagged_missing(self):
        ticket = self._ticket("Q1", [self._selection()])  # _ticket writes a real file
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product", "qualifying": [ticket]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000001-ffffffff", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(report["raw_response_missing"], [])

    def test_fixture_metadata_conflict_detected_across_markets(self):
        # R07: same native event, different market/kickoff metadata --
        # must surface as a fixture-level conflict even with no leg-identity
        # collision.
        leg_a = self._selection(market="1X2", pick="1", market_id="1", outcome_id="2",
                                 kickoff="2026-10-25 15:45:00")
        leg_b = self._selection(market="Over/Under", pick="Over", market_id="9", outcome_id="8",
                                 kickoff="2026-10-25 16:00:00")
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [self._ticket("Q1", [leg_a]), self._ticket("Q2", [leg_b])]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000002-aaaaaaaa", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(report["conflicting_observations"], [])
        self.assertEqual(len(report["conflicting_fixture_observations"]), 1)

    def test_duplicate_ticket_code_identical_content_not_flagged_conflicting(self):
        leg = self._selection()
        ticket_a = self._ticket("Q1", [leg])
        ticket_b = dict(ticket_a)
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [ticket_a, ticket_b]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000002-bbbbbbbb", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(len(report["duplicate_ticket_codes"]), 1)
        self.assertEqual(report["conflicting_ticket_codes"], [])

    def test_same_code_different_content_flagged_conflicting(self):
        ticket_a = self._ticket("Q1", [self._selection(pick="1")])
        ticket_b = self._ticket("Q1", [self._selection(pick="2")])
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product",
                   "qualifying": [ticket_a, ticket_b]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000002-cccccccc", extract)
        report = audit_run(self.registry, run_id)
        self.assertEqual(report["duplicate_ticket_codes"], [])
        self.assertEqual(len(report["conflicting_ticket_codes"]), 1)

    def test_malformed_selections_container_recorded_explicitly(self):
        ticket = self._ticket("Q1", [])
        ticket["selections"] = "not-a-list"
        extract = {"provider": "bet9ja", "odds_basis": "parsed_leg_product", "qualifying": [ticket]}
        run_id = self._make_run("bet9ja", "run-bet9ja-20261023-000002-dddddddd", extract)
        report = audit_run(self.registry, run_id)
        self.assertIn("selections_not_list", report["missing_fields"])


if __name__ == "__main__":
    unittest.main()
