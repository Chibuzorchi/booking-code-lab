"""F9 step 5 piece 4: backend API orchestration (results_api.py).

No real network calls -- a FakeRegistry stands in for RunRegistry
(committed_extract_snapshot only) and a FakeApiFootballClient stands in for
ApiFootballClient (get_fixtures_by_date only), both injected explicitly.
"""
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portfolio.results_tracking.jobs import JobState, JobStore
from portfolio.results_tracking.results_api import (
    ReportNotFoundError, ResultsApiConfig, ResultsApiError, _build_audit_rows,
    _dates_needed, _fixture_to_evidence_dict, _ticket_to_evidence, cancel_job,
    compute_config_digest, get_latest_report, list_codes, load_population,
    publish_revision, submit_evaluation,
)
from portfolio.results_tracking.sources.api_football import build_fixture, filter_in_scope


class FakeRegistry:
    def __init__(self, extract: dict):
        self._raw = json.dumps(extract).encode("utf-8")

    def committed_extract_snapshot(self, run_id):
        return Path("fake-extract.json"), self._raw


def _extract(tickets):
    return {"provider": "bet9ja", "odds_basis": "parsed_leg_product", "qualifying": tickets}


def _ticket(code, event, league, market, pick, kickoff, sport="football"):
    return {"code": code, "num_legs": 1, "bet_type": "single",
            "selections": [{"event": event, "league": league, "market": market,
                             "pick": pick, "kickoff": kickoff, "sport": sport}]}


def _raw_fixture(fixture_id, league_id, league_name, home, away, status, date,
                  fulltime=None, halftime=None):
    fixture_json = {
        "fixture": {"id": fixture_id, "date": date, "status": {"short": status}},
        "league": {"id": league_id, "name": league_name, "season": 2026},
        "teams": {"home": {"name": home}, "away": {"name": away}},
    }
    if fulltime is not None:
        fixture_json["score"] = {"fulltime": fulltime, "halftime": halftime or {"home": None, "away": None}}
    return fixture_json


class FakeApiFootballClient:
    def __init__(self, fixtures_by_date: dict, *, delay: float = 0.0):
        self._fixtures_by_date = fixtures_by_date
        self._delay = delay
        self.calls = []
        self._lock = threading.Lock()

    def get_fixtures_by_date(self, date, league=None, season=None):
        if self._delay:
            time.sleep(self._delay)
        with self._lock:
            self.calls.append(date)
        return self._fixtures_by_date.get(date, [])


class LoadPopulationTests(unittest.TestCase):
    def test_builds_digest_keyed_row_ids(self):
        extract = _extract([_ticket("C1", "A - B", "England - Premier League", "1X2", "1",
                                     "2026-10-23T15:00:00")])
        registry = FakeRegistry(extract)
        provider, digest, rows = load_population(registry, "run-1")
        self.assertEqual(provider, "bet9ja")
        self.assertEqual(list(rows.keys()), [f"{digest}:0"])

    def test_rejects_non_list_qualifying(self):
        registry = FakeRegistry({"provider": "bet9ja", "qualifying": "not-a-list"})
        with self.assertRaises(ResultsApiError):
            load_population(registry, "run-1")

    def test_malformed_rows_are_preserved_not_dropped(self):
        # S5-02: a non-dict retained row must still occupy a row_id -- the
        # denominator never silently shrinks below the frozen source count.
        valid = _ticket("C1", "A - B", "England - Premier League", "1X2", "1",
                         "2026-10-23T15:00:00")
        registry = FakeRegistry(_extract([valid, None, 42]))
        provider, digest, rows = load_population(registry, "run-1")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[f"{digest}:0"], valid)
        self.assertIsNone(rows[f"{digest}:1"])
        self.assertEqual(rows[f"{digest}:2"], 42)


class BuildAuditRowsTests(unittest.TestCase):
    def test_malformed_rows_get_a_placeholder_entry(self):
        rows = {
            "r0": _ticket("C1", "A - B", "L", "1X2", "1", "2026-10-23T15:00:00"),
            "r1": None,
            "r2": 42,
        }
        audit_rows = _build_audit_rows("bet9ja", rows)
        self.assertEqual(len(audit_rows), 3)
        self.assertEqual(audit_rows["r1"],
                          {"code": None, "num_legs": None, "num_selections": 0,
                           "provider": None, "original_selections": []})
        self.assertEqual(audit_rows["r2"],
                          {"code": None, "num_legs": None, "num_selections": 0,
                           "provider": None, "original_selections": []})
        self.assertEqual(audit_rows["r0"]["provider"], "bet9ja")


class DatesNeededTests(unittest.TestCase):
    def test_extracts_distinct_sorted_dates(self):
        rows = {
            "r0": _ticket("C1", "A - B", "L", "1X2", "1", "2026-10-24T15:00:00"),
            "r1": _ticket("C2", "C - D", "L", "1X2", "1", "2026-10-23T12:00:00"),
        }
        self.assertEqual(_dates_needed(rows), ["2026-10-23", "2026-10-24"])

    def test_ignores_malformed_kickoff(self):
        rows = {"r0": _ticket("C1", "A - B", "L", "1X2", "1", "not-a-date")}
        self.assertEqual(_dates_needed(rows), [])

    def test_ignores_non_dict_rows(self):
        rows = {"r0": _ticket("C1", "A - B", "L", "1X2", "1", "2026-10-24T15:00:00"),
                "r1": None, "r2": 42}
        self.assertEqual(_dates_needed(rows), ["2026-10-24"])


class EvidenceConversionTests(unittest.TestCase):
    def test_ticket_to_evidence_shape(self):
        ticket = _ticket("C1", "A - B", "L", "1X2", "1", "2026-10-23T15:00:00")
        evidence = _ticket_to_evidence("digest:0", ticket, "bet9ja")
        self.assertEqual(evidence["ticket_id"], "C1")
        self.assertEqual(evidence["row_id"], "digest:0")
        self.assertEqual(len(evidence["legs"]), 1)
        leg = evidence["legs"][0]
        self.assertEqual(leg["event_display"], "A - B")
        self.assertEqual(leg["kickoff"]["raw"], "2026-10-23T15:00:00")
        self.assertEqual(leg["kickoff"]["precision"], "date_only")

    def test_fixture_to_evidence_dict_round_trips_through_main_parser(self):
        from portfolio.results_tracking.__main__ import _parse_fixture

        raw = _raw_fixture(1, 39, "Premier League", "Arsenal", "Chelsea", "FT",
                            "2026-10-23T15:00:00+00:00", fulltime={"home": 2, "away": 1})
        fixture = build_fixture(raw, fetched_at="2026-10-23T16:00:00+00:00")
        payload = _fixture_to_evidence_dict(fixture)
        round_tripped = _parse_fixture(payload, 0)
        self.assertEqual(round_tripped.home_team, "Arsenal")
        self.assertEqual(round_tripped.regulation_score.home, 2)
        self.assertEqual(round_tripped.state.value, "finished_regulation")


class RevisionPublicationTests(unittest.TestCase):
    def _report(self, total=1):
        return {"tickets": [{"ticket_id": "C1", "row_id": "r0", "status": "unresolved"}],
                "counts": {"won": 0, "lost": 0, "void": 0, "pending": 0, "unresolved": total, "total": total},
                "percentages": {}, "rule_versions": {}}

    def test_publish_then_get_latest_report(self):
        with TemporaryDirectory() as tmp:
            config = ResultsApiConfig(root=Path(tmp))
            report = self._report()
            revision_id = publish_revision(config, "run-1", {"fixtures": []}, report,
                                            input_digest="d1", config_digest="c1")
            fetched = get_latest_report(config, "run-1")
            self.assertEqual(fetched["revision_id"], revision_id)
            self.assertEqual(fetched["counts"]["total"], 1)

    def test_get_latest_report_raises_when_absent(self):
        with TemporaryDirectory() as tmp:
            config = ResultsApiConfig(root=Path(tmp))
            with self.assertRaises(ReportNotFoundError):
                get_latest_report(config, "run-missing")

    def test_second_publish_becomes_latest(self):
        with TemporaryDirectory() as tmp:
            config = ResultsApiConfig(root=Path(tmp))
            publish_revision(config, "run-1", {"fixtures": []}, self._report(),
                              input_digest="d1", config_digest="c1")
            second = publish_revision(config, "run-1", {"fixtures": []}, self._report(total=2),
                                       input_digest="d1", config_digest="c1")
            fetched = get_latest_report(config, "run-1")
            self.assertEqual(fetched["revision_id"], second)
            self.assertEqual(fetched["counts"]["total"], 2)

    def test_list_codes_pagination(self):
        with TemporaryDirectory() as tmp:
            config = ResultsApiConfig(root=Path(tmp))
            report = {"tickets": [{"row_id": f"r{i}"} for i in range(5)],
                      "counts": {"won": 0, "lost": 0, "void": 0, "pending": 0, "unresolved": 5, "total": 5},
                      "percentages": {}, "rule_versions": {}}
            revision_id = publish_revision(config, "run-1", {"fixtures": []}, report,
                                            input_digest="d1", config_digest="c1")
            page = list_codes(config, "run-1", revision_id, offset=2, limit=2)
            self.assertEqual(page["total"], 5)
            self.assertEqual([r["row_id"] for r in page["rows"]], ["r2", "r3"])

    def test_list_codes_limit_is_bounded(self):
        with TemporaryDirectory() as tmp:
            config = ResultsApiConfig(root=Path(tmp))
            report = {"tickets": [{"row_id": f"r{i}"} for i in range(300)],
                      "counts": {"won": 0, "lost": 0, "void": 0, "pending": 0, "unresolved": 300, "total": 300},
                      "percentages": {}, "rule_versions": {}}
            revision_id = publish_revision(config, "run-1", {"fixtures": []}, report,
                                            input_digest="d1", config_digest="c1")
            page = list_codes(config, "run-1", revision_id, offset=0, limit=10_000)
            self.assertEqual(len(page["rows"]), 200)

    def test_list_codes_raises_for_unknown_revision(self):
        with TemporaryDirectory() as tmp:
            config = ResultsApiConfig(root=Path(tmp))
            with self.assertRaises(ReportNotFoundError):
                list_codes(config, "run-1", "rev-does-not-exist")


def _wait_for_terminal(job_store, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = job_store.get(job_id)
        if record.state in ("succeeded", "failed", "cancelled"):
            return record
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal state within {timeout}s")


class SubmitEvaluationTests(unittest.TestCase):
    def test_end_to_end_with_no_aliases_publishes_a_revision_with_honest_unresolved_outcomes(self):
        with TemporaryDirectory() as tmp:
            registry = FakeRegistry(_extract([
                _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                        "2026-10-23T15:00:00"),
            ]))
            config = ResultsApiConfig(root=Path(tmp) / "results_api")
            job_store = JobStore(Path(tmp) / "jobs")
            client = FakeApiFootballClient({
                "2026-10-23": [_raw_fixture(1, 39, "Premier League", "Arsenal", "Chelsea", "FT",
                                             "2026-10-23T15:00:00+00:00", fulltime={"home": 2, "away": 1})],
            })

            record = submit_evaluation(registry, config, job_store, "run-1",
                                        client_factory=lambda: client)
            final = _wait_for_terminal(job_store, record.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED)
            self.assertEqual(client.calls, ["2026-10-23"])

            report = get_latest_report(config, "run-1")
            self.assertEqual(report["counts"]["total"], 1)
            # No aliases/verified mappings were supplied, so matching can
            # only ever reach an honest unresolved outcome here, never a
            # silently-wrong match (matching.py's fuzzy tier never MATCHes).
            self.assertEqual(report["counts"]["unresolved"], 1)

    def test_concurrent_submissions_for_the_same_run_share_one_job_and_one_fetch(self):
        with TemporaryDirectory() as tmp:
            registry = FakeRegistry(_extract([
                _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                        "2026-10-23T15:00:00"),
            ]))
            config = ResultsApiConfig(root=Path(tmp) / "results_api")
            job_store = JobStore(Path(tmp) / "jobs")
            client = FakeApiFootballClient({"2026-10-23": []}, delay=0.1)

            first = submit_evaluation(registry, config, job_store, "run-1", client_factory=lambda: client)
            second = submit_evaluation(registry, config, job_store, "run-1", client_factory=lambda: client)
            self.assertEqual(first.job_id, second.job_id)

            final = _wait_for_terminal(job_store, first.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED)
            self.assertEqual(client.calls, ["2026-10-23"])  # fetched once, not twice

    def test_no_dates_needed_still_publishes_a_report_for_every_row(self):
        with TemporaryDirectory() as tmp:
            registry = FakeRegistry(_extract([
                _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1", "not-a-date"),
            ]))
            config = ResultsApiConfig(root=Path(tmp) / "results_api")
            job_store = JobStore(Path(tmp) / "jobs")
            client = FakeApiFootballClient({})

            record = submit_evaluation(registry, config, job_store, "run-1", client_factory=lambda: client)
            final = _wait_for_terminal(job_store, record.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED)
            self.assertEqual(client.calls, [])
            report = get_latest_report(config, "run-1")
            self.assertEqual(report["counts"]["total"], 1)

    def test_malformed_rows_keep_their_place_in_the_denominator(self):
        # S5-02 repro: [valid_ticket, null, 42] must produce N=3, with the
        # two malformed rows surfacing as explicit not_supplied_in_evidence
        # unresolved tickets rather than silently vanishing from the report.
        with TemporaryDirectory() as tmp:
            valid = _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                             "2026-10-23T15:00:00")
            registry = FakeRegistry(_extract([valid, None, 42]))
            config = ResultsApiConfig(root=Path(tmp) / "results_api")
            job_store = JobStore(Path(tmp) / "jobs")
            client = FakeApiFootballClient({
                "2026-10-23": [_raw_fixture(1, 39, "Premier League", "Arsenal", "Chelsea", "FT",
                                             "2026-10-23T15:00:00+00:00", fulltime={"home": 2, "away": 1})],
            })

            record = submit_evaluation(registry, config, job_store, "run-1",
                                        client_factory=lambda: client)
            final = _wait_for_terminal(job_store, record.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED)

            report = get_latest_report(config, "run-1")
            self.assertEqual(report["counts"]["total"], 3)
            codes = list_codes(config, "run-1", report["revision_id"], limit=10)
            not_supplied = [t for t in codes["rows"] if t["reason"] == "not_supplied_in_evidence"]
            self.assertEqual(len(not_supplied), 2)
            for ticket in not_supplied:
                self.assertEqual(ticket["status"], "unresolved")
                self.assertIsNone(ticket["ticket_id"])


class CancelJobTests(unittest.TestCase):
    def test_cancel_propagates_to_recorded_fetch_job_id(self):
        with TemporaryDirectory() as tmp:
            job_store = JobStore(Path(tmp))
            outer = job_store.create_or_reuse("run-1", "input-a", "config-a")
            inner = job_store.create_or_reuse("run-1:fetch", "input-a", "config-a")
            job_store.update(outer.job_id, lambda r: r["progress"].update(fetch_job_id=inner.job_id))

            cancel_job(job_store, outer.job_id)

            self.assertTrue(job_store.get(outer.job_id).cancel_requested)
            self.assertTrue(job_store.get(inner.job_id).cancel_requested)

    def test_cancel_is_a_noop_when_no_fetch_job_was_recorded(self):
        with TemporaryDirectory() as tmp:
            job_store = JobStore(Path(tmp))
            outer = job_store.create_or_reuse("run-1", "input-a", "config-a")
            record = cancel_job(job_store, outer.job_id)
            self.assertTrue(record.cancel_requested)


if __name__ == "__main__":
    unittest.main()
