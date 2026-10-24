"""F9 step 5 piece 4: backend API orchestration (results_api.py).

No real network calls -- a FakeRegistry stands in for RunRegistry
(committed_extract_snapshot only) and a FakeApiFootballClient stands in for
ApiFootballClient (get_fixtures_by_date only), both injected explicitly.
"""
import json
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from tempfile import TemporaryDirectory

from portfolio.registry import RunStateError
from portfolio.results_tracking import __main__ as results_cli
from portfolio.results_tracking.jobs import JobState, JobStore
from portfolio.results_tracking.cache import FixtureCache
from portfolio.results_tracking.results_api import (
    _ABANDONED_JOB_AFTER_SECONDS,
    ReportNotFoundError, ResultsApiConfig, ResultsApiError, _build_audit_rows,
    _cache_key_for_date, _commit_publication, _dates_needed, _default_client_factory,
    _fixture_to_evidence_dict, _ticket_to_evidence, cancel_job,
    compute_config_digest, get_latest_report, list_codes, load_identity_evidence,
    load_population, publish_revision, submit_evaluation,
)
from portfolio.results_tracking.sources.api_football import build_fixture, filter_in_scope


class FakeRegistry:
    def __init__(self, extract: dict | None = None, *, checkpoint: dict | None = None,
                 succeeded: bool = True):
        self._raw = json.dumps(extract).encode("utf-8") if extract is not None else None
        self._checkpoint_raw = json.dumps(checkpoint).encode("utf-8") if checkpoint is not None else None
        self._succeeded = succeeded

    def committed_extract_snapshot(self, run_id):
        if not self._succeeded or self._raw is None:
            raise RunStateError(f"run {run_id} has no committed extract")
        return Path("fake-extract.json"), self._raw

    def saved_codes_snapshot(self, run_id):
        if self._succeeded:
            return Path("fake-extract.json"), self._raw, False
        if self._checkpoint_raw is None:
            raise RunStateError(f"run {run_id} has no saved codes")
        return Path("fake-checkpoint.json"), self._checkpoint_raw, True


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
        provider, digest, rows, population_complete = load_population(registry, "run-1")
        self.assertEqual(provider, "bet9ja")
        self.assertEqual(list(rows.keys()), [f"{digest}:0"])
        self.assertTrue(population_complete)

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
        provider, digest, rows, population_complete = load_population(registry, "run-1")
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


class LoadIdentityEvidenceTests(unittest.TestCase):
    def test_returns_empty_lists_when_unconfigured(self):
        config = ResultsApiConfig(root=Path("/nonexistent"))
        self.assertEqual(load_identity_evidence(config), ([], []))

    def test_reads_aliases_and_verified_mappings_from_the_configured_file(self):
        with TemporaryDirectory() as tmp:
            identity_path = Path(tmp) / "identity.json"
            identity_path.write_text(json.dumps({
                "aliases": [{"namespace": "bet9ja_team", "display_name": "Man Utd", "canonical": "Manchester United"}],
                "verified_mappings": [],
            }), encoding="utf-8")
            config = ResultsApiConfig(root=Path(tmp), identity_config_path=identity_path)
            aliases, verified_mappings = load_identity_evidence(config)
            self.assertEqual(len(aliases), 1)
            self.assertEqual(verified_mappings, [])

    def test_rejects_a_non_object_identity_config(self):
        with TemporaryDirectory() as tmp:
            identity_path = Path(tmp) / "identity.json"
            identity_path.write_text("[]", encoding="utf-8")
            config = ResultsApiConfig(root=Path(tmp), identity_config_path=identity_path)
            with self.assertRaises(ResultsApiError):
                load_identity_evidence(config)

    def test_config_digest_changes_when_identity_config_content_changes(self):
        with TemporaryDirectory() as tmp:
            identity_path = Path(tmp) / "identity.json"
            identity_path.write_text(json.dumps({"aliases": [], "verified_mappings": []}), encoding="utf-8")
            config = ResultsApiConfig(root=Path(tmp), identity_config_path=identity_path)
            digest_before = compute_config_digest(config)
            identity_path.write_text(json.dumps({"aliases": [{"namespace": "bet9ja_team",
                                                                "display_name": "A", "canonical": "B"}],
                                                   "verified_mappings": []}), encoding="utf-8")
            digest_after = compute_config_digest(config)
            self.assertNotEqual(digest_before, digest_after)


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

    def test_preserves_specifier_and_native_event_id(self):
        ticket = {"code": "C1", "num_legs": 1, "bet_type": "single",
                  "selections": [{"event": "A - B", "league": "L", "market": "over_under",
                                   "pick": "Over", "specifier": "2.5", "kickoff": "2026-10-23T15:00:00",
                                   "sport": "football", "native_event_id": "1001"}]}
        leg = _ticket_to_evidence("digest:0", ticket, "bet9ja")["legs"][0]
        self.assertEqual(leg["specifier"], "2.5")
        self.assertEqual(leg["provider_event_id"], "1001")

    def test_falls_back_to_legacy_event_id_when_native_event_id_is_absent(self):
        ticket = _ticket("C1", "A - B", "L", "1X2", "1", "2026-10-23T15:00:00")
        ticket["selections"][0]["event_id"] = 1001
        leg = _ticket_to_evidence("digest:0", ticket, "bet9ja")["legs"][0]
        self.assertEqual(leg["provider_event_id"], "1001")

    def test_offset_aware_kickoff_gets_exact_precision(self):
        ticket = _ticket("C1", "A - B", "L", "1X2", "1", "2026-10-23T15:00:00+00:00")
        leg = _ticket_to_evidence("digest:0", ticket, "bet9ja")["legs"][0]
        self.assertEqual(leg["kickoff"]["precision"], "exact")
        self.assertEqual(leg["kickoff"]["utc"], "2026-10-23T15:00:00+00:00")

    def test_naive_kickoff_stays_date_only(self):
        ticket = _ticket("C1", "A - B", "L", "1X2", "1", "2026-10-23T15:00:00")
        leg = _ticket_to_evidence("digest:0", ticket, "bet9ja")["legs"][0]
        self.assertEqual(leg["kickoff"]["precision"], "date_only")
        self.assertIsNone(leg["kickoff"]["utc"])

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


def _force_job_record(job_store, job_id, **fields):
    """Rewrites a job's persisted record directly, bypassing
    JobStore.update's terminal-state guard and "now" stamping -- used only
    to simulate a worker-less job left mid-run by a server restart."""
    status_path = job_store._status_path(job_id)
    record = json.loads(status_path.read_text(encoding="utf-8"))
    record.update(fields)
    status_path.write_text(json.dumps(record), encoding="utf-8")


def _backdate_job(job_store, job_id, seconds_ago):
    stale = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    _force_job_record(job_store, job_id, updated_at=stale.isoformat())


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

    def test_a_worker_less_running_job_is_reclaimed_and_retried_on_resubmission(self):
        # S5-03 repro: persist a RUNNING job for this identity with no
        # worker thread behind it (simulating a server restart) and verify
        # resubmission does not just hand that dead job back forever.
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

            abandoned = submit_evaluation(registry, config, job_store, "run-1",
                                           client_factory=lambda: client)
            _wait_for_terminal(job_store, abandoned.job_id)
            # Force it back to a never-finishing RUNNING state with a
            # stale heartbeat, as if its worker thread vanished mid-run.
            stale = datetime.now(timezone.utc) - timedelta(seconds=_ABANDONED_JOB_AFTER_SECONDS + 1)
            _force_job_record(job_store, abandoned.job_id,
                               state=JobState.RUNNING, updated_at=stale.isoformat())
            self.assertEqual(job_store.get(abandoned.job_id).state, JobState.RUNNING)

            retried = submit_evaluation(registry, config, job_store, "run-1",
                                         client_factory=lambda: client)
            self.assertNotEqual(retried.job_id, abandoned.job_id)
            final = _wait_for_terminal(job_store, retried.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED)

            orphaned = job_store.get(abandoned.job_id)
            self.assertEqual(orphaned.state, JobState.FAILED)
            self.assertEqual(orphaned.error["code"], "orphaned_worker")

    def test_a_late_cancellation_lands_as_cancelled_not_succeeded(self):
        # S5-04 repro: cancellation requested after fetch finishes but
        # before the job's terminal state is committed must not be
        # silently ignored by an unconditional SUCCEEDED write.
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
            }, delay=0.1)

            record = submit_evaluation(registry, config, job_store, "run-1", client_factory=lambda: client)

            from portfolio.results_tracking.__main__ import evaluate_run as real_evaluate_run

            def _evaluate_then_cancel(evidence, audit_rows=None):
                # Lands the cancellation request exactly inside the S5-04
                # race window: after fetch has completed, before this
                # job's terminal state is written.
                cancel_job(job_store, record.job_id)
                return real_evaluate_run(evidence, audit_rows=audit_rows)

            with mock.patch("portfolio.results_tracking.results_api.evaluate_run",
                             side_effect=_evaluate_then_cancel):
                final = _wait_for_terminal(job_store, record.job_id)

            self.assertEqual(final.state, JobState.CANCELLED)
            # The revision was still published -- discoverable evidence is
            # never thrown away just because a cancellation arrived too
            # late to stop it (jobs.py's own cancellation contract).
            report = get_latest_report(config, "run-1")
            self.assertEqual(final.result["revision_id"], report["revision_id"])

    def test_an_empty_fixture_response_is_cached_as_pending_not_terminal(self):
        # S5-09 repro: a date with no in-scope fixtures returned yet must
        # get the short pending refresh window, not the 24h terminal one
        # -- an empty response is "nothing published yet", not a confirmed
        # final state.
        with TemporaryDirectory() as tmp:
            registry = FakeRegistry(_extract([
                _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                        "2026-10-23T15:00:00"),
            ]))
            config = ResultsApiConfig(root=Path(tmp) / "results_api")
            job_store = JobStore(Path(tmp) / "jobs")
            client = FakeApiFootballClient({"2026-10-23": []})

            record = submit_evaluation(registry, config, job_store, "run-1", client_factory=lambda: client)
            _wait_for_terminal(job_store, record.job_id)

            cache = FixtureCache(config.cache_root())
            entry = cache.get(_cache_key_for_date("2026-10-23"))
            self.assertIsNotNone(entry)
            self.assertFalse(entry.all_terminal)

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

    def test_a_verified_mapping_produces_a_real_matched_leg_through_the_http_path(self):
        # S5-01: with a server-side identity config supplying a hand-verified
        # mapping for this exact native event id, the HTTP path must reach a
        # real MATCHED leg with the correct won/lost outcome, not merely an
        # honest unresolved/fixture_not_matched -- the review's prior repro.
        # Ticket-level status stays unresolved/unverified_ticket_structure
        # regardless (verify_bet_type is always "unverified" -- no documented
        # provider bet-structure mapping exists, provider_types.py); that is
        # unrelated, pre-existing, intentional behavior this fix does not
        # touch, so the assertion is on the leg, not report["counts"].
        with TemporaryDirectory() as tmp:
            ticket = {"code": "C1", "num_legs": 1, "bet_type": "single",
                      "selections": [{"event": "Arsenal - Chelsea", "league": "England - Premier League",
                                       "market": "1X2", "pick": "1", "kickoff": "2026-10-23T15:00:00",
                                       "sport": "Soccer", "native_event_id": "1001"}]}
            registry = FakeRegistry(_extract([ticket]))
            identity_path = Path(tmp) / "identity.json"
            identity_path.write_text(json.dumps({
                "aliases": [],
                "verified_mappings": [{
                    "provider": "bet9ja", "provider_event_id": "1001",
                    "league_display": "England - Premier League",
                    "home_display": "Arsenal", "away_display": "Chelsea",
                    "source": "api_football", "source_fixture_id": "1",
                    "source_competition": "Premier League",
                    "source_home_team": "Arsenal", "source_away_team": "Chelsea",
                    "source_kickoff_raw": "2026-10-23T15:00:00+00:00",
                    "verified_at": "2026-10-23T00:00:00+00:00",
                    "evidence": "hand-verified for this test",
                }],
            }), encoding="utf-8")
            config = ResultsApiConfig(root=Path(tmp) / "results_api", identity_config_path=identity_path)
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
            codes = list_codes(config, "run-1", report["revision_id"], limit=10)
            leg = codes["rows"][0]["legs"][0]
            self.assertEqual(leg["status"], "won")
            self.assertEqual(leg["match_tier"], "verified_mapping")
            self.assertEqual(leg["matched_fixture"], {"source": "api_football", "source_fixture_id": "1"})

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

    def test_a_published_revision_replays_through_the_cli_with_row_id_intact(self):
        # S5-05 repro: an HTTP-produced revision directory must itself be a
        # valid `evaluate --replay` target, with row_id preserved -- not the
        # null row_id the review observed when only evidence.json/report.json
        # were archived (no audit_population.json for --replay to recover
        # the audited population from).
        with TemporaryDirectory() as tmp:
            valid = _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                             "2026-10-23T15:00:00")
            registry = FakeRegistry(_extract([valid]))
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
            rev_dir = config.revisions_root("run-1") / report["revision_id"]
            self.assertTrue((rev_dir / "audit_population.json").exists())

            replay_out = Path(tmp) / "replayed"
            exit_code = results_cli.main(["evaluate", "--replay", str(rev_dir), "--output", str(replay_out)])
            self.assertEqual(exit_code, 0)
            replayed_report = json.loads((replay_out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(replayed_report["tickets"]), 1)
            self.assertIsNotNone(replayed_report["tickets"][0]["row_id"])

    def test_a_non_succeeded_run_without_allow_partial_is_refused(self):
        # F9_RESULTS_PLAN.md: only a succeeded run's committed extract is
        # ever read unless the caller explicitly opts into the partial
        # frozen-checkpoint mode.
        with TemporaryDirectory() as tmp:
            registry = FakeRegistry(succeeded=False, checkpoint=_extract([
                _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                        "2026-10-23T15:00:00"),
            ]))
            config = ResultsApiConfig(root=Path(tmp) / "results_api")
            job_store = JobStore(Path(tmp) / "jobs")
            with self.assertRaises(RunStateError):
                submit_evaluation(registry, config, job_store, "run-1",
                                   client_factory=lambda: FakeApiFootballClient({}))

    def test_allow_partial_evaluates_a_frozen_checkpoint_as_population_incomplete(self):
        # S5-10: a cancelled/failed run with a saved checkpoint can still be
        # evaluated under allow_partial=True, with the published report
        # honestly marked population_complete=False rather than reading as
        # a full-run claim.
        with TemporaryDirectory() as tmp:
            registry = FakeRegistry(succeeded=False, checkpoint=_extract([
                _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                        "2026-10-23T15:00:00"),
            ]))
            config = ResultsApiConfig(root=Path(tmp) / "results_api")
            job_store = JobStore(Path(tmp) / "jobs")
            client = FakeApiFootballClient({
                "2026-10-23": [_raw_fixture(1, 39, "Premier League", "Arsenal", "Chelsea", "FT",
                                             "2026-10-23T15:00:00+00:00", fulltime={"home": 2, "away": 1})],
            })

            record = submit_evaluation(registry, config, job_store, "run-1", allow_partial=True,
                                        client_factory=lambda: client)
            final = _wait_for_terminal(job_store, record.job_id)
            self.assertEqual(final.state, JobState.SUCCEEDED)
            report = get_latest_report(config, "run-1")
            self.assertFalse(report["population_complete"])

    def test_allow_partial_changes_job_identity_even_with_the_same_content(self):
        # Review: "include behavior-changing options in job identity" --
        # a frozen-checkpoint evaluation and a committed-extract evaluation
        # of the same bytes must never silently share a job record, since
        # they carry different population_complete guarantees.
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

            without_partial = submit_evaluation(registry, config, job_store, "run-1",
                                                  client_factory=lambda: client)
            _wait_for_terminal(job_store, without_partial.job_id)
            with_partial = submit_evaluation(registry, config, job_store, "run-1", allow_partial=True,
                                               client_factory=lambda: client)
            self.assertNotEqual(without_partial.job_id, with_partial.job_id)
            _wait_for_terminal(job_store, with_partial.job_id)

    def test_refresh_forces_a_refetch_past_a_still_fresh_terminal_cache_entry(self):
        # S5-10: refresh=True bypasses the normal staleness check so a
        # forced re-fetch happens even for a date whose cached entry is
        # still well within its freshness window.
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

            first = submit_evaluation(registry, config, job_store, "run-1", client_factory=lambda: client)
            _wait_for_terminal(job_store, first.job_id)
            self.assertEqual(client.calls, ["2026-10-23"])

            # Same identity, already terminal: a plain resubmission mints a
            # fresh job but the still-fresh/terminal cache entry means the
            # source is not actually re-hit.
            again = submit_evaluation(registry, config, job_store, "run-1", client_factory=lambda: client)
            self.assertNotEqual(first.job_id, again.job_id)
            _wait_for_terminal(job_store, again.job_id)
            self.assertEqual(client.calls, ["2026-10-23"])

            refreshed = submit_evaluation(registry, config, job_store, "run-1", refresh=True,
                                           client_factory=lambda: client)
            _wait_for_terminal(job_store, refreshed.job_id)
            self.assertEqual(client.calls, ["2026-10-23", "2026-10-23"])

    def test_tolerate_incomplete_fetch_config_setting_controls_publish_on_incomplete_dates(self):
        # S5-10: the server-side config setting -- not the client-supplied
        # allow_partial flag -- is what decides whether an incomplete
        # source fetch can still publish.
        with TemporaryDirectory() as tmp:
            registry = FakeRegistry(_extract([
                _ticket("C1", "Arsenal - Chelsea", "England - Premier League", "1X2", "1",
                        "2026-10-23T15:00:00"),
                _ticket("C2", "Leeds - Burnley", "England - Championship", "1X2", "1",
                        "2026-10-24T15:00:00"),
            ]))
            client = FakeApiFootballClient({"2026-10-23": [], "2026-10-24": []})
            tight_budget = ResultsApiConfig(root=Path(tmp) / "strict", max_requests_per_job=1,
                                             max_retries=0)
            job_store = JobStore(Path(tmp) / "jobs-strict")
            record = submit_evaluation(registry, tight_budget, job_store, "run-1",
                                        client_factory=lambda: client)
            final = _wait_for_terminal(job_store, record.job_id)
            self.assertEqual(final.state, JobState.FAILED)
            self.assertEqual(final.error["code"], "incomplete_fetch")

            tolerant = ResultsApiConfig(root=Path(tmp) / "tolerant", max_requests_per_job=1,
                                         max_retries=0, tolerate_incomplete_fetch=True)
            job_store2 = JobStore(Path(tmp) / "jobs-tolerant")
            record2 = submit_evaluation(registry, tolerant, job_store2, "run-1",
                                         client_factory=lambda: client)
            final2 = _wait_for_terminal(job_store2, record2.job_id)
            self.assertEqual(final2.state, JobState.SUCCEEDED)
            self.assertEqual(len(final2.result["incomplete_dates"]), 1)


class CommitPublicationTests(unittest.TestCase):
    def test_commits_succeeded_when_no_cancellation_was_requested(self):
        with TemporaryDirectory() as tmp:
            job_store = JobStore(Path(tmp))
            outer = job_store.create_or_reuse("run-1", "input-a", "config-a")
            _commit_publication(job_store, outer.job_id, {"revision_id": "r1"})
            record = job_store.get(outer.job_id)
            self.assertEqual(record.state, JobState.SUCCEEDED)
            self.assertEqual(record.result, {"revision_id": "r1"})

    def test_commits_cancelled_when_cancellation_was_requested(self):
        # S5-04: the decision reads cancel_requested in the very same
        # update() call that writes the terminal state -- no separate,
        # earlier read that a late request could race past.
        with TemporaryDirectory() as tmp:
            job_store = JobStore(Path(tmp))
            outer = job_store.create_or_reuse("run-1", "input-a", "config-a")
            job_store.request_cancel(outer.job_id)
            _commit_publication(job_store, outer.job_id, {"revision_id": "r1"})
            record = job_store.get(outer.job_id)
            self.assertEqual(record.state, JobState.CANCELLED)
            # The published revision is still recorded, not discarded.
            self.assertEqual(record.result, {"revision_id": "r1"})


class DefaultClientFactoryTests(unittest.TestCase):
    def test_binds_the_configured_per_call_timeout_into_the_real_client(self):
        # S5-08: the default client previously kept its own independent
        # 10-second transport timeout regardless of config.per_call_timeout.
        with mock.patch.dict("os.environ", {"API_FOOTBALL_KEY": "test-key"}):
            config = ResultsApiConfig(root=Path("/unused"), per_call_timeout=3.5)
            client = _default_client_factory(config)()
            self.assertEqual(client.timeout_s, 3.5)


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

    def test_cancelling_an_abandoned_job_resolves_it_instead_of_hanging(self):
        # S5-03: cancelling a job with no live worker left (the review's
        # "cancellation also has no worker to settle that abandoned
        # record") must resolve it to terminal, not just flip a flag
        # nothing will ever check again.
        with TemporaryDirectory() as tmp:
            job_store = JobStore(Path(tmp))
            outer = job_store.create_or_reuse("run-1", "input-a", "config-a")
            job_store.update(outer.job_id, lambda r: r.update(state=JobState.RUNNING))
            _backdate_job(job_store, outer.job_id, _ABANDONED_JOB_AFTER_SECONDS + 1)

            record = cancel_job(job_store, outer.job_id)

            self.assertEqual(record.state, JobState.FAILED)
            self.assertEqual(record.error["code"], "orphaned_worker")


if __name__ == "__main__":
    unittest.main()
