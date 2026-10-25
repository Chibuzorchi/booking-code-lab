"""Publication/cancellation ordering and real worker lifetime ownership.

Uses blocked workers and actual process exit; no source traffic or wall-clock
orphan heuristics. Existing API helpers supply only offline fixture data.
"""
import json
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from portfolio.results_tracking import results_api as api
from portfolio.results_tracking.jobs import JobError, JobState, JobStore
from test_results_tracking_api import (
    FakeApiFootballClient, FakeRegistry, _extract, _ticket, _wait_for_terminal,
)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = api.ResultsApiConfig(root=Path(self.tmp.name), per_call_timeout=5)
        self.store = JobStore(self.config.jobs_root())
        self.registry = FakeRegistry(_extract([
            _ticket("C1", "Arsenal - Chelsea", "L", "1X2", "1",
                    "2026-10-23T15:00:00", sport="Soccer")]))
        self.client = FakeApiFootballClient({"2026-10-23": []})

    def submit(self):
        return api.submit_evaluation(self.registry, self.config, self.store, "run-1",
                                     client_factory=lambda: self.client)

    def seed_report(self):
        job = self.submit()
        _wait_for_terminal(self.store, job.job_id)
        return api.get_latest_report(self.config, "run-1")["revision_id"]

    def wait(self, event):
        self.assertTrue(event.wait(3), "worker did not reach test barrier")

    def test_cancel_during_evaluation_preserves_previous_latest(self):
        self._cancel_at_barrier("evaluate_run")

    def test_cancel_after_staging_preserves_previous_latest_and_no_manifest(self):
        self._cancel_at_barrier("_stage_revision")

    def _cancel_at_barrier(self, target):
        previous = self.seed_report()
        entered, release = threading.Event(), threading.Event()
        original = getattr(api, target)

        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier timed out")
            return result

        with patch.object(api, target, side_effect=paused):
            job = self.submit()
            try:
                self.wait(entered)
                api.cancel_job(self.store, job.job_id)
            finally:
                release.set()
                final = _wait_for_terminal(self.store, job.job_id)
        self.assertEqual(final.state, JobState.CANCELLED)
        self.assertIsNone(final.result)
        self.assertEqual(api.get_latest_report(self.config, "run-1")["revision_id"], previous)
        self.assertEqual(len(list(self.config.revisions_root("run-1").glob("*/manifest.json"))), 1)

    def test_publication_winning_status_lock_makes_later_cancel_a_terminal_noop(self):
        entered, release, requested, returned = (threading.Event() for _ in range(4))
        original = api._publish_staged_revision

        def paused(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test publication barrier timed out")
            return original(*args, **kwargs)

        results = []
        with patch.object(api, "_publish_staged_revision", side_effect=paused):
            job = self.submit()
            self.wait(entered)

            def cancel():
                requested.set()
                results.append(api.cancel_job(self.store, job.job_id))
                returned.set()

            caller = threading.Thread(target=cancel)
            caller.start()
            try:
                self.wait(requested)
                self.assertFalse(returned.wait(0.05))  # publication owns the status lock
            finally:
                release.set()
                caller.join(3)
                final = _wait_for_terminal(self.store, job.job_id)
        self.assertFalse(caller.is_alive())
        self.assertEqual(final.state, JobState.SUCCEEDED)
        self.assertFalse(final.cancel_requested)
        self.assertEqual(results[0].state, JobState.SUCCEEDED)
        self.assertEqual(api.get_latest_report(self.config, "run-1")["revision_id"],
                         final.result["revision_id"])

    def test_live_worker_with_old_progress_is_not_replaced(self):
        entered, release = threading.Event(), threading.Event()

        class BlockedClient:
            def get_fixtures_by_date(self, date):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test fetch barrier timed out")
                return []

        self.client = BlockedClient()
        job = self.submit()
        try:
            self.wait(entered)
            path = self.store._status_path(job.job_id)
            record = json.loads(path.read_bytes())
            record["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            path.write_text(json.dumps(record))
            other_store = JobStore(self.config.jobs_root())
            reused = api.submit_evaluation(self.registry, self.config, other_store, "run-1",
                                           client_factory=lambda: self.client)
            self.assertEqual(reused.job_id, job.job_id)
            self.assertEqual(reused.state, JobState.RUNNING)
        finally:
            release.set()
            final = _wait_for_terminal(self.store, job.job_id)
        self.assertEqual(final.state, JobState.SUCCEEDED)
        self.assertEqual(len(list(self.config.revisions_root("run-1").glob("*/manifest.json"))), 1)

    def test_process_death_releases_ownership_and_resumes_inner_work_immediately(self):
        _, digest, _, _ = api.load_population(self.registry, "run-1")
        config_digest = api.compute_config_digest(self.config)
        # Abrupt exit deliberately bypasses Python cleanup. The kernel must
        # release both descriptors, while both RUNNING records remain on disk.
        code = """
import os, sys
from pathlib import Path
from portfolio.results_tracking.jobs import JobStore
store = JobStore(Path(sys.argv[1]))
outer, outer_owner = store.claim_worker('run-1', sys.argv[2], sys.argv[3])
inner = store.create_or_reuse('run-1:fetch', sys.argv[4], sys.argv[3])
inner_owner = store.try_acquire_worker(inner.job_id)
store.update(inner.job_id, lambda r: r.update(state='running'))
store.update(outer.job_id, lambda r: r['progress'].update(fetch_job_id=inner.job_id))
print(outer.job_id, flush=True)
os._exit(0)
"""
        child = subprocess.run(
            [sys.executable, "-c", code, str(self.config.jobs_root()),
             api._job_identity_digest(digest, False), config_digest, digest],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        old_id = child.stdout.strip()
        self.assertEqual(self.store.get(old_id).state, JobState.RUNNING)
        replacement = self.submit()
        final = _wait_for_terminal(self.store, replacement.job_id)
        self.assertNotEqual(replacement.job_id, old_id)
        self.assertEqual(self.store.get(old_id).error["code"], "orphaned_worker")
        self.assertEqual(final.state, JobState.SUCCEEDED)
        self.assertEqual(self.client.calls, ["2026-10-23"])

    def test_failed_or_wrong_owner_cannot_invoke_publication(self):
        job, owner = self.store.claim_worker("run-1", "input", "config")
        publish = Mock(return_value={"revision_id": "unused"})
        try:
            self.store.update(job.job_id, lambda r: r.update(state=JobState.FAILED))
            result = api._commit_publication(self.store, job.job_id, owner, publish)
            self.assertEqual(result.state, JobState.FAILED)
            publish.assert_not_called()
        finally:
            owner.close()
        with self.assertRaises(JobError):
            api._commit_publication(self.store, job.job_id, owner, publish)
        publish.assert_not_called()

    def test_owner_is_held_before_worker_thread_starts(self):
        real_thread = threading.Thread
        entered, release = threading.Event(), threading.Event()
        first = []

        def delayed_thread(*args, **kwargs):
            worker = real_thread(*args, **kwargs)

            class DelayedStart:
                def start(self):
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("test launch barrier timed out")
                    worker.start()

            return DelayedStart()

        caller = real_thread(target=lambda: first.append(self.submit()))
        with patch.object(api.threading, "Thread", side_effect=delayed_thread):
            caller.start()
            try:
                self.wait(entered)
                second = self.submit()
                self.assertEqual(second.state, JobState.RUNNING)
                cancelled = api.cancel_job(self.store, second.job_id)
                self.assertEqual(cancelled.state, JobState.RUNNING)
                self.assertTrue(cancelled.cancel_requested)
            finally:
                release.set()
                caller.join(3)
        self.assertFalse(caller.is_alive())
        self.assertEqual(first[0].job_id, second.job_id)
        self.assertEqual(_wait_for_terminal(self.store, second.job_id).state, JobState.CANCELLED)
        self.assertEqual(self.client.calls, [])

    def test_launch_failure_releases_lock_and_allows_retry(self):
        with patch.object(api.threading.Thread, "start", side_effect=RuntimeError("launch refused")):
            failed = self.submit()
        self.assertEqual(failed.state, JobState.FAILED)
        self.assertEqual(failed.error["code"], "worker_launch_failed")
        retried = self.submit()
        self.assertNotEqual(retried.job_id, failed.job_id)
        self.assertEqual(_wait_for_terminal(self.store, retried.job_id).state, JobState.SUCCEEDED)
