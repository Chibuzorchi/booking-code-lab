"""Historical user journeys, using real CSV parsing and immutable report replay."""
import base64
import json
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from portfolio.results_tracking.__main__ import evaluate_run, EvaluationInputError
from portfolio.results_tracking.jobs import JobState, JobStore
from portfolio.results_tracking.local_sources import freeze_local_sources
from portfolio.results_tracking.results_api import (
    ResultsApiConfig, ResultsApiError, compute_config_digest, get_latest_report,
    list_codes, list_saved_codes, load_population, submit_evaluation, _dates_needed,
)
from portfolio.results_tracking.sources.api_football import (
    ApiFootballAccessError, _classify_api_errors,
)
from test_results_tracking_api import FakeRegistry, _extract, _ticket, _wait_for_terminal, _raw_fixture


class HistoricalResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / "E0.csv"
        self.csv.write_text("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG\n"
            "E0,08/10/2026,Arsenal,Chelsea,2,0,1,0\n"
            "E0,09/10/2026,Arsenal,Chelsea,,,,\n")
        rows = [
            _ticket("WIN", "Arsenal - Chelsea", "L", "1X2", "1", "2026-10-08"),
            _ticket("LOSS", "Arsenal - Chelsea", "L", "1X2", "2", "2026-10-08"),
            _ticket("VOID", "Arsenal - Chelsea", "L", "Over/Under", "Over", "2026-10-08"),
            _ticket("PENDING", "Arsenal - Chelsea", "L", "1X2", "1", "2026-10-09"),
            _ticket("UNSUPPORTED", "Arsenal - Chelsea", "L", "Cards Over/Under", "Over", "2026-10-08"),
        ]
        rows[2]["selections"][0]["specifier"] = "2"
        for row in rows:
            row["selections"][0]["sport"] = "Football"
        self.registry = FakeRegistry(_extract(rows))
        self.keys = list(load_population(self.registry, "run-1")[2])
        aliases = [{"namespace": ns, "display_name": name, "canonical": canonical}
            for ns, name, canonical in [
                ("bet9ja_competition", "L", "england-top"),
                ("football_data_competition", "England top flight", "england-top"),
                ("bet9ja_team", "Arsenal", "arsenal"), ("bet9ja_team", "Chelsea", "chelsea"),
                ("football_data_team", "Arsenal", "arsenal"), ("football_data_team", "Chelsea", "chelsea")]]
        self.settings = {"network_enabled": False, "aliases": aliases,
            "football_data_files": [{"path": "E0.csv", "division": "E0", "season": "2026-27",
                "competition": "England top flight", "downloaded_at": "2026-10-24T12:00:00Z"}],
            "ticket_structures": [{"provider": "bet9ja", "row_id": key, "raw_value": "single",
                "selected_systems": None, "structure": "single", "evidence": "Synthetic hand-checked single corpus",
                "settlement_policy": "ordinary_regulation_no_promotions"} for key in self.keys]}
        self.path = self.root / "config.json"
        self.save_settings()
        self.config = ResultsApiConfig(root=self.root / "results", identity_config_path=self.path,
            network_enabled=False, tolerate_incomplete_fetch=True)
        self.store = JobStore(self.config.jobs_root())

    def save_settings(self):
        self.path.write_text(json.dumps(self.settings))

    def check(self, row_id=None):
        def no_network():
            self.fail("offline CSV evaluation must never construct a network client")
        job = submit_evaluation(self.registry, self.config, self.store, "run-1",
            row_id=row_id, client_factory=no_network)
        final = _wait_for_terminal(self.store, job.job_id)
        self.assertEqual(final.state, JobState.SUCCEEDED, final.error)
        summary = get_latest_report(self.config, "run-1")
        return summary, list_codes(self.config, "run-1", summary["revision_id"], limit=20)

    def test_one_then_all_keeps_population_and_explains_every_outcome(self):
        summary, page = self.check(self.keys[0])
        self.assertEqual(summary["counts"]["total"], 5)
        self.assertEqual(summary["counts"]["won"], 1)
        self.assertEqual(summary["checked_count"], 1)
        self.assertEqual(summary["percentages"]["confirmed_win_rate"]["value"], 0.2)
        self.assertEqual(sum(t["reason"] == "not_checked" for t in page["rows"]), 4)
        leg = page["rows"][0]["legs"][0]
        self.assertEqual(leg["fixture"]["score"]["home"], 2)
        self.assertEqual(leg["selection"]["pick"], "1")
        summary, page = self.check()
        self.assertEqual(summary["counts"], dict(total=5, won=1, lost=1, void=1, pending=1, unresolved=1))
        self.assertEqual(summary["checked_count"], 5)
        winners = list_codes(self.config, "run-1", summary["revision_id"], status="won")
        self.assertEqual(winners["total"], 5)
        self.assertEqual(winners["filtered_total"], 1)
        self.assertEqual(winners["rows"][0]["ticket_id"], "WIN")

    def test_concurrent_single_checks_merge_checked_membership(self):
        jobs = [submit_evaluation(self.registry, self.config, self.store, "run-1", row_id=k)
                for k in self.keys[:2]]
        for job in jobs:
            final = _wait_for_terminal(self.store, job.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED, final.error)
        summary = get_latest_report(self.config, "run-1")
        self.assertEqual(summary["checked_count"], 2)
        self.assertEqual(summary["counts"]["won"], 1)
        self.assertEqual(summary["counts"]["lost"], 1)

    def test_complete_replay_after_sources_config_cache_and_registry_removed(self):
        summary, _ = self.check()
        revision = self.config.revisions_root("run-1") / summary["revision_id"]
        evidence = json.loads((revision / "evidence.json").read_bytes())
        audit = json.loads((revision / "audit_population.json").read_bytes())
        report = json.loads((revision / "report.json").read_bytes())
        self.csv.unlink(); self.path.unlink()
        shutil.rmtree(self.config.cache_root(), ignore_errors=True)
        self.registry._raw = None
        self.assertEqual(evaluate_run(evidence, audit_rows=audit), report)
        source = next(s for s in evidence["raw_sources"] if s["kind"] == "original_extract")
        self.assertEqual(len(json.loads(base64.b64decode(source["base64"]))["qualifying"]), 5)

    def test_unverified_structures_never_promote_known_winning_legs(self):
        self.settings["ticket_structures"] = []
        self.save_settings()
        summary, page = self.check()
        self.assertEqual(summary["counts"]["won"], 0)
        self.assertEqual(page["rows"][0]["legs"][0]["status"], "won")
        self.assertEqual(page["rows"][0]["reason"], "unverified_ticket_structure")

    def test_structure_attestation_is_bound_to_frozen_row_and_provider_fields(self):
        self.settings["ticket_structures"][0]["selected_systems"] = [[1]]
        self.save_settings()
        _, page = self.check(self.keys[0])
        self.assertEqual(page["rows"][0]["status"], "unresolved")

    def test_raw_source_tampering_is_rejected_before_replay(self):
        summary, _ = self.check()
        revision = self.config.revisions_root("run-1") / summary["revision_id"]
        evidence = json.loads((revision / "evidence.json").read_bytes())
        evidence["raw_sources"][0]["base64"] = base64.b64encode(b"tampered").decode()
        with self.assertRaisesRegex(EvaluationInputError, "digest mismatch"):
            evaluate_run(evidence)

    def test_saved_pages_include_unchecked_and_reject_changed_population(self):
        page = list_saved_codes(self.registry, "run-1", offset=1, limit=2)
        self.assertEqual(page["total"], 5)
        self.assertEqual(len(page["rows"]), 2)
        self.assertEqual(page["rows"][0]["ticket_id"], "LOSS")
        self.assertFalse(page["rows"][0]["checked"])
        self.assertEqual(list_saved_codes(self.registry, "run-1", status="won")["filtered_total"], 0)
        with self.assertRaisesRegex(ResultsApiError, "changed"):
            list_saved_codes(self.registry, "run-1", snapshot="wrong")

    def test_unknown_row_cannot_start_a_subset_job(self):
        with self.assertRaisesRegex(ResultsApiError, "row_id"):
            submit_evaluation(self.registry, self.config, self.store, "run-1", row_id="wrong")

    def test_csv_edits_change_job_configuration_identity(self):
        before = compute_config_digest(self.config)
        self.csv.write_text(self.csv.read_text().replace(",2,0,1,0", ",3,0,1,0"))
        self.assertNotEqual(compute_config_digest(self.config), before)

    def test_duplicate_source_fixture_import_is_rejected(self):
        self.settings["football_data_files"] *= 2
        self.save_settings()
        with self.assertRaisesRegex(ValueError, "overlapping"):
            freeze_local_sources(self.path)

    def test_utc_date_queries_cross_midnight_in_both_directions(self):
        for raw, expected in [("2026-10-08T00:30:00+02:00", "2026-10-07"),
                              ("2026-10-08T23:30:00-02:00", "2026-10-09")]:
            self.assertEqual(_dates_needed({"r": {"selections": [{"kickoff": raw}]}}), [expected])

    def test_only_explicit_date_access_errors_are_classified_as_scoped(self):
        self.assertIsInstance(_classify_api_errors({"plan":
            "Free plans do not have access to this date, try from 2026-10-23 to 2026-10-25."}), ApiFootballAccessError)
        self.assertNotIsInstance(_classify_api_errors({"plan": "Unexpected schema"}), ApiFootballAccessError)

    def test_restricted_date_preserves_accessible_results_and_survives_warm_cache(self):
        self.settings["football_data_files"] = []
        self.settings["network_enabled"] = True
        for alias in self.settings["aliases"]:
            alias["namespace"] = alias["namespace"].replace("football_data", "api_football")
        self.save_settings()
        config = replace(self.config, network_enabled=True)
        class Client:
            def get_fixtures_by_date(self, date):
                if date == "2026-10-08":
                    raise ApiFootballAccessError("Free plans do not have access to this date")
                return [_raw_fixture(1, 39, "England top flight", "Arsenal", "Chelsea", "FT",
                    "2026-10-09T15:00:00+00:00", fulltime={"home": 2, "away": 0})]
        for _ in range(2):
            job = submit_evaluation(self.registry, config, self.store, "run-1", client_factory=Client)
            final = _wait_for_terminal(self.store, job.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED, final.error)
            summary = get_latest_report(config, "run-1")
            self.assertEqual(summary["counts"]["total"], 5)
            self.assertEqual(summary["counts"]["won"], 1)
            page = list_codes(config, "run-1", summary["revision_id"])
            self.assertEqual(page["rows"][0]["legs"][0]["reason"], "source_access_restricted")
            self.assertEqual(page["rows"][3]["status"], "won")

    def test_local_complete_result_does_not_spend_a_network_request(self):
        self.settings["network_enabled"] = True
        self.save_settings()
        config = replace(self.config, network_enabled=True)
        def forbidden():
            self.fail("a completed locally matched fixture must not need a client")
        job = submit_evaluation(self.registry, config, self.store, "run-1",
            row_id=self.keys[0], client_factory=forbidden)
        self.assertEqual(_wait_for_terminal(self.store, job.job_id).state, JobState.SUCCEEDED)
        self.assertEqual(get_latest_report(config, "run-1")["counts"]["won"], 1)

    def test_refreshing_one_code_does_not_refetch_other_checked_dates(self):
        self.settings["football_data_files"] = []
        self.settings["network_enabled"] = True
        for alias in self.settings["aliases"]:
            alias["namespace"] = alias["namespace"].replace("football_data", "api_football")
        self.save_settings()
        config = replace(self.config, network_enabled=True)
        calls = []
        class Client:
            def get_fixtures_by_date(self, date):
                calls.append(date)
                return [_raw_fixture(int(date[-2:]), 39, "England top flight", "Arsenal", "Chelsea", "FT",
                    date + "T15:00:00+00:00", fulltime={"home": 2, "away": 0})]
        first = submit_evaluation(self.registry, config, self.store, "run-1",
            row_id=self.keys[0], client_factory=Client)
        self.assertEqual(_wait_for_terminal(self.store, first.job_id).state, JobState.SUCCEEDED)
        self.assertEqual(calls, ["2026-10-08"])
        calls.clear()
        second = submit_evaluation(self.registry, config, self.store, "run-1",
            row_id=self.keys[3], refresh=True, client_factory=Client)
        self.assertEqual(_wait_for_terminal(self.store, second.job_id).state, JobState.SUCCEEDED)
        self.assertEqual(calls, ["2026-10-09"])
        summary = get_latest_report(config, "run-1")
        self.assertEqual(summary["checked_count"], 2)
        self.assertEqual(summary["counts"]["won"], 2)

    def test_malformed_selection_cannot_be_dropped_to_create_a_false_winner(self):
        data = json.loads(self.registry._raw)
        data["qualifying"][0]["selections"].append(None)
        data["qualifying"][0]["num_legs"] = 2
        self.registry = FakeRegistry(data)
        key = next(iter(load_population(self.registry, "run-1")[2]))
        self.settings["ticket_structures"][0].update(row_id=key, structure="ordinary_accumulator")
        self.save_settings()
        _, page = self.check(key)
        self.assertEqual(page["rows"][0]["status"], "unresolved")
        self.assertEqual(page["rows"][0]["reason"], "malformed_original_ticket")


if __name__ == "__main__":
    unittest.main()
