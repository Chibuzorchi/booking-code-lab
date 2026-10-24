"""F9 step 5 piece 3: bounded resumable result jobs.

Pure filesystem/in-process tests against a throwaway TemporaryDirectory; no
network, no real source adapter needed. fetch_one is a plain test double.
"""
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portfolio.results_tracking.jobs import (
    FatalTaskError, JobBudget, JobError, JobNotFoundError, JobState, JobStore,
    Task, run_result_job,
)


def _store(tmp: str) -> JobStore:
    return JobStore(Path(tmp))


class JobStoreIdentityTests(unittest.TestCase):
    def test_create_starts_in_starting_state(self):
        with TemporaryDirectory() as tmp:
            record = _store(tmp).create_or_reuse("run-1", "input-a", "config-a")
            self.assertEqual(record.state, JobState.STARTING)
            self.assertEqual(record.run_id, "run-1")

    def test_same_identity_reuses_active_job(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            first = store.create_or_reuse("run-1", "input-a", "config-a")
            second = store.create_or_reuse("run-1", "input-a", "config-a")
            self.assertEqual(first.job_id, second.job_id)

    def test_different_identity_gets_separate_job(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            first = store.create_or_reuse("run-1", "input-a", "config-a")
            second = store.create_or_reuse("run-1", "input-b", "config-a")
            self.assertNotEqual(first.job_id, second.job_id)

    def test_new_job_started_once_prior_identity_job_is_terminal(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            first = store.create_or_reuse("run-1", "input-a", "config-a")
            store.update(first.job_id, lambda r: r.update(state=JobState.SUCCEEDED))
            second = store.create_or_reuse("run-1", "input-a", "config-a")
            self.assertNotEqual(first.job_id, second.job_id)

    def test_get_unknown_job_raises(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(JobNotFoundError):
                _store(tmp).get("job-does-not-exist")


class JobStoreUpdateTests(unittest.TestCase):
    def test_update_mutates_nonterminal_job(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            updated = store.update(record.job_id, lambda r: r.update(state=JobState.RUNNING))
            self.assertEqual(updated.state, JobState.RUNNING)

    def test_update_is_noop_once_terminal(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            store.update(record.job_id, lambda r: r.update(state=JobState.FAILED,
                                                             error={"code": "x", "message": "x"}))
            store.update(record.job_id, lambda r: r.update(state=JobState.SUCCEEDED))
            final = store.get(record.job_id)
            self.assertEqual(final.state, JobState.FAILED)

    def test_request_cancel_is_idempotent_and_noop_once_terminal(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            store.request_cancel(record.job_id)
            store.request_cancel(record.job_id)
            self.assertTrue(store.get(record.job_id).cancel_requested)
            store.update(record.job_id, lambda r: r.update(state=JobState.SUCCEEDED))
            store.request_cancel(record.job_id)
            self.assertEqual(store.get(record.job_id).state, JobState.SUCCEEDED)


class RunResultJobTests(unittest.TestCase):
    def test_all_tasks_succeed(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            tasks = [Task(key=f"t{i}", payload=i) for i in range(5)]
            outcome = run_result_job(
                store, record.job_id, tasks,
                fetch_one=lambda task: task.payload * 2,
                budget=JobBudget(max_requests=10, max_workers=2),
            )
            self.assertEqual(outcome.results, {f"t{i}": i * 2 for i in range(5)})
            self.assertEqual(outcome.incomplete_task_keys, ())
            self.assertFalse(outcome.cancelled)
            self.assertEqual(store.get(record.job_id).state, JobState.SUCCEEDED)

    def test_already_terminal_job_raises(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            store.update(record.job_id, lambda r: r.update(state=JobState.CANCELLED))
            with self.assertRaises(JobError):
                run_result_job(store, record.job_id, [Task(key="t0")],
                                fetch_one=lambda task: None, budget=JobBudget(max_requests=1))

    def test_fatal_error_marks_job_failed_and_reraises(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")

            def fetch_one(task):
                raise FatalTaskError("auth failed")

            with self.assertRaises(FatalTaskError):
                run_result_job(store, record.job_id, [Task(key="t0")],
                                fetch_one=fetch_one, budget=JobBudget(max_requests=5))
            final = store.get(record.job_id)
            self.assertEqual(final.state, JobState.FAILED)
            self.assertEqual(final.error["code"], "fatal_task_error")

    def test_retries_then_succeeds_within_max_retries(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            attempts = {"count": 0}

            def fetch_one(task):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise RuntimeError("transient")
                return "ok"

            outcome = run_result_job(
                store, record.job_id, [Task(key="t0")], fetch_one=fetch_one,
                budget=JobBudget(max_requests=10, max_retries=3),
            )
            self.assertEqual(outcome.results, {"t0": "ok"})
            self.assertEqual(attempts["count"], 3)

    def test_exhausting_retries_leaves_task_incomplete_and_fails_job(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")

            def fetch_one(task):
                raise RuntimeError("always fails")

            outcome = run_result_job(
                store, record.job_id, [Task(key="t0")], fetch_one=fetch_one,
                budget=JobBudget(max_requests=10, max_retries=1),
            )
            self.assertEqual(outcome.incomplete_task_keys, ("t0",))
            self.assertEqual(store.get(record.job_id).state, JobState.FAILED)
            self.assertEqual(store.get(record.job_id).error["code"], "budget_exhausted")

    def test_per_job_request_budget_caps_total_attempts(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            calls = {"count": 0}

            def fetch_one(task):
                calls["count"] += 1
                raise RuntimeError("always fails")

            tasks = [Task(key="t0")]
            run_result_job(store, record.job_id, tasks, fetch_one=fetch_one,
                            budget=JobBudget(max_requests=2, max_retries=10))
            # max_retries=10 would allow 11 attempts, but the per-job budget
            # of 2 caps total attempts across the whole job.
            self.assertEqual(calls["count"], 2)

    def test_resuming_skips_already_completed_tasks(self):
        # Simulates a process crash: the job is left in "running" with one
        # task already recorded complete (progress persisted before the
        # crash), never reaching a terminal state. A fresh process resuming
        # the same job_id must not re-fetch that task.
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            store.update(record.job_id, lambda r: (
                r.update(state=JobState.RUNNING),
                r["progress"].update(tasks_completed=["t0"], requests_used=1),
            ))
            calls = []

            def fetch_one(task):
                calls.append(task.key)
                return task.key.upper()

            tasks = [Task(key="t0"), Task(key="t1")]
            outcome = run_result_job(store, record.job_id, tasks, fetch_one=fetch_one,
                                      budget=JobBudget(max_requests=10))
            self.assertEqual(calls, ["t1"])  # t0 skipped, already completed
            self.assertEqual(outcome.results, {"t1": "T1"})
            self.assertEqual(outcome.incomplete_task_keys, ())
            self.assertEqual(store.get(record.job_id).state, JobState.SUCCEEDED)

    def test_cancellation_stops_new_chunks_but_keeps_prior_results(self):
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")

            def fetch_one(task):
                return task.key

            # Cancel before run_result_job starts: first chunk still runs
            # (cooperative check happens before each chunk, not mid-chunk),
            # but no further chunks are submitted.
            store.request_cancel(record.job_id)
            tasks = [Task(key=f"t{i}") for i in range(4)]
            outcome = run_result_job(store, record.job_id, tasks, fetch_one=fetch_one,
                                      budget=JobBudget(max_requests=10, max_workers=4))
            self.assertTrue(outcome.cancelled)
            self.assertEqual(outcome.results, {})
            self.assertEqual(store.get(record.job_id).state, JobState.CANCELLED)

    def test_a_blocked_worker_does_not_hold_up_the_runner_past_its_timeout(self):
        # S5-08 repro: a fetch blocked indefinitely behind an unset Event,
        # with a tiny per_call_timeout, used to leave the runner (not just
        # future.result()) blocked past that timeout -- because leaving
        # ThreadPoolExecutor's `with` block waits for every submitted
        # thread regardless of whether its result was already given up on.
        # The worker is abandoned (never released), so a strict wait=True
        # shutdown would hang this test forever if the fix regressed.
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            blocker = threading.Event()
            # concurrent.futures.thread registers an atexit hook that joins
            # every worker thread at interpreter shutdown, with no timeout,
            # regardless of this test's own shutdown(wait=False) fix -- so
            # the abandoned worker must still be released before the test
            # process exits, or the whole run hangs forever.
            self.addCleanup(blocker.set)

            def fetch_one(task):
                blocker.wait()  # released only by the cleanup above
                return "unreachable"

            started = time.monotonic()
            outcome = run_result_job(
                store, record.job_id, [Task(key="t0")], fetch_one=fetch_one,
                budget=JobBudget(max_requests=1, max_retries=0, per_call_timeout=0.02),
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.15)
            self.assertEqual(outcome.incomplete_task_keys, ("t0",))
            self.assertEqual(store.get(record.job_id).state, JobState.FAILED)

    def test_cancellation_is_observed_before_a_retry_but_not_mid_attempt(self):
        # A task already in flight when cancellation lands is left to
        # finish (its result, if any, is still recorded); only the next
        # retry attempt is refused (S5-08).
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            record = store.create_or_reuse("run-1", "input-a", "config-a")
            attempts = {"count": 0}

            def fetch_one(task):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    store.request_cancel(record.job_id)
                raise RuntimeError("always fails")

            outcome = run_result_job(
                store, record.job_id, [Task(key="t0")], fetch_one=fetch_one,
                budget=JobBudget(max_requests=10, max_retries=5, max_workers=1),
            )
            self.assertEqual(attempts["count"], 1)
            self.assertEqual(outcome.incomplete_task_keys, ("t0",))


if __name__ == "__main__":
    unittest.main()
