"""Bounded resumable result jobs, separate from harvests.

F9_RESULTS_PLAN.md section 8 scopes this module narrowly: it orchestrates
*fetching* (bounded concurrency, per-call timeout, retries, a per-job
request budget, cooperative cancellation, durable resumable progress) --
it does not fetch anything itself, does not know about API-Football or any
other source, and does not publish report revisions. Those stay in
sources/, cache.py and reporting.py/__main__.py respectively. This keeps
jobs.py reusable for any future source adapter.

Design choices directly grounded in section 8's wording:

- "Result jobs persist progress and resume completed work": progress is
  the set of completed task *keys*, not their results. A resumed job skips
  calling `fetch_one` again for an already-completed key and spends no
  budget on it -- but it also does not return that key's data in this run's
  JobRunOutcome.results. The caller is expected to pair a task's key with
  cache.py's own durable store (e.g. a CacheKey digest) to recover
  already-fetched data; jobs.py deliberately does not duplicate cache.py's
  storage.
- "One active evaluation per run/input/config prevents duplicate charges":
  JobStore.create_or_reuse computes a stable identity from
  (run_id, input_digest, config_digest) and returns the existing job if one
  for that identity is still non-terminal, instead of starting a second one.
- "Bound worker/source concurrency, per-call timeout, retries and per-job
  request budget": run_result_job processes tasks in chunks no larger than
  budget.max_workers, retries a task up to budget.max_retries times, times
  out each attempt at budget.per_call_timeout, and never issues more than
  budget.max_requests attempts in total for the job. A timed-out attempt's
  thread is abandoned, not killed -- Python cannot forcibly stop a running
  thread -- its result is simply never collected.
- "Cancellation stops new calls, preserves evidence and leaves the last
  complete report intact": cancellation is cooperative, checked before each
  new chunk is submitted. In-flight attempts in the current chunk are
  allowed to finish (and their results, including any already-cached
  fetches performed by fetch_one, are kept); no further chunks are
  submitted. This module never writes a report, so "leaves the last
  complete report intact" is automatically true -- it has no report to
  touch.
- "Authentication/rate failures are not negative fixture evidence": a
  `fetch_one` that raises FatalTaskError aborts the job immediately as
  failed, with a structured error -- it is never retried into a false
  "fetched, nothing there" result, and never silently swallowed.
- Job states are starting/running/succeeded/failed/cancelled with durable
  timestamps and structured errors, exactly as section 8 lists them. A
  progress update is a no-op once a job is terminal (mirrors registry.py's
  "a progress update can never overwrite a cancellation or terminal
  state").
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


class JobState:
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})


class JobError(RuntimeError):
    """Invalid job configuration or an operation invalid for a job's state."""


class JobNotFoundError(KeyError):
    """No job with this id is recorded."""


# Matches exactly what create_or_reuse() mints (f"job-{uuid4().hex}"). A
# caller-supplied job_id (e.g. from an HTTP path segment) is joined straight
# into a filesystem path below, so anything else -- including one containing
# "/" or ".." -- is rejected as not-found before that join happens.
_JOB_ID_RE = re.compile(r"^job-[0-9a-f]{32}$")


class FatalTaskError(RuntimeError):
    """A failure that must never be retried or treated as negative evidence
    (e.g. authentication/rate-limit failures) -- aborts the job immediately."""


class _BudgetExhaustedError(RuntimeError):
    """Internal signal: the per-job request budget has no attempts left."""


class _CancelledTaskError(RuntimeError):
    """Internal signal: cancellation was observed before a retry attempt."""


@contextmanager
def _flock_path(path: Path):
    """Exclusive flock on path, serializing access across processes.
    Mirrors registry.py's/cache.py's _flock_path -- same convention."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _write_atomic(path: Path, raw_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(raw_bytes)
    os.replace(tmp, path)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def identity_digest(run_id: str, input_digest: str, config_digest: str) -> str:
    """Stable identity for 'one active evaluation per run/input/config'."""
    payload = "|".join((run_id, input_digest, config_digest)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    identity: str
    run_id: str
    input_digest: str
    config_digest: str
    state: str
    created_at: str
    updated_at: str
    progress: dict
    cancel_requested: bool
    error: dict | None
    result: dict | None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_JOB_STATES

    @classmethod
    def _from_dict(cls, data: dict) -> "JobRecord":
        return cls(
            job_id=data["job_id"], identity=data["identity"], run_id=data["run_id"],
            input_digest=data["input_digest"], config_digest=data["config_digest"],
            state=data["state"], created_at=data["created_at"], updated_at=data["updated_at"],
            progress=data["progress"], cancel_requested=data["cancel_requested"],
            error=data["error"], result=data["result"],
        )


class JobStore:
    """Durable job records under `root`, one directory per job_id plus an
    identity index for the one-active-job-per-identity rule."""

    def __init__(self, root: Path):
        self._root = Path(root)

    def _job_dir(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
            raise JobNotFoundError(job_id)
        return self._root / "jobs" / job_id

    def _status_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "status.json"

    def _status_lock_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "status.lock"

    def _identity_path(self, identity: str) -> Path:
        return self._root / "identities" / f"{identity}.json"

    def _identity_lock_path(self, identity: str) -> Path:
        return self._root / "identities" / f"{identity}.lock"

    def _read_status_dict(self, job_id: str) -> dict:
        try:
            raw = self._status_path(job_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise JobNotFoundError(job_id) from None
        return json.loads(raw)

    def _write_status_dict(self, job_id: str, data: dict) -> None:
        _write_atomic(self._status_path(job_id),
                       json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))

    def create_or_reuse(self, run_id: str, input_digest: str, config_digest: str) -> JobRecord:
        """Returns the existing non-terminal job for this identity, or
        starts a new one. Never creates a second active job for the same
        (run_id, input_digest, config_digest)."""
        identity = identity_digest(run_id, input_digest, config_digest)
        with _flock_path(self._identity_lock_path(identity)):
            try:
                existing_job_id = json.loads(
                    self._identity_path(identity).read_text(encoding="utf-8"))["job_id"]
            except FileNotFoundError:
                existing_job_id = None
            if existing_job_id is not None:
                try:
                    existing = self._read_status_dict(existing_job_id)
                except JobNotFoundError:
                    existing = None
                if existing is not None and existing["state"] not in TERMINAL_JOB_STATES:
                    return JobRecord._from_dict(existing)

            job_id = f"job-{uuid.uuid4().hex}"
            now = _utcnow_iso()
            record = {
                "job_id": job_id, "identity": identity, "run_id": run_id,
                "input_digest": input_digest, "config_digest": config_digest,
                "state": JobState.STARTING, "created_at": now, "updated_at": now,
                "progress": {"tasks_total": None, "tasks_completed": [], "requests_used": 0},
                "cancel_requested": False, "error": None, "result": None,
            }
            self._write_status_dict(job_id, record)
            _write_atomic(self._identity_path(identity),
                          json.dumps({"job_id": job_id}).encode("utf-8"))
            return JobRecord._from_dict(record)

    def get(self, job_id: str) -> JobRecord:
        return JobRecord._from_dict(self._read_status_dict(job_id))

    def update(self, job_id: str, mutate: Callable[[dict], None]) -> JobRecord:
        """Read-modify-write under lock. A no-op once the job is terminal,
        so a late progress update can never override a terminal state."""
        with _flock_path(self._status_lock_path(job_id)):
            record = self._read_status_dict(job_id)
            if record["state"] in TERMINAL_JOB_STATES:
                return JobRecord._from_dict(record)
            mutate(record)
            record["updated_at"] = _utcnow_iso()
            self._write_status_dict(job_id, record)
            return JobRecord._from_dict(record)

    def request_cancel(self, job_id: str) -> JobRecord:
        """Idempotent: a no-op if the job is already terminal or already
        has a cancellation pending."""
        return self.update(job_id, lambda r: r.update(cancel_requested=True))


@dataclass(frozen=True)
class Task:
    """One unit of fetch work. `key` must be stable across job resumptions
    (the same task must produce the same key every time it is built)."""
    key: str
    payload: Any = None


@dataclass(frozen=True)
class JobBudget:
    max_requests: int
    max_retries: int = 2
    per_call_timeout: float = 30.0
    max_workers: int = 4
    # Bounded exponential backoff before each retry (not the first
    # attempt): retry_backoff_base * 2**(attempt-1), capped at
    # retry_backoff_max. 0 (the default) retries immediately, preserving
    # the original behavior (F9_STEP5_REVIEW.md S5-11).
    retry_backoff_base: float = 0.0
    retry_backoff_max: float = 0.0

    def __post_init__(self):
        if self.max_requests <= 0:
            raise JobError("max_requests must be positive")
        if self.max_retries < 0:
            raise JobError("max_retries must not be negative")
        if self.per_call_timeout <= 0:
            raise JobError("per_call_timeout must be positive")
        if self.max_workers <= 0:
            raise JobError("max_workers must be positive")
        if self.retry_backoff_base < 0:
            raise JobError("retry_backoff_base must not be negative")
        if self.retry_backoff_max < self.retry_backoff_base:
            raise JobError("retry_backoff_max must not be less than retry_backoff_base")


@dataclass(frozen=True)
class JobRunOutcome:
    results: dict
    incomplete_task_keys: tuple
    requests_used: int
    cancelled: bool


class _Budget:
    """Thread-safe check-and-increment counter, same race-free shape as
    cache.py's QuotaTracker.record_request, but in-process."""

    def __init__(self, max_requests: int, used: int = 0):
        self._max = max_requests
        self._used = used
        self._lock = threading.Lock()

    def try_consume(self) -> bool:
        with self._lock:
            if self._used >= self._max:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


def _attempt_task(task: Task, fetch_one: Callable[[Task], Any], budget: _Budget,
                   max_retries: int, cancel_check: Callable[[], bool], *,
                   retry_backoff_base: float = 0.0, retry_backoff_max: float = 0.0) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            if cancel_check():
                # A cancellation observed between retries stops this task
                # from issuing another call -- the attempt already in
                # flight when cancellation lands is still allowed to
                # finish, but no new one starts on its behalf
                # (F9_STEP5_REVIEW.md S5-08). Checked before backoff so
                # cancellation is never delayed behind a wait.
                raise _CancelledTaskError()
            if retry_backoff_base > 0:
                # Bounded exponential backoff before a retry, not the
                # first attempt (F9_STEP5_REVIEW.md S5-11): a real source
                # failure gets spaced-out retries instead of hammering it
                # immediately.
                time.sleep(min(retry_backoff_base * (2 ** (attempt - 1)), retry_backoff_max))
        if not budget.try_consume():
            raise _BudgetExhaustedError()
        try:
            return fetch_one(task)
        except FatalTaskError:
            raise
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
            # non-fatal fetch_one failure is retried up to max_retries, per
            # section 8's "retries" requirement.
            last_exc = exc
            continue
    raise last_exc


def run_result_job(store: JobStore, job_id: str, tasks: Sequence[Task],
                    fetch_one: Callable[[Task], Any], budget: JobBudget) -> JobRunOutcome:
    """Runs (or resumes) a job's fetch tasks. Raises JobError if the job is
    already terminal. Re-raises FatalTaskError after marking the job failed.
    """
    record = store.get(job_id)
    if record.is_terminal:
        raise JobError(f"job {job_id} is already terminal ({record.state})")

    completed_keys = set(record.progress.get("tasks_completed", []))
    pending = [task for task in tasks if task.key not in completed_keys]

    store.update(job_id, lambda r: (
        r.update(state=JobState.RUNNING),
        r["progress"].update(tasks_total=len(tasks)),
    ))

    budget_counter = _Budget(budget.max_requests, used=record.progress.get("requests_used", 0))
    results: dict[str, Any] = {}
    cancelled = False
    fatal_error: FatalTaskError | None = None

    def _cancel_check() -> bool:
        return store.get(job_id).cancel_requested

    index = 0
    # shutdown(wait=False) on exit, not the `with` form's wait=True: a
    # timed-out future's thread is abandoned (per this module's own
    # docstring above), but ThreadPoolExecutor.__exit__ otherwise blocks
    # the runner itself on that thread's completion regardless of
    # per_call_timeout, which contradicts "abandoned, not killed"
    # (F9_STEP5_REVIEW.md S5-08).
    pool = ThreadPoolExecutor(max_workers=budget.max_workers)
    try:
        while index < len(pending):
            if store.get(job_id).cancel_requested:
                cancelled = True
                break
            chunk = pending[index:index + budget.max_workers]
            index += len(chunk)
            futures = {
                pool.submit(_attempt_task, task, fetch_one, budget_counter,
                            budget.max_retries, _cancel_check,
                            retry_backoff_base=budget.retry_backoff_base,
                            retry_backoff_max=budget.retry_backoff_max): task
                for task in chunk
            }
            for future, task in futures.items():
                try:
                    result = future.result(timeout=budget.per_call_timeout)
                except FatalTaskError as exc:
                    fatal_error = fatal_error or exc
                    continue
                except Exception:
                    # Timeout, cancellation, budget exhaustion, or retries
                    # exhausted: this task stays incomplete, but siblings in
                    # the same chunk still get to finish and be recorded
                    # (evidence already fetched is preserved even when one
                    # task fails fatally).
                    continue
                else:
                    results[task.key] = result
                    completed_keys.add(task.key)
                    store.update(job_id, lambda r, k=task.key: (
                        r["progress"]["tasks_completed"].append(k),
                        r["progress"].update(requests_used=budget_counter.used),
                    ))
            if fatal_error is not None:
                break
    finally:
        pool.shutdown(wait=False)

    incomplete = tuple(task.key for task in pending if task.key not in completed_keys)

    if fatal_error is not None:
        store.update(job_id, lambda r: r.update(
            state=JobState.FAILED,
            error={"code": "fatal_task_error", "message": str(fatal_error)},
        ))
        raise fatal_error
    if cancelled:
        store.update(job_id, lambda r: r.update(state=JobState.CANCELLED))
    elif incomplete:
        store.update(job_id, lambda r: r.update(
            state=JobState.FAILED,
            error={"code": "budget_exhausted",
                   "message": f"{len(incomplete)} of {len(tasks)} tasks did not complete "
                              f"within the per-job request budget"},
        ))
    else:
        store.update(job_id, lambda r: r.update(
            state=JobState.SUCCEEDED,
            result={"completed_task_keys": sorted(completed_keys),
                    "requests_used": budget_counter.used},
        ))

    return JobRunOutcome(results=results, incomplete_task_keys=incomplete,
                          requests_used=budget_counter.used, cancelled=cancelled)
