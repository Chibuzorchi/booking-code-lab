"""Durable run registry + job lifecycle on top of the F3 worker foundation.

Layout (worker.py owns the provider-scoped run tree):
    results/runs/<provider>/<run_id>/
        status.json       registry-owned lifecycle record (atomic replace)
        status.lock       flock file serializing status read-modify-write
        launch.pending    diagnostic launch metadata (not an ownership authority)
        run.lock          inherited lifetime lock, acquired before publication
        worker.exit.json  supervisor-written completion evidence {exit_code}
        progress.json     worker-owned structured live counters (throttled)
        worker.log        worker stdout/stderr
        extracts/         the scan's committed extract (exactly one per run)
        codes/            the scan's code list

Writer ownership: the RunRegistry writes status.json/launch.pending and
nothing else; the worker writes progress.json/extracts/codes/worker.log and
the SUPERVISOR writes worker.exit.json. Live counter reads merge progress.json
into the status view but are never persisted into status.json until
finalization, so a progress update can never overwrite a cancellation or
terminal state. Counters are only ever stated when known: null means
"not available".

State machine:
    starting -> running -> succeeded | failed | cancelled | cancelled_unresolved | unknown
    starting -> launch_failed
    cancelled_unresolved -> cancelled   (once the worker is gone)
All other terminal states are immutable. "unknown" means completion evidence
was never established (fail closed; never inferred from file presence).

Recovery invariants:
- The host holds run.lock before publication and passes that descriptor to
  the supervisor and worker. A starting record cannot outlive ownership
  merely because a wall-clock timeout elapsed or a sidecar is missing.
- Recovery probes this run's lock, not the provider lock or saved PIDs.
- A worker that exited without completion evidence is "unknown", never
  "succeeded", regardless of what files exist.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .decorrelation import ExtractError, harvest_odds_band
from .worker import JobBusyError, LaunchError, ScanJob, WorkerHost

SCHEMA_VERSION = 1

STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_LAUNCH_FAILED = "launch_failed"
STATE_CANCELLED_UNRESOLVED = "cancelled_unresolved"
STATE_UNKNOWN = "unknown"

TERMINAL_STATES = {STATE_SUCCEEDED, STATE_FAILED, STATE_CANCELLED,
                   STATE_LAUNCH_FAILED, STATE_CANCELLED_UNRESOLVED,
                   STATE_UNKNOWN}
NONTERMINAL_STATES = {STATE_STARTING, STATE_RUNNING}

COUNTER_KEYS = ("tried", "found_ok", "qualifying",
                "excluded_simulations", "unpriced")

# Test seam: pause between status publication and worker launch so tests can
# deterministically exercise the launch window. Zero in production.
LAUNCH_PAUSE_ENV = "WORKER_REGISTRY_LAUNCH_PAUSE"

_RUN_ID_RE = re.compile(r"^run-(bet9ja|sportybet)-(\d{8}-\d{6})-([0-9a-f]{8})$")


class RunNotFoundError(KeyError):
    """No registered run with this id."""


class RunStateError(RuntimeError):
    """The requested operation is impossible in the run's current state."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def _parse_strict_json(raw: bytes):
    """Strict JSON parse of a byte snapshot (rejects Infinity/NaN tokens)."""

    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(raw.decode("utf-8"), parse_constant=invalid)


def _read_status(run_dir: Path) -> dict | None:
    try:
        raw = (run_dir / "status.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RunStateError(f"run status is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise RunStateError("run status must be an object")
    if data.get("run_id") != run_dir.name or data.get("provider") != run_dir.parent.name:
        raise RunStateError("run status identity does not match its directory")
    return data


@contextmanager
def _flock_path(path: Path):
    """Context manager: exclusive flock on path (serializes across processes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _empty_counters() -> dict:
    return {key: None for key in COUNTER_KEYS}


def _initial_status(provider: str, run_id: str, request: dict) -> dict:
    now = _utcnow()
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "provider": provider,
        "state": STATE_STARTING,
        "request": request,
        "started_at": now,
        "finished_at": None,
        "updated_at": now,
        "stop_reason": None,
        "counters": _empty_counters(),
        "error": None,
        "cancel_requested_at": None,
        "cancel_granted_at": None,
        "supervisor": {"pid": None, "exit_code": None},
        "worker": {"pid": None},
        "extract": None,
        "recovery_note": None,
    }


class RunRegistry:
    """Durable provider-scoped run registry driving WorkerHost.

    At most one live worker per provider (inherited-lock single-flight from
    worker.py). Every run is created with a unique directory; a start that
    loses the single-flight race raises JobBusyError WITHOUT creating a run
    record. Launch failures are persisted (state launch_failed) and returned,
    not raised; uncertain launch outcomes stay observable as "running" until
    ownership and completion are established.
    """

    def __init__(self, workspace: Path | str | None = None,
                 python: str | None = None, cancel_grace_s: float = 10.0,
                 report_timeout_s: float = 30.0, reap_timeout_s: float = 30.0):
        self.host = WorkerHost(workspace, python, report_timeout_s=report_timeout_s,
                               reap_timeout_s=reap_timeout_s)
        self.runs_root = self.host.runs_root()
        self.cancel_grace_s = cancel_grace_s
        self._jobs: dict[str, ScanJob] = {}
        self._guard = threading.RLock()
        self.recover()

    # ------------------------------------------------------------------ start

    def start_scan(self, provider: str, seed: str, *, max_qualifying: int = 0,
                   min_odds: float | None = None, max_odds: float | None = None,
                   max_codes: int | None = None, depth: int | None = None,
                   env_extra: dict | None = None) -> dict:
        """Start a provider scan job; returns the public status view.

        Raises JobBusyError (no run record created) when the provider already
        has an active job, ValueError for bad inputs. Launch failures are
        persisted (state launch_failed) and returned instead of raised;
        uncertain launch outcomes stay observable as "running".
        env_extra mirrors WorkerHost.start_scan: additional child environment
        entries that win over the isolated environment.
        """
        if not isinstance(seed, str) or not seed.strip():
            raise ValueError("seed must be a non-empty booking code")
        self.host.provider_root(provider)  # validates provider
        for name, value, minimum in (("max_qualifying", max_qualifying, 0),
                                     ("max_codes", max_codes, 1), ("depth", depth, 1)):
            if value is not None and (type(value) is not int or value < minimum):
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if max_qualifying is None:
            raise ValueError("max_qualifying must be an integer >= 0")
        for name, value in (("min_odds", min_odds), ("max_odds", max_odds)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value)):
                raise ValueError(f"{name} must be finite")
        if min_odds is not None and min_odds < 1:
            raise ValueError("min_odds must be >= 1")
        if min_odds is not None and max_odds is not None and 0 < max_odds < min_odds:
            raise ValueError("positive max_odds must be >= min_odds")
        run_id = self._new_run_id(provider)
        run_dir = self.runs_root / provider / run_id
        for _ in range(5):
            try:
                run_dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                run_id = self._new_run_id(provider)
                run_dir = self.runs_root / provider / run_id
        else:
            raise LaunchError(f"cannot allocate a fresh run dir for {provider}")
        with _flock_path(run_dir / "run.lock") as run_lock_fd:
            self._launch_scan(provider, seed, run_id, run_dir, run_lock_fd,
                                       max_qualifying, min_odds, max_odds, max_codes,
                                       depth, env_extra)
        # The host reference is closed; supervisors/workers retain theirs.
        return self.get(run_id)

    def _launch_scan(self, provider, seed, run_id, run_dir, run_lock_fd,
                     max_qualifying, min_odds, max_odds, max_codes, depth, env_extra):
        request = {"seed": seed, "max_qualifying": int(max_qualifying),
                   "min_odds": min_odds, "max_odds": max_odds,
                   "max_codes": max_codes, "depth": depth}
        # Diagnostic claim precedes publication. The inherited run.lock,
        # not the PID or timestamp here, is the ownership authority.
        _atomic_write_json(run_dir / "launch.pending",
                           {"pid": os.getpid(), "claimed_at": _utcnow()})
        _atomic_write_json(run_dir / "status.json",
                           _initial_status(provider, run_id, request))
        pause = float(os.environ.get(LAUNCH_PAUSE_ENV, "0"))
        if pause:
            time.sleep(pause)
        # Another registry may have cancelled (or otherwise settled) this run
        # during the launch window: do not launch a worker for a terminal run.
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir) or {}
            if current.get("state") in TERMINAL_STATES:
                self._clear_launch_claim(run_dir)
                return self._public(run_dir, current)
        try:
            job = self.host.start_scan(
                provider, seed, max_qualifying=max_qualifying,
                min_odds=min_odds, max_odds=max_odds, max_codes=max_codes,
                depth=depth, run_dir=run_dir, env_extra=env_extra,
                run_lock_fd=run_lock_fd)
        except JobBusyError:
            self._clear_launch_claim(run_dir)
            shutil.rmtree(run_dir, ignore_errors=True)
            raise
        except LaunchError as exc:
            self._clear_launch_claim(run_dir)
            if exc.uncertain:
                self._finish_launch_uncertain(run_dir, str(exc))
            else:
                self._finish_launch_failed(run_dir, str(exc))
            return self.get(run_id)
        self._clear_launch_claim(run_dir)
        with self._guard:
            self._jobs[run_id] = job
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir) or {}
            if current.get("state") not in TERMINAL_STATES:
                current.update({
                    "state": STATE_RUNNING,
                    "updated_at": _utcnow(),
                    "supervisor": {"pid": job.process.pid, "exit_code": None},
                    "worker": {"pid": job.worker_pid},
                })
                _atomic_write_json(run_dir / "status.json", current)
        self._start_finalizer(run_id, job, run_dir)
        return self.get(run_id)

    def _finish_launch_failed(self, run_dir: Path, message: str) -> None:
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir)
            if current is None or current.get("state") in TERMINAL_STATES:
                return
            current.update({
                "state": STATE_LAUNCH_FAILED,
                "finished_at": _utcnow(),
                "updated_at": _utcnow(),
                "stop_reason": "failure",
                "error": {"code": "launch_error", "message": message[:500]},
            })
            _atomic_write_json(run_dir / "status.json", current)

    def _finish_launch_uncertain(self, run_dir: Path, message: str) -> None:
        """The supervisor never acknowledged but a worker may hold the lock:
        keep the run observable (running) until ownership is established."""
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir)
            if current is None or current.get("state") in TERMINAL_STATES:
                return
            current.update({
                "state": STATE_RUNNING,
                "updated_at": _utcnow(),
                "error": {"code": "launch_unacknowledged",
                          "message": message[:500]},
                "recovery_note": "worker may be running and holding the "
                                 "provider lock",
            })
            _atomic_write_json(run_dir / "status.json", current)

    # ------------------------------------------------------------------- read

    def get(self, run_id: str) -> dict:
        """Public status view for one run; merges live worker counters."""
        run_dir = self._resolve_run_dir(run_id)
        status = _read_status(run_dir)
        if status is None:
            raise RunNotFoundError(run_id)
        status = self._reconcile(run_id, run_dir, status)
        return self._public(run_dir, status)

    def list_runs(self, provider: str | None = None,
                  state: str | None = None, limit: int | None = None) -> list[dict]:
        entries = []
        for run_dir in sorted(self._run_dirs(), reverse=True):
            if provider is not None and run_dir.parent.name != provider:
                continue
            try:
                status = _read_status(run_dir)
            except RunStateError:
                continue
            if status is None:
                continue
            status = self._reconcile(run_dir.name, run_dir, status)
            public = self._public(run_dir, status)
            if state is not None and public.get("state") != state:
                continue
            entries.append(public)
        entries.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return entries[:limit] if limit is not None else entries

    def active_run(self, provider: str) -> dict | None:
        """The provider's nonterminal (or unresolved) run, newest first."""
        for run in self.list_runs(provider=provider):
            if run.get("state") in NONTERMINAL_STATES | {STATE_CANCELLED_UNRESOLVED}:
                return run
        return None

    def committed_extract(self, run_id: str) -> Path:
        """The ONE extract registered for a successful run; never discovered
        by globbing, and re-verified against the committed digest and size so
        a replaced or mutated artifact is refused."""
        return self.committed_extract_snapshot(run_id)[0]

    def committed_extract_snapshot(self, run_id: str) -> tuple[Path, bytes]:
        """Return the exact verified bytes, avoiding a check-then-reopen race."""
        status = self.get(run_id)
        extract = status.get("extract") or {}
        path = extract.get("path")
        if not path or status.get("state") != STATE_SUCCEEDED:
            raise RunStateError(f"run {run_id} has no committed extract")
        candidate = Path(path).resolve()
        run_dir = self._resolve_run_dir(run_id)
        if not str(candidate).startswith(str(run_dir.resolve()) + os.sep) \
                or not candidate.is_file():
            raise RunStateError(f"run {run_id}: committed extract missing or misplaced")
        try:
            raw = candidate.read_bytes()
        except OSError as exc:
            raise RunStateError("committed extract cannot be read") from exc
        if hashlib.sha256(raw).hexdigest() != extract.get("sha256") \
                or len(raw) != extract.get("size"):
            raise RunStateError(
                f"run {run_id}: committed extract changed after commitment")
        return candidate, raw

    # ----------------------------------------------------------------- cancel

    def cancel(self, run_id: str) -> dict:
        """Request cancellation. Idempotent; terminal runs are no-ops.

        Persists cancel_requested_at BEFORE signalling (persist-then-signal),
        so a completion race can never mark a requested cancellation as a
        natural success. With a live local job the supervisor is signalled
        and, after a bounded grace, escalation asks that same supervisor to
        kill its own child. WITHOUT a control channel
        (no local job) the request is recorded and the run becomes
        cancelled_unresolved: the owning finalizer (or recovery) consumes the
        request, and the run settles to cancelled only when its worker is
        actually gone — never masquerading a request as an executed cancel.
        """
        run_dir = self._resolve_run_dir(run_id)
        with _flock_path(run_dir / "status.lock"):
            status = _read_status(run_dir)
            if status is None:
                raise RunNotFoundError(run_id)
            if status.get("state") in TERMINAL_STATES:
                return self._public(run_dir, status)
            if not status.get("cancel_requested_at"):
                status["cancel_requested_at"] = _utcnow()
                status["updated_at"] = _utcnow()
            job = self._jobs.get(run_id)
            if job is None:
                status.update({
                    "state": STATE_CANCELLED_UNRESOLVED,
                    "updated_at": _utcnow(),
                    "stop_reason": "cancelled",
                    "error": {"code": "cancel_control_unavailable",
                              "message": "cancel request recorded, but this "
                                         "process cannot signal the run's "
                                         "owner; the run will settle to "
                                         "cancelled once its worker exits"},
                    "recovery_note": "worker may still be running and holding "
                                     "the provider lock",
                })
            _atomic_write_json(run_dir / "status.json", status)
        if job is not None and job.process.poll() is None:
            job.terminate()  # SIGTERM supervisor -> forwards to the worker
        return self.get(run_id)

    def _start_escalator(self, run_id: str, job, run_dir: Path) -> None:
        def escalate():
            deadline = time.monotonic() + self.cancel_grace_s
            while job.worker_alive() and time.monotonic() < deadline:
                time.sleep(0.2)
            if job.worker_settled or not job.worker_alive():
                return  # finalizer settles it as cancelled
            try:
                signalled = job.kill_worker()
            except OSError:
                signalled = False
            if signalled:
                deadline = time.monotonic() + self.cancel_grace_s
                while job.worker_alive() and time.monotonic() < deadline:
                    time.sleep(0.2)
                if not job.worker_alive():
                    return
            self._mark_unresolved(run_id, run_dir)
            while job.worker_alive():
                time.sleep(0.5)
            self._settle_unresolved(run_id, run_dir, job)

        threading.Thread(target=escalate, name=f"escalate-{run_id}",
                         daemon=True).start()

    def _mark_unresolved(self, run_id: str, run_dir: Path) -> None:
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir)
            if current is None or current.get("state") not in NONTERMINAL_STATES:
                return
            current.update({
                "state": STATE_CANCELLED_UNRESOLVED,
                "updated_at": _utcnow(),
                "stop_reason": "cancelled",
                "error": {"code": "cancel_unresolved",
                          "message": "cancel requested but the worker could not "
                                     "be safely terminated; ownership retained"},
                "recovery_note": "worker may still be running and holding the "
                                 "provider lock",
            })
            _atomic_write_json(run_dir / "status.json", current)

    def _settle_unresolved(self, run_id: str, run_dir: Path, job=None) -> None:
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir)
            if current is None or current.get("state") != STATE_CANCELLED_UNRESOLVED:
                return
            progress = self._read_progress(run_dir)
            current.update({
                "state": STATE_CANCELLED,
                "finished_at": _utcnow(),
                "updated_at": _utcnow(),
                "stop_reason": "cancelled",
                "cancel_granted_at": _utcnow(),
                "error": None, "extract": None,
                "counters": self._counters_from(progress) if progress else current.get("counters"),
                "supervisor": {"pid": (job.process.pid if job else None),
                               "exit_code": (job.exit_code if job else None)},
            })
            _atomic_write_json(run_dir / "status.json", current)

    # ------------------------------------------------------------ finalization

    def _start_finalizer(self, run_id: str, job, run_dir: Path) -> None:
        def finalize():
            signalled = False
            while not job.worker_settled:
                time.sleep(0.2)
                # Consume cancellation requests persisted by ANY registry:
                # the owning lifecycle component acts on them.
                if not signalled:
                    status = _read_status(run_dir)
                    if status and status.get("cancel_requested_at"):
                        signalled = True
                        if job.process.poll() is None:
                            job.terminate()
                        self._start_escalator(run_id, job, run_dir)
            try:
                self._finalize(run_id, job, run_dir)
            finally:
                with self._guard:
                    self._jobs.pop(run_id, None)

        threading.Thread(target=finalize, name=f"finalize-{run_id}",
                         daemon=True).start()

    def _finalize(self, run_id: str, job, run_dir: Path) -> None:
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir)
            if current is None:
                return
            if current.get("state") not in NONTERMINAL_STATES | {STATE_CANCELLED_UNRESOLVED}:
                return
            outcome = self._decide_outcome(
                run_dir, job.provider, rc=job.exit_code,
                cancel_requested=bool(current.get("cancel_requested_at")))
            merged = {**current, **outcome}
            merged["supervisor"] = {"pid": job.process.pid, "exit_code": job.exit_code}
            merged["worker"] = {"pid": job.worker_pid}
            _atomic_write_json(run_dir / "status.json", merged)

    def _decide_outcome(self, run_dir: Path, provider: str, rc: int | None,
                        cancel_requested: bool) -> dict:
        """Worker is gone: decide the terminal outcome from durable evidence.

        rc comes from the live supervisor when known; otherwise the
        supervisor's own completion evidence (worker.exit.json) is required.
        Success is NEVER inferred from an extract's presence alone.
        """
        progress = self._read_progress(run_dir)
        counters = self._counters_from(progress) if progress else None
        if cancel_requested:
            return {"state": STATE_CANCELLED, "finished_at": _utcnow(),
                    "updated_at": _utcnow(), "stop_reason": "cancelled",
                    "counters": counters, "extract": None,
                    "cancel_granted_at": _utcnow(), "error": None}
        if rc is None:
            evidence = self._read_worker_exit(run_dir)
            if evidence is None:
                return {"state": STATE_UNKNOWN, "finished_at": _utcnow(),
                        "updated_at": _utcnow(), "stop_reason": "unknown",
                        "counters": counters, "extract": None,
                        "error": {"code": "completion_unknown",
                                  "message": "no worker completion evidence; "
                                             "outcome unknown (fail closed)"}}
            rc = evidence["exit_code"]
        if rc != 0:
            return {"state": STATE_FAILED, "finished_at": _utcnow(),
                    "updated_at": _utcnow(), "stop_reason": "failure",
                    "counters": counters, "extract": None,
                    "error": {"code": "worker_exit",
                              "message": f"worker exited with code {rc}"}}
        candidates = sorted((run_dir / "extracts").glob("*.json"))
        if len(candidates) != 1:
            code = "missing_extract" if not candidates else "multiple_extracts"
            message = ("worker produced no extract"
                       if not candidates else
                       "worker produced multiple extracts; none committed")
            return {"state": STATE_FAILED, "finished_at": _utcnow(),
                    "updated_at": _utcnow(), "stop_reason": "failure",
                    "counters": counters, "extract": None,
                    "error": {"code": code, "message": message}}
        path = candidates[0]
        try:
            if not path.resolve().is_relative_to(run_dir.resolve()):
                raise ValueError("extract escapes its run directory")
            raw = path.read_bytes()
            data = _parse_strict_json(raw)
        except (OSError, ValueError) as exc:
            return {"state": STATE_FAILED, "finished_at": _utcnow(),
                    "updated_at": _utcnow(), "stop_reason": "failure",
                    "counters": counters, "extract": None,
                    "error": {"code": "invalid_extract",
                              "message": f"extract unreadable: {exc}"[:400]}}
        problem = self._validate_extract(data, provider)
        if problem:
            return {"state": STATE_FAILED, "finished_at": _utcnow(),
                    "updated_at": _utcnow(), "stop_reason": "failure",
                    "counters": counters, "extract": None, "error": problem}
        try:
            policy = harvest_odds_band(data)
        except ExtractError as exc:
            return {"state": STATE_FAILED, "finished_at": _utcnow(),
                    "updated_at": _utcnow(), "stop_reason": "failure",
                    "counters": counters, "extract": None,
                    "error": {"code": "invalid_extract", "message": str(exc)}}
        digest = hashlib.sha256(raw).hexdigest()
        return {"state": STATE_SUCCEEDED, "finished_at": _utcnow(),
                "updated_at": _utcnow(),
                "stop_reason": data.get("stop_reason") or "unknown",
                "counters": {"tried": data.get("tried"),
                             "found_ok": data.get("found_ok"),
                             "qualifying": len(data["qualifying"]),
                             "excluded_simulations": data.get("excluded_simulations"),
                             "unpriced": data.get("unpriced_excluded")},
                "extract": {"path": str(path.resolve()), "sha256": digest,
                            "size": len(raw),
                            "validated_at": _utcnow(), "policy": policy}}

    @staticmethod
    def _validate_extract(data, provider) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("qualifying"), list):
            return {"code": "invalid_extract",
                    "message": "extract must contain a qualifying array"}
        if data.get("provider") != provider:
            return {"code": "invalid_extract",
                    "message": "extract provider does not match the run provider"}
        if data.get("odds_basis") != "parsed_leg_product":
            return {"code": "invalid_extract",
                    "message": "extract carries no parsed_leg_product odds basis"}
        return None

    # --------------------------------------------------------------- recovery

    def recover(self) -> list[dict]:
        """Reconcile nonterminal records against durable evidence."""
        reconciled = []
        for run_dir in sorted(self._run_dirs()):
            try:
                status = _read_status(run_dir)
            except RunStateError:
                continue
            if status is None:
                continue
            run_id = run_dir.name
            new = self._reconcile(run_id, run_dir, status)
            if new is not status:
                reconciled.append(self._public(run_dir, new))
        return reconciled

    def _reconcile(self, run_id: str, run_dir: Path, status: dict) -> dict:
        state = status.get("state")
        if state not in NONTERMINAL_STATES | {STATE_CANCELLED_UNRESOLVED}:
            return status
        if run_id in self._jobs:
            return status
        if self.host._lock_held(run_dir / "run.lock"):
            # Covers publication, Popen, acknowledgement, and orphan execution.
            # Sidecar contents and provider-wide ownership are not evidence for
            # this run; a missing/stale sidecar cannot release run ownership.
            if state == STATE_RUNNING and not status.get("recovery_note"):
                with _flock_path(run_dir / "status.lock"):
                    current = _read_status(run_dir)
                    if current and current.get("state") == STATE_RUNNING:
                        current["recovery_note"] = "ownership held by this run's live worker"
                        current["updated_at"] = _utcnow()
                        _atomic_write_json(run_dir / "status.json", current)
                return _read_status(run_dir) or status
            return status
        if state == STATE_CANCELLED_UNRESOLVED:
            self._settle_unresolved(run_id, run_dir)
            return _read_status(run_dir) or status
        return self._finalize_from_disk(run_id, run_dir, status)

    def _finalize_from_disk(self, run_id: str, run_dir: Path, status: dict) -> dict:
        with _flock_path(run_dir / "status.lock"):
            current = _read_status(run_dir)
            if current is None or current.get("state") not in NONTERMINAL_STATES:
                return current or status
            provider = current.get("provider") or _RUN_ID_RE.match(run_id).group(1)
            outcome = self._decide_outcome(
                run_dir, provider, rc=None,
                cancel_requested=bool(current.get("cancel_requested_at")))
            merged = {**current, **outcome}
            merged["recovery_note"] = ("finalized from disk after restart; "
                                       "worker exit evidence from disk")
            _atomic_write_json(run_dir / "status.json", merged)
            return merged

    # ---------------------------------------------------------------- helpers

    def _run_dirs(self) -> list[Path]:
        if not self.runs_root.is_dir():
            return []
        dirs = []
        for provider_dir in sorted(self.runs_root.iterdir()):
            if not provider_dir.is_dir():
                continue
            for run_dir in sorted(provider_dir.iterdir()):
                if run_dir.is_dir() and _RUN_ID_RE.match(run_dir.name):
                    dirs.append(run_dir)
        return dirs

    def _resolve_run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str):
            raise ValueError(f"invalid run id: {run_id!r}")
        match = _RUN_ID_RE.match(run_id)
        if not match:
            raise ValueError(f"invalid run id: {run_id!r}")
        candidate = (self.runs_root / match.group(1) / run_id).resolve()
        root = (self.runs_root / match.group(1)).resolve()
        if (root.parent != self.runs_root.resolve() or
                candidate.parent != root or candidate.name != run_id):
            raise ValueError(f"run id escapes the registry root: {run_id!r}")
        return candidate

    def _public(self, run_dir: Path, status: dict) -> dict:
        if status.get("state") in NONTERMINAL_STATES:
            progress = self._read_progress(run_dir)
            if progress:
                view = dict(status)
                counters = dict(view["counters"])
                for key in COUNTER_KEYS:
                    if progress.get(key) is not None:
                        counters[key] = progress[key]
                view["counters"] = counters
                view["live_progress_at"] = progress.get("updated_at")
                return view
        return status

    @staticmethod
    def _read_progress(run_dir: Path) -> dict | None:
        try:
            data = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _read_worker_exit(run_dir: Path) -> dict | None:
        try:
            data = json.loads((run_dir / "worker.exit.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (isinstance(data, dict) and type(data.get("exit_code")) is int
                and data.get("run_id") == run_dir.name
                and data.get("provider") == run_dir.parent.name):
            return data
        return None

    @staticmethod
    def _clear_launch_claim(run_dir: Path) -> None:
        try:
            (run_dir / "launch.pending").unlink()
        except OSError:
            pass

    @staticmethod
    def _counters_from(progress: dict) -> dict:
        counters = _empty_counters()
        for key in COUNTER_KEYS:
            if progress.get(key) is not None:
                counters[key] = progress[key]
        return counters

    @staticmethod
    def _new_run_id(provider: str) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        return f"run-{provider}-{stamp}-{uuid.uuid4().hex[:8]}"
