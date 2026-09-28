"""F5 run-registry tests: durable status, commit, cancel, and recovery over
real subprocess boundaries. Fixture providers mirror the real scan_code CLI
contract and write structured, throttled progress.json exactly like the real
scanner. No provider traffic.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from portfolio.registry import (
    LAUNCH_PAUSE_ENV,
    RunNotFoundError,
    RunRegistry,
    RunStateError,
    STATE_CANCELLED,
    STATE_CANCELLED_UNRESOLVED,
    STATE_FAILED,
    STATE_LAUNCH_FAILED,
    STATE_SUCCEEDED,
    STATE_UNKNOWN,
)
from portfolio.worker import SUPERVISOR_PAUSE_ENV, JobBusyError, WorkerHost

TICKET_ANALYSIS = Path(__file__).resolve().parents[1]

REGISTRY_FIXTURE_SCAN = r'''
"""Offline fixture mirroring the real scan_code CLI + progress plumbing."""
import argparse, json, os, signal, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


def write_progress(run_dir, tried, qualifying, stop_reason):
    payload = {
        "schema_version": 1,
        "phase": "scanning",
        "tried": tried,
        "found_ok": tried,
        "qualifying": qualifying,
        "excluded_simulations": 0,
        "unpriced": 0,
        "stop_reason": stop_reason,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = run_dir / "progress.json"
    tmp = path.with_name(".progress.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def make_extract(provider, qualifying, tried, stop_reason):
    coupons = []
    for i in range(qualifying):
        coupons.append({
            "code": f"Q{i}", "ok": True, "provider": provider, "num_legs": 1,
            "site_displayed_odds": 200.0, "parsed_leg_product": 100.0,
            "computed_odds": 100.0, "total_odds": 200.0,
            "selections": [{"event_id": 100 + i, "event": f"G{i}",
                            "league": "L", "market": "1X2", "pick": "H",
                            "odds": 100.0, "kickoff": ""}],
        })
    return {
        "provider": provider, "seed": "SEED", "tried": tried,
        "found_ok": tried, "excluded_simulations": 0, "unpriced_excluded": 0,
        "odds_basis": "parsed_leg_product", "stop_reason": stop_reason,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "min_total_odds": 5000.0, "max_total_odds": 350000.0,
        "min_legs": 0, "max_legs": 0, "top_n_display": 0,
        "max_qualifying": 0, "max_codes_to_try": 25000,
        "max_mutation_depth": 3, "request_workers": 1,
        "qualifying": coupons,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--min-odds", type=float, default=None)
    ap.add_argument("--max-odds-cap", type=float, default=None)
    ap.add_argument("--max-codes", type=int, default=None)
    ap.add_argument("--depth", type=int, default=None)
    ap.add_argument("--max-qualifying", type=int, default=None)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--env", default=None)
    args = ap.parse_args()

    sleep_s = float(os.getenv("FIXTURE_SLEEP_SECONDS", "0"))
    exit_code = int(os.getenv("FIXTURE_EXIT_CODE", "0"))
    qualifying = int(os.getenv("FIXTURE_QUALIFYING", "1"))
    extract_mode = os.getenv("FIXTURE_EXTRACT_MODE", "valid")
    stop_reason = os.getenv("FIXTURE_STOP_REASON", "candidates_exhausted")
    provider = os.getenv("PROVIDER", "")
    if os.getenv("FIXTURE_IGNORE_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "results"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "started.marker").write_text(str(os.getpid()))

    interval = float(os.getenv("FIXTURE_PROGRESS_INTERVAL", "0.05"))
    tried = 0
    deadline = time.monotonic() + sleep_s
    while time.monotonic() < deadline:
        time.sleep(interval)
        tried += 1
        write_progress(run_dir, tried, min(tried, qualifying), None)
    if exit_code:
        print("fixture failing", exit_code)
        write_progress(run_dir, tried, 0, None)
        return exit_code

    if extract_mode == "none":
        write_progress(run_dir, tried, 0, stop_reason)
        return 0
    (run_dir / "extracts").mkdir(parents=True, exist_ok=True)
    extract_path = run_dir / "extracts" / "scan_fixture.json"
    if extract_mode == "partial":
        extract_path.write_text('{"provider": "')
    elif extract_mode == "invalid":
        extract_path.write_text("{not valid json")
    elif extract_mode == "wrong_provider":
        other = "sportybet" if provider == "bet9ja" else "bet9ja"
        extract_path.write_text(json.dumps(make_extract(other, qualifying, tried, stop_reason)))
    else:
        extract_path.write_text(json.dumps(make_extract(provider, qualifying, tried, stop_reason)))
    write_progress(run_dir, tried, qualifying, stop_reason)
    return int(os.getenv("FIXTURE_EXIT_AFTER_SAVE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
'''

BET9JA_ENV = "PROVIDER=bet9ja\nMIN_TOTAL_ODDS=5000\n"
SPORTYBET_ENV = "PROVIDER=sportybet\nMIN_TOTAL_ODDS=5000\n"


class RegistryTestBase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Path(self._temp.name).resolve() / "workspace"
        self.workspace.mkdir()
        for provider, dotenv in (("bet9ja", BET9JA_ENV), ("sportybet", SPORTYBET_ENV)):
            scripts = self.workspace / provider / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "scan_code.py").write_text(REGISTRY_FIXTURE_SCAN)
            (self.workspace / provider / ".env").write_text(dotenv)
        self._env_backup = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env_backup)))

    def registry(self, **kwargs):
        return RunRegistry(self.workspace, **kwargs)

    def wait_state(self, registry, run_id, states, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = registry.get(run_id)
            if run["state"] in states:
                return run
            time.sleep(0.05)
        self.fail(f"run {run_id} never reached {states}; last: {run}")


class RegistryLifecycleTests(RegistryTestBase):
    def test_successful_run_commits_extract_and_policy(self):
        registry = self.registry()
        run = registry.start_scan("bet9ja", "SEED")
        self.assertEqual(run["state"], "running")
        self.assertEqual(run["request"]["seed"], "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        self.assertEqual(final["stop_reason"], "candidates_exhausted")
        self.assertEqual(final["counters"]["qualifying"], 1)
        self.assertEqual(final["counters"]["tried"], 0)
        extract = final["extract"]
        self.assertTrue(extract["path"].endswith("scan_fixture.json"))
        self.assertEqual(len(extract["sha256"]), 64)
        self.assertEqual(extract["policy"],
                         {"odds_basis": "parsed_leg_product",
                          "min_odds": 5000.0, "max_odds": 350000.0})
        committed = registry.committed_extract(run["run_id"])
        self.assertEqual(Path(extract["path"]), committed)
        listed = registry.list_runs(provider="bet9ja")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["state"], STATE_SUCCEEDED)

    def test_empty_run_is_successful(self):
        registry = self.registry()
        os.environ["FIXTURE_QUALIFYING"] = "0"
        run = registry.start_scan("bet9ja", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        self.assertEqual(final["counters"]["qualifying"], 0)
        self.assertTrue(final["extract"]["path"])

    def test_stop_reason_propagated(self):
        registry = self.registry()
        os.environ["FIXTURE_STOP_REASON"] = "try_budget"
        run = registry.start_scan("sportybet", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        self.assertEqual(final["stop_reason"], "try_budget")

    def test_launch_failure_persisted(self):
        registry = self.registry(python="/nonexistent/python-xyz")
        run = registry.start_scan("bet9ja", "SEED")
        self.assertEqual(run["state"], STATE_LAUNCH_FAILED)
        self.assertEqual(run["error"]["code"], "launch_error")
        self.assertEqual(run["stop_reason"], "failure")
        self.assertIn(run["run_id"], [r["run_id"] for r in registry.list_runs()])

    def test_worker_failure_no_extract(self):
        registry = self.registry()
        os.environ["FIXTURE_EXIT_CODE"] = "3"
        os.environ["FIXTURE_SLEEP_SECONDS"] = "0.4"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        run = registry.start_scan("bet9ja", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_FAILED})
        self.assertEqual(final["error"]["code"], "worker_exit")
        self.assertGreaterEqual(final["counters"]["tried"], 1)

    def test_progress_visible_while_running_and_monotonic(self):
        registry = self.registry()
        os.environ["FIXTURE_SLEEP_SECONDS"] = "1.0"
        run = registry.start_scan("bet9ja", "SEED")
        last = -1
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            view = registry.get(run["run_id"])
            if view["state"] != "running":
                break
            tried = view["counters"]["tried"] or 0
            self.assertGreaterEqual(tried, last)
            last = tried
            time.sleep(0.05)
        self.assertGreater(last, 0, "live progress counters were never observed")

    def test_duplicate_start_rejected_without_record(self):
        registry = self.registry()
        os.environ["FIXTURE_SLEEP_SECONDS"] = "1.0"
        first = registry.start_scan("bet9ja", "SEED")
        with self.assertRaises(JobBusyError):
            registry.start_scan("bet9ja", "SEED")
        self.assertEqual(len(registry.list_runs(provider="bet9ja")), 1)
        self.wait_state(registry, first["run_id"], {STATE_SUCCEEDED})

    def test_providers_run_independently(self):
        registry = self.registry()
        os.environ["FIXTURE_SLEEP_SECONDS"] = "0.5"
        a = registry.start_scan("bet9ja", "SEED-A")
        b = registry.start_scan("sportybet", "SEED-B")
        fa = self.wait_state(registry, a["run_id"], {STATE_SUCCEEDED})
        fb = self.wait_state(registry, b["run_id"], {STATE_SUCCEEDED})
        self.assertEqual(fa["provider"], "bet9ja")
        self.assertEqual(fb["provider"], "sportybet")
        self.assertNotEqual(fa["extract"]["path"], fb["extract"]["path"])


class RegistryCommitTests(RegistryTestBase):
    def test_provider_mismatch_extract_rejected(self):
        registry = self.registry()
        os.environ["FIXTURE_EXTRACT_MODE"] = "wrong_provider"
        run = registry.start_scan("bet9ja", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_FAILED})
        self.assertEqual(final["error"]["code"], "invalid_extract")
        self.assertIsNone(final["extract"])
        with self.assertRaises(RunStateError):
            registry.committed_extract(run["run_id"])

    def test_malformed_extract_rejected(self):
        registry = self.registry()
        os.environ["FIXTURE_EXTRACT_MODE"] = "invalid"
        run = registry.start_scan("bet9ja", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_FAILED})
        self.assertEqual(final["error"]["code"], "invalid_extract")

    def test_partial_extract_rejected(self):
        registry = self.registry()
        os.environ["FIXTURE_EXTRACT_MODE"] = "partial"
        run = registry.start_scan("bet9ja", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_FAILED})
        self.assertEqual(final["error"]["code"], "invalid_extract")

    def test_missing_extract_fails(self):
        registry = self.registry()
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        run = registry.start_scan("bet9ja", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_FAILED})
        self.assertEqual(final["error"]["code"], "missing_extract")

    def test_multiple_extracts_none_committed(self):
        registry = self.registry()
        os.environ["FIXTURE_SLEEP_SECONDS"] = "0.6"
        run = registry.start_scan("bet9ja", "SEED")
        time.sleep(0.4)  # worker sleeps; plant a decoy before finalize
        extracts = self.workspace / "results" / "runs" / "bet9ja" / run["run_id"] / "extracts"
        extracts.mkdir(parents=True, exist_ok=True)
        (extracts / "decoy.json").write_text("{}")
        final = self.wait_state(registry, run["run_id"], {STATE_FAILED})
        self.assertEqual(final["error"]["code"], "multiple_extracts")

    def test_committed_extract_immune_to_later_neighbors(self):
        registry = self.registry()
        run = registry.start_scan("bet9ja", "SEED")
        final = self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        committed = registry.committed_extract(run["run_id"])
        # A new extract appears afterwards in the same dir: lookup still
        # returns the registered path, never rediscovers by glob.
        (committed.parent / "newer_scan_99.json").write_text("{}")
        self.assertEqual(registry.committed_extract(run["run_id"]), committed)
        # And a neighboring provider's run has its own committed extract.
        other = registry.start_scan("sportybet", "SEED")
        self.wait_state(registry, other["run_id"], {STATE_SUCCEEDED})
        self.assertNotEqual(registry.committed_extract(other["run_id"]), committed)


class RegistryCancelTests(RegistryTestBase):
    def test_cancel_during_work(self):
        registry = self.registry(cancel_grace_s=1.0)
        os.environ["FIXTURE_SLEEP_SECONDS"] = "30"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        run = registry.start_scan("bet9ja", "SEED")
        time.sleep(0.3)
        view = registry.cancel(run["run_id"])
        self.assertIsNotNone(view["cancel_requested_at"])
        final = self.wait_state(registry, run["run_id"], {STATE_CANCELLED})
        self.assertEqual(final["stop_reason"], "cancelled")
        self.assertIsNotNone(final["cancel_granted_at"])
        self.assertIsNone(final["extract"])
        followup = registry.start_scan(
            "bet9ja", "SEED", env_extra={"FIXTURE_SLEEP_SECONDS": "0",
                                          "FIXTURE_EXTRACT_MODE": "valid"})
        self.wait_state(registry, followup["run_id"], {STATE_SUCCEEDED})

    def test_repeated_cancel_idempotent(self):
        registry = self.registry(cancel_grace_s=1.0)
        os.environ["FIXTURE_SLEEP_SECONDS"] = "30"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        run = registry.start_scan("bet9ja", "SEED")
        time.sleep(0.3)
        first_at = registry.cancel(run["run_id"])["cancel_requested_at"]
        second_at = registry.cancel(run["run_id"])["cancel_requested_at"]
        self.assertEqual(first_at, second_at)
        self.wait_state(registry, run["run_id"], {STATE_CANCELLED})
        # Cancelling a terminal run is a no-op returning the final state.
        after = registry.cancel(run["run_id"])
        self.assertEqual(after["state"], STATE_CANCELLED)

    def test_cancel_completion_race(self):
        for _ in range(5):
            registry = self.registry(cancel_grace_s=1.0)
            os.environ["FIXTURE_SLEEP_SECONDS"] = "0.15"
            run = registry.start_scan("bet9ja", "SEED")
            time.sleep(0.1)
            registry.cancel(run["run_id"])
            final = self.wait_state(registry, run["run_id"],
                                    {STATE_CANCELLED, STATE_SUCCEEDED})
            if final["state"] == STATE_SUCCEEDED:
                self.assertIsNone(final["cancel_requested_at"])
            else:
                self.assertIsNotNone(final["cancel_requested_at"])

    def test_cancel_escalation_sigkills_only_verified_worker(self):
        registry = self.registry(cancel_grace_s=1.0)
        os.environ["FIXTURE_SLEEP_SECONDS"] = "60"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        os.environ["FIXTURE_IGNORE_SIGTERM"] = "1"
        run = registry.start_scan("bet9ja", "SEED")
        time.sleep(0.3)
        registry.cancel(run["run_id"])
        final = self.wait_state(registry, run["run_id"],
                                {STATE_CANCELLED}, timeout=30)
        self.assertEqual(final["stop_reason"], "cancelled")

    def test_cancel_unresolved_fails_closed_until_worker_exits(self):
        registry = self.registry(cancel_grace_s=0.5)
        os.environ["FIXTURE_SLEEP_SECONDS"] = "60"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        run = registry.start_scan("bet9ja", "SEED")
        time.sleep(0.3)
        job = registry._jobs[run["run_id"]]
        job.process.kill()  # supervisor dies out-of-band; the worker survives
        job.process.wait(timeout=30)
        registry.cancel(run["run_id"])
        view = self.wait_state(registry, run["run_id"], {STATE_CANCELLED_UNRESOLVED})
        self.assertEqual(view["error"]["code"], "cancel_unresolved")
        # Ownership retained: new start is refused while the worker lives.
        with self.assertRaises(JobBusyError):
            registry.start_scan("bet9ja", "SEED")
        os.kill(job.worker_pid, signal.SIGTERM)  # test owns this live-reported pid
        final = self.wait_state(registry, run["run_id"], {STATE_CANCELLED})
        self.assertEqual(final["stop_reason"], "cancelled")


class RegistryRecoveryTests(RegistryTestBase):
    def test_restart_reconciles_completed_work(self):
        registry = self.registry()
        run = registry.start_scan("bet9ja", "SEED")
        self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        run_dir = self.workspace / "results" / "runs" / "bet9ja" / run["run_id"]
        status_path = run_dir / "status.json"
        status = json.loads(status_path.read_text())
        status["state"] = "running"          # simulate a registry that died mid-run
        status["recovery_note"] = None
        status_path.write_text(json.dumps(status))
        fresh = self.registry()  # __init__ already runs recover()
        self.assertEqual(fresh.recover(), [], "recovery is idempotent")
        final = fresh.get(run["run_id"])
        self.assertEqual(final["state"], STATE_SUCCEEDED)
        self.assertIn("restart", final["recovery_note"])
        self.assertTrue(final["extract"]["path"])

    def test_restart_keeps_own_worker_run_running(self):
        registry = self.registry()
        os.environ["FIXTURE_SLEEP_SECONDS"] = "3"
        run = registry.start_scan("bet9ja", "SEED")
        fresh = self.registry()  # no local job: ownership is external
        view = fresh.get(run["run_id"])
        self.assertEqual(view["state"], "running")
        self.assertIn("this run's live worker", view["recovery_note"])
        self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})

    def test_restart_finalizes_run_when_lock_belongs_to_another_run(self):
        registry = self.registry()
        run = registry.start_scan("bet9ja", "SEED")
        self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        run_dir = self.workspace / "results" / "runs" / "bet9ja" / run["run_id"]
        status_path = run_dir / "status.json"
        status = json.loads(status_path.read_text())
        status["state"] = "running"          # simulate a registry that died mid-run
        status["recovery_note"] = None
        status_path.write_text(json.dumps(status))
        host = WorkerHost(self.workspace)
        holder = host.start_scan("bet9ja", "SEED",
                                 env_extra={"FIXTURE_SLEEP_SECONDS": "5"})
        self.addCleanup(holder.terminate)
        fresh = self.registry()
        # The held lock belongs to the HOLDER's run, not to the old record:
        # the old run finalizes from its own completion evidence.
        final = fresh.get(run["run_id"])
        self.assertEqual(final["state"], STATE_SUCCEEDED)
        self.assertIn("restart", final["recovery_note"])
        with self.assertRaises(JobBusyError):
            fresh.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(holder), 0)

    def test_restart_reconciles_cancellation_request(self):
        registry = self.registry()
        run = registry.start_scan("bet9ja", "SEED")
        self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        run_dir = self.workspace / "results" / "runs" / "bet9ja" / run["run_id"]
        status_path = run_dir / "status.json"
        status = json.loads(status_path.read_text())
        status["state"] = "running"
        status["cancel_requested_at"] = "2026-09-28T00:00:00Z"
        status_path.write_text(json.dumps(status))
        fresh = self.registry()
        final = fresh.get(run["run_id"])
        self.assertEqual(final["state"], STATE_CANCELLED)
        self.assertEqual(final["stop_reason"], "cancelled")


class RegistryPathSafetyTests(RegistryTestBase):
    def test_run_id_validation_and_traversal(self):
        registry = self.registry()
        for bad in ("nonsense", "../bet9ja/x", "run-bet9ja-20260928-120000-XXXXXXXX",
                    "run-bet9ja-20260928-120000-zzzzzzzz", "", None):
            with self.assertRaises(ValueError, msg=bad):
                registry.get(bad)
        with self.assertRaises(RunNotFoundError):
            registry.get("run-bet9ja-20260928-120000-deadbeef")

    def test_status_remains_readable_under_concurrent_polling(self):
        registry = self.registry()
        os.environ["FIXTURE_SLEEP_SECONDS"] = "0.8"
        run = registry.start_scan("bet9ja", "SEED")
        stop = threading.Event()
        errors = []

        def poll():
            while not stop.is_set():
                try:
                    view = registry.get(run["run_id"])
                    assert view["schema_version"] == 1
                    assert view["provider"] == "bet9ja"
                    assert view["run_id"] == run["run_id"]
                    json.dumps(view)
                except Exception as exc:  # noqa: BLE001 - collected, not swallowed
                    errors.append(exc)
                    stop.set()

        threads = [threading.Thread(target=poll) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])


class RegistryCrashBoundaryTests(RegistryTestBase):
    def test_observer_does_not_finalize_in_progress_launch(self):
        os.environ[LAUNCH_PAUSE_ENV] = "2"
        launched = threading.Event()
        started = {}

        def start():
            started["run"] = self.registry().start_scan("bet9ja", "SEED")
            launched.set()

        thread = threading.Thread(target=start)
        thread.start()
        time.sleep(0.5)  # claimant has published claim + status, still launching
        observer = self.registry()
        runs = observer.list_runs(provider="bet9ja")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["state"], "starting",
                         "in-progress launch must not be finalized by an observer")
        self.assertEqual(observer.recover(), [])
        run_id = runs[0]["run_id"]
        thread.join(timeout=30)
        self.assertTrue(launched.is_set())
        final = self.wait_state(observer, run_id, {STATE_SUCCEEDED})
        self.assertEqual(final["provider"], "bet9ja")

    def test_cancel_during_launch_window(self):
        os.environ[LAUNCH_PAUSE_ENV] = "2"
        os.environ["FIXTURE_SLEEP_SECONDS"] = "30"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        launched = threading.Event()

        def start():
            self.registry().start_scan("bet9ja", "SEED")
            launched.set()

        thread = threading.Thread(target=start)
        thread.start()
        time.sleep(0.5)
        observer = self.registry()
        run_id = observer.list_runs(provider="bet9ja")[0]["run_id"]
        view = observer.cancel(run_id)
        # Nothing is running yet (launch window): the request settles the run
        # as cancelled instead of masquerading as an executed cancel.
        self.assertEqual(view["state"], STATE_CANCELLED_UNRESOLVED)
        thread.join(timeout=30)
        self.assertTrue(launched.is_set())
        final = self.wait_state(observer, run_id, {STATE_CANCELLED})
        self.assertIsNotNone(final["cancel_requested_at"])
        self.assertIsNotNone(final["cancel_granted_at"])
        marker = (self.workspace / "results" / "runs" / "bet9ja" / run_id
                  / "started.marker")
        self.assertFalse(marker.exists(),
                         "claimant must not launch a worker for a terminal run")

    def test_uncertain_launch_stays_observable_then_unknown(self):
        os.environ[SUPERVISOR_PAUSE_ENV] = "60"   # supervisor never reports
        os.environ["FIXTURE_SLEEP_SECONDS"] = "2"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        registry = self.registry(report_timeout_s=0.5, reap_timeout_s=0.5)
        run = registry.start_scan("bet9ja", "SEED")
        self.assertEqual(run["state"], "running",
                         "an unacknowledged live worker must not be terminal")
        self.assertEqual(run["error"]["code"], "launch_unacknowledged")
        self.assertTrue(registry.host.lock_held("bet9ja"))
        self.assertIsNotNone(registry.active_run("bet9ja"))
        final = self.wait_state(registry, run["run_id"], {STATE_UNKNOWN})
        self.assertEqual(final["error"]["code"], "completion_unknown",
                         "no completion evidence => unknown, never succeeded")

    def craft_dead_registry_run(self, exit_code, keep_extract=True):
        registry = self.registry()
        run = registry.start_scan("bet9ja", "SEED")
        self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        run_dir = self.workspace / "results" / "runs" / "bet9ja" / run["run_id"]
        status = json.loads((run_dir / "status.json").read_text())
        status["state"] = "running"
        status["recovery_note"] = None
        (run_dir / "status.json").write_text(json.dumps(status))
        exit_file = run_dir / "worker.exit.json"
        if exit_code is None:
            exit_file.unlink()
        else:
            exit_file.write_text(json.dumps(
                {"provider": "bet9ja", "run_id": run["run_id"],
                 "worker_pid": 1, "exit_code": exit_code,
                 "finished_at": "2026-09-28T00:00:00Z"}))
        if not keep_extract:
            shutil.rmtree(run_dir / "extracts")
        return run["run_id"]

    def test_recovery_respects_nonzero_exit_evidence(self):
        run_id = self.craft_dead_registry_run(exit_code=7)
        final = self.registry().get(run_id)
        self.assertEqual(final["state"], STATE_FAILED)
        self.assertEqual(final["error"]["code"], "worker_exit")

    def test_recovery_without_exit_evidence_is_unknown(self):
        run_id = self.craft_dead_registry_run(exit_code=None)
        final = self.registry().get(run_id)
        self.assertEqual(final["state"], STATE_UNKNOWN)
        self.assertEqual(final["error"]["code"], "completion_unknown")

    def test_recovery_with_zero_exit_evidence_commits(self):
        run_id = self.craft_dead_registry_run(exit_code=0)
        final = self.registry().get(run_id)
        self.assertEqual(final["state"], STATE_SUCCEEDED)
        self.assertIn("restart", final["recovery_note"])
        self.assertTrue(final["extract"]["path"])

    def test_cross_registry_cancel_consumed_by_owner(self):
        os.environ["FIXTURE_SLEEP_SECONDS"] = "30"
        os.environ["FIXTURE_EXTRACT_MODE"] = "none"
        owner = self.registry()
        run = owner.start_scan("bet9ja", "SEED")
        time.sleep(0.3)
        other = self.registry()
        view = other.cancel(run["run_id"])
        self.assertEqual(view["state"], STATE_CANCELLED_UNRESOLVED)
        final = self.wait_state(owner, run["run_id"], {STATE_CANCELLED})
        self.assertIsNotNone(final["cancel_granted_at"],
                             "owner must consume the persisted cancellation request")
        self.assertFalse(owner.host.lock_held("bet9ja"))

    def test_committed_extract_integrity_enforced(self):
        registry = self.registry()
        run = registry.start_scan("bet9ja", "SEED")
        self.wait_state(registry, run["run_id"], {STATE_SUCCEEDED})
        path = registry.committed_extract(run["run_id"])
        original = path.read_bytes()
        path.write_text("{broken")
        with self.assertRaises(RunStateError):
            registry.committed_extract(run["run_id"])
        path.write_text(json.dumps({"provider": "bet9ja", "qualifying": []}))
        with self.assertRaises(RunStateError):
            registry.committed_extract(run["run_id"])
        path.write_bytes(original[:50])  # truncation
        with self.assertRaises(RunStateError):
            registry.committed_extract(run["run_id"])
        path.write_bytes(original)  # restoration
        self.assertEqual(registry.committed_extract(run["run_id"]), path)
        self.assertEqual(registry.get(run["run_id"])["state"], STATE_SUCCEEDED)


class RegistryRealCrashTests(RegistryTestBase):
    def wait_file(self, path, timeout=10):
        deadline = time.monotonic() + timeout
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(path.exists(), str(path))

    def launch_host(self, script, **env):
        child_env = dict(os.environ, WORKSPACE=str(self.workspace), **env)
        process = subprocess.Popen([sys.executable, "-c", script],
                                   cwd=TICKET_ANALYSIS, env=child_env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        return process

    def test_host_crash_before_launch_releases_claim_without_starting_worker(self):
        script = """
import os
from portfolio.registry import RunRegistry
RunRegistry(os.environ['WORKSPACE']).start_scan('bet9ja', 'SEED')
"""
        process = self.launch_host(script, **{LAUNCH_PAUSE_ENV: '120'})
        root = self.workspace / 'results' / 'runs' / 'bet9ja'
        deadline = time.monotonic() + 10
        statuses = []
        while not statuses and time.monotonic() < deadline:
            statuses = list(root.glob('run-*/status.json'))
            time.sleep(.02)
        self.assertEqual(len(statuses), 1)
        run_dir = statuses[0].parent
        # An arbitrarily old diagnostic claim must not defeat the live lock.
        (run_dir / 'launch.pending').write_text(json.dumps(
            {'pid': process.pid, 'claimed_at': '2000-01-01T00:00:00+00:00'}))
        observer = self.registry()
        self.assertEqual(observer.get(run_dir.name)['state'], 'starting')
        process.kill()
        process.wait(timeout=10)
        recovered = observer.get(run_dir.name)
        self.assertEqual(recovered['state'], STATE_UNKNOWN)
        self.assertFalse((run_dir / 'started.marker').exists())

    def test_host_crash_after_ack_before_running_publication_uses_exit_evidence(self):
        script = """
import os,time,json
from pathlib import Path
from portfolio.registry import RunRegistry
r=RunRegistry(os.environ['WORKSPACE'])
def pause_after_ack(run_dir):
    (Path(os.environ['WORKSPACE'])/'ack.marker').write_text(run_dir.name)
    time.sleep(120)
r._clear_launch_claim=pause_after_ack
r.start_scan('bet9ja','SEED',env_extra={'FIXTURE_SLEEP_SECONDS': '0.7',
                                     'FIXTURE_EXIT_AFTER_SAVE': '7'})
"""
        process = self.launch_host(script)
        marker = self.workspace / 'ack.marker'
        self.wait_file(marker)
        run_id = marker.read_text()
        run_dir = self.workspace / 'results' / 'runs' / 'bet9ja' / run_id
        self.wait_file(run_dir / 'started.marker')
        process.kill()
        process.wait(timeout=10)
        observer = self.registry()
        final = self.wait_state(observer, run_id, {STATE_FAILED})
        self.assertEqual(final['error']['code'], 'worker_exit')
        self.assertIn('7', final['error']['message'])
        self.assertIsNone(final['extract'])
        self.assertTrue(list((run_dir / 'extracts').glob('*.json')))

    def test_completion_evidence_must_belong_to_the_run(self):
        registry = self.registry()
        run = registry.start_scan('bet9ja', 'SEED')
        self.wait_state(registry, run['run_id'], {STATE_SUCCEEDED})
        run_dir = self.workspace / 'results' / 'runs' / 'bet9ja' / run['run_id']
        status = json.loads((run_dir / 'status.json').read_text())
        status['state'] = 'running'
        (run_dir / 'status.json').write_text(json.dumps(status))
        receipt = json.loads((run_dir / 'worker.exit.json').read_text())
        receipt['run_id'] = 'different-run'
        (run_dir / 'worker.exit.json').write_text(json.dumps(receipt))
        self.assertEqual(self.registry().get(run['run_id'])['state'], STATE_UNKNOWN)

    def test_invalid_params_do_not_allocate_a_run(self):
        registry = self.registry()
        for params in ({'max_qualifying': True}, {'max_qualifying': -1},
                       {'max_codes': 1.5}, {'min_odds': float('nan')},
                       {'min_odds': 100, 'max_odds': 50}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                registry.start_scan('bet9ja', 'SEED', **params)
        self.assertEqual(registry.list_runs(), [])


if __name__ == "__main__":
    unittest.main()
