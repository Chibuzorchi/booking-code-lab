"""Provider job workers: run each provider's scan/booking code in its own
subprocess with an isolated environment and a per-provider single-flight lock.

Why subprocesses: each provider's scan_code inserts its own root on sys.path
(bet9ja/scripts/scan_code.py:13, sportybet/scripts/scan_code.py:15) and loads
.env with load_dotenv(override=False) (engine/env.py:19 both), so a process
that has already loaded one provider's environment or modules cannot safely
run the other. This runner therefore NEVER imports provider packages into the
server process: it only builds argument lists and spawns interpreters.

Environment isolation is explicit: changing cwd alone does not remove
inherited variables. The child environment strips the complete provider
configuration inventory (every key read by provider settings/env code, plus
every key any provider .env/.env.<name> file defines, plus PYTHONPATH/
PYTHONHOME/BET_ENV), so the child's own load_dotenv(override=False) can
populate those names from ITS .env instead of inheriting previous values.
Callers may re-add keys via env_extra, which wins.

Single-flight: the flock is opened and held by a tiny SUPERVISOR process, and
the WORKER inherits the same lock descriptor and retains it for its whole
lifetime (flock references survive across fork/exec; the lock is released
only when every reference closes, i.e. when the worker exits). Consequences:
- Host death during startup or at any later time cannot release the lock
  while the worker lives — a closed report pipe is a host disconnect, not
  permission to abandon ownership; the supervisor keeps waiting on the worker.
- A supervisor killed while its worker lives leaves the lock held by the
  worker: replacement starts fail closed (JobBusyError) until that worker
  exits. No recovery ever signals a process identified only by a saved PID,
  so PID reuse cannot harm unrelated processes.
- Normal worker exit, forwarded terminate, or worker death all release the
  lock automatically via the kernel.

F3 scope: launch + env isolation + per-provider single-flight + ownership
release on launch failure and worker exit. Durable status.json, cancellation
semantics, API routes and the UI remain F5-F7 and build on this foundation.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import select
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

PROVIDER_NAMES = ("bet9ja", "sportybet")
SCAN_MODULE = "scripts.scan_code"

# Keys that steer import resolution or .env file selection rather than
# provider configuration; removing them keeps the child hermetic.
_STRUCTURAL_ENV = ("PYTHONPATH", "PYTHONHOME", "BET_ENV")

# Every key read by provider settings/env code (engine/settings.py and
# infra/config/settings.py for both providers), independent of what the local
# dotenv files happen to contain. Locale/timezone/request-timeout are absent
# from both .env files today but are still provider configuration.
PROVIDER_CONFIG_KEYS = frozenset({
    "BROWSER_CHANNEL", "CACHE_VERSION", "CHARSET", "COMPETITION_ANCHOR_ID",
    "HEADLESS", "LEAGUE_NAME", "LEAGUE_TOGGLE_ID", "LOCALE",
    "MAX_CODES_TO_TRY", "MAX_LEGS", "MAX_MUTATION_DEPTH", "MAX_QUALIFYING",
    "MAX_TOTAL_ODDS", "MIN_LEGS", "MIN_TOTAL_ODDS", "PROVIDER",
    "REQUEST_DELAY_MS", "REQUEST_TIMEOUT_S", "REQUEST_WORKERS", "RESULTS_DIR",
    "SEED_CODE", "SITE_URL", "SPORT", "SPORTS_URL", "TIMEZONE_ID", "TOP_N",
    "USER_AGENT",
})

# Test seam: the supervisor sleeps between spawning the worker and publishing
# the sidecar, so tests can deterministically exercise supervisor death in
# that window. Zero in production.
SUPERVISOR_PAUSE_ENV = "WORKER_SUPERVISOR_PAUSE_AFTER_SPAWN"

_ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class UnknownProviderError(ValueError):
    """The workspace has no scan entrypoint for the named provider."""


class JobBusyError(RuntimeError):
    """Another job for this provider holds the single-flight lock."""

    def __init__(self, provider: str, owner: dict | None = None):
        self.provider = provider
        self.owner = owner
        detail = f" (owner: {owner})" if owner else ""
        super().__init__(f"provider {provider!r} already has an active job{detail}")


class LaunchError(RuntimeError):
    """The worker could not be started.

    uncertain=True means a worker may still be running and holding the
    provider lock (ownership retained); uncertain=False is a definitive
    launch failure with nothing left running.
    """

    def __init__(self, message: str, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


def default_workspace() -> Path:
    """Repo root holding bet9ja/, sportybet/ and ticket-analysis/."""
    return Path(__file__).resolve().parents[2]


def dotenv_keys(path: Path) -> set[str]:
    """Keys a dotenv file would set; only the left-hand identifiers matter."""
    keys: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return keys
    for line in text.splitlines():
        match = _ENV_KEY.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def provider_env_keys(workspace: Path) -> set[str]:
    """Union of keys every provider .env/.env.<name> file under workspace sets."""
    keys: set[str] = set()
    for provider in PROVIDER_NAMES:
        root = workspace / provider
        if not root.is_dir():
            continue
        for candidate in root.glob(".env*"):
            if candidate.name == ".env" or candidate.name.startswith(".env."):
                keys |= dotenv_keys(candidate)
    return keys


def isolated_env(workspace: Path, extra: dict | None = None) -> dict[str, str]:
    """Parent environment minus provider-config keys, plus explicit extras.

    Stripping the static inventory AND every key a provider .env defines is
    what makes load_dotenv(override=False) safe in the child: the child's own
    loader can now populate those names, and keys its .env does not define
    stay unset so code defaults apply. Over-stripping is harmless for the
    same reason.
    """
    env = dict(os.environ)
    for key in PROVIDER_CONFIG_KEYS | provider_env_keys(workspace):
        env.pop(key, None)
    for key in _STRUCTURAL_ENV:
        env.pop(key, None)
    if extra:
        env.update({str(k): str(v) for k, v in extra.items() if v is not None})
    return env


def scan_argv(python: str, seed: str, run_dir: Path, *, max_qualifying: int = 0,
              min_odds: float | None = None, max_odds: float | None = None,
              max_codes: int | None = None, depth: int | None = None) -> list[str]:
    """Argument list for scripts.scan_code --scan; never passed to a shell.

    Flag semantics verified in both scan_code CLIs: --max-qualifying sets
    settings.max_qualifying (0 = uncapped; negatives rejected at the CLI) and
    --run-dir sets settings.results_dir so the extract is written under the
    run folder.
    """
    argv = [python, "-m", SCAN_MODULE, seed, "--scan",
            "--max-qualifying", str(int(max_qualifying)),
            "--run-dir", str(run_dir)]
    if min_odds is not None:
        argv += ["--min-odds", str(min_odds)]
    if max_odds is not None:
        argv += ["--max-odds-cap", str(max_odds)]
    if max_codes is not None:
        argv += ["--max-codes", str(int(max_codes))]
    if depth is not None:
        argv += ["--depth", str(int(depth))]
    return argv


# Runs in its own interpreter process. It opens and flocks the per-provider
# lock file, then spawns the worker WITH THE LOCK DESCRIPTOR INHERITED, so the
# worker itself keeps the lock alive for its whole lifetime. It reports
# ok/busy/error over the report pipe, forwards SIGTERM to the worker, and
# unlinks the owner sidecar before releasing its own descriptor — but a
# closed report pipe is only a host disconnect: ownership continues with the
# worker, and this supervisor keeps waiting on it.
_SUPERVISOR_RAW = r"""
import fcntl, json, os, signal, subprocess, sys, time


def main():
    lock_path, pipe_fd, owner_path, log_path, owner_json = sys.argv[1:6]
    pipe_fd = int(pipe_fd)
    argv = sys.argv[6:]
    pipe = os.fdopen(pipe_fd, "w", buffering=1)

    def report(payload):
        try:
            pipe.write(json.dumps(payload) + "\n")
        except (BrokenPipeError, OSError):
            pass

    fd = None
    run_fd = None
    wrote_sidecar = False
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            previous = None
            try:
                with open(owner_path) as stream:
                    previous = json.load(stream)
            except (OSError, ValueError):
                pass
            report({"ok": False, "error": "busy", "owner": previous})
            return 1
        owner = json.loads(owner_json)
        run_fd = owner.pop("run_lock_fd", None)
        if run_fd is None:
            run_fd = os.open(os.path.join(os.path.dirname(log_path), "run.lock"),
                             os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(run_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log = open(log_path, "wb")
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        child = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                 pass_fds=(fd, run_fd))
        pause = float(os.environ.get("__PAUSE_ENV__", "0"))
        if pause:
            time.sleep(pause)
        owner["pid"] = os.getpid()
        owner["worker_pid"] = child.pid
        try:
            with open(owner_path, "w") as stream:
                json.dump(owner, stream)
        except OSError as exc:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            report({"ok": False, "error": "sidecar: " + str(exc)})
            return 1
        wrote_sidecar = True

        def forward(signum, frame):
            child.terminate()

        def force(signum, frame):
            child.kill()

        signal.signal(signal.SIGTERM, forward)
        signal.signal(signal.SIGUSR1, force)
        report({"ok": True, "worker_pid": child.pid})
        rc = child.wait()
        # Durable, run-specific completion evidence for crash recovery: the
        # registry never infers success from an extract's presence alone.
        try:
            evidence = {"provider": owner["provider"], "run_id": owner["run_id"],
                        "worker_pid": child.pid, "exit_code": rc,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            exit_path = os.path.join(os.path.dirname(log_path), "worker.exit.json")
            tmp = exit_path + ".tmp"
            with open(tmp, "w") as stream:
                json.dump(evidence, stream)
            os.replace(tmp, exit_path)
        except OSError:
            pass
        return rc
    finally:
        if wrote_sidecar:
            try:
                os.unlink(owner_path)
            except OSError:
                pass
        if fd is not None:
            os.close(fd)
        if run_fd is not None:
            os.close(run_fd)
        try:
            pipe.close()
        except OSError:
            pass


os._exit(main())
"""

_SUPERVISOR = _SUPERVISOR_RAW.replace("__PAUSE_ENV__", SUPERVISOR_PAUSE_ENV)


def supervisor_command(python: str, lock_file: Path, pipe_fd: int,
                       owner_file: Path, log_path: Path, owner: dict,
                       worker_argv: list[str]) -> list[str]:
    """Argument list for the supervisor process; also used by lifecycle tests."""
    return [python, "-c", _SUPERVISOR, str(lock_file), str(pipe_fd),
            str(owner_file), str(log_path), json.dumps(owner), *worker_argv]


@dataclass
class ScanJob:
    provider: str
    run_id: str
    run_dir: Path
    seed: str
    argv: list
    process: subprocess.Popen
    worker_pid: int
    started_at: str
    exit_code: int | None = None
    worker_settled: bool = False

    def poll(self) -> int | None:
        """Supervisor exit code; in normal operation the supervisor lives
        exactly as long as the worker, so this proxies the worker's rc."""
        return self.process.poll()

    def is_running(self) -> bool:
        """True while the supervisor OR the worker is still alive.

        A supervisor killed out-of-band leaves the worker running (and still
        holding the provider lock) — the job counts as running in that case
        too, until the worker is gone.
        """
        return self.process.poll() is None or self.worker_alive()

    def worker_alive(self) -> bool:
        """Advisory liveness of the reported worker PID; bookkeeping only.

        The pid came from the supervisor's live start report, never read back
        from disk, and is never used to signal anything. A recycled pid can
        only delay settlement, never cause harm.
        """
        try:
            os.kill(self.worker_pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the SUPERVISOR; worker completion is tracked separately
        (worker_settled / worker_alive / WorkerHost.lock_held)."""
        return self.process.wait(timeout=timeout)

    def terminate(self) -> None:
        """Cancel through the supervisor, which forwards SIGTERM to the worker.

        A no-op once the supervisor is gone (killed out-of-band): escalation
        against the live-reported worker PID belongs to F5's cancel semantics.
        """
        if self.process.poll() is None:
            self.process.terminate()

    def kill_worker(self) -> bool:
        """Ask the live supervisor to kill its own child; never use a saved PID."""
        if self.process.poll() is not None:
            return False
        self.process.send_signal(signal.SIGUSR1)
        return True


class WorkerHost:
    """Launches and owns provider worker subprocesses for one workspace.

    At most one active job per provider. The worker itself holds the flock
    for its whole lifetime, so single-flight holds across processes, survives
    host death, and never depends on signalling a PID read back from disk.
    Provider packages are never imported here.
    """

    def __init__(self, workspace: Path | str | None = None,
                 python: str | None = None, report_timeout_s: float = 30.0,
                 reap_timeout_s: float = 30.0):
        self.workspace = Path(workspace).resolve() if workspace else default_workspace()
        self.python = python or sys.executable
        self.report_timeout_s = report_timeout_s
        self.reap_timeout_s = reap_timeout_s
        self._jobs: dict[str, ScanJob] = {}
        self._jobs_guard = threading.Lock()

    def runs_root(self) -> Path:
        return self.workspace / "results" / "runs"

    def provider_root(self, provider: str) -> Path:
        if provider not in PROVIDER_NAMES:
            raise UnknownProviderError(f"unknown provider {provider!r}")
        root = self.workspace / provider
        entrypoint = root / (SCAN_MODULE.replace(".", "/") + ".py")
        if not entrypoint.is_file():
            raise UnknownProviderError(f"no {SCAN_MODULE}.py under {root}")
        return root.resolve()

    def active_job(self, provider: str) -> ScanJob | None:
        job = self._jobs.get(provider)
        if job is not None and job.worker_settled and job.exit_code is not None:
            self._jobs.pop(provider, None)
            return None
        return job

    def lock_held(self, provider: str) -> bool:
        """Authoritative, PID-free: some worker or supervisor holds the
        provider lock, so no new job may start for this provider."""
        return self._lock_held(self.runs_root() / provider / "job.lock")

    def start_scan(self, provider: str, seed: str, *, max_qualifying: int = 0,
                   min_odds: float | None = None, max_odds: float | None = None,
                   max_codes: int | None = None, depth: int | None = None,
                   env_extra: dict | None = None, run_dir: Path | None = None,
                   run_lock_fd: int | None = None) -> ScanJob:
        if not isinstance(seed, str) or not seed.strip():
            raise ValueError("seed must be a non-empty booking code")
        root = self.provider_root(provider)
        lock_file = self.runs_root() / provider / "job.lock"
        owner_file = lock_file.with_name("job.owner.json")
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Allocate an ABSOLUTE run dir; only a directory successfully created
        # HERE is owned by this call and may ever be removed by it. A supplied
        # run_dir that already exists is ADOPTED: the caller (e.g. the run
        # registry) owns its lifecycle, and this call never deletes it.
        owned_dir: Path | None = None
        adopted = False
        if run_dir is None:
            for _ in range(5):
                candidate = self.runs_root() / provider / self._new_run_id(provider)
                try:
                    candidate.mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    continue
                owned_dir = candidate
                break
            else:
                raise LaunchError(f"cannot allocate a fresh run dir for {provider}")
        else:
            supplied = Path(run_dir).resolve()
            if supplied.exists():
                if not supplied.is_dir():
                    raise LaunchError(f"run dir path is not a directory: {supplied}")
                owned_dir = supplied
                adopted = True
            else:
                try:
                    supplied.mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    raise LaunchError(f"run dir already exists: {supplied}")
                owned_dir = supplied

        argv = scan_argv(self.python, seed, owned_dir, max_qualifying=max_qualifying,
                         min_odds=min_odds, max_odds=max_odds,
                         max_codes=max_codes, depth=depth)
        env = isolated_env(self.workspace, env_extra)
        owner = {"provider": provider, "run_id": owned_dir.name,
                 "started_at": started_at}
        if run_lock_fd is not None:
            owner["run_lock_fd"] = run_lock_fd

        pipe_r, pipe_w = os.pipe()
        supervisor_argv = supervisor_command(
            self.python, lock_file, pipe_w, owner_file, owned_dir / "worker.log",
            owner, argv)
        try:
            try:
                process = subprocess.Popen(supervisor_argv, cwd=root, env=env,
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,
                                           pass_fds=(pipe_w,) + ((run_lock_fd,) if run_lock_fd is not None else ()))
            except OSError as exc:
                raise LaunchError(f"cannot start worker for {provider}: {exc}") from exc
        except BaseException:
            os.close(pipe_r)
            os.close(pipe_w)
            if not adopted:
                shutil.rmtree(owned_dir, ignore_errors=True)
            raise
        os.close(pipe_w)

        report = self._read_report(pipe_r, timeout=self.report_timeout_s)
        os.close(pipe_r)
        if report is None:
            # Supervisor died before reporting, or never reported in time.
            # Its worker may still be running AND holding the lock: decide
            # by probing the lock, never by trusting a saved PID.
            self._reap(process)
            if self._lock_held(lock_file):
                raise LaunchError(
                    f"worker supervisor for {provider} never reported and the "
                    f"provider lock is still held by a live worker; run dir "
                    f"{owned_dir} retained", uncertain=True)
            if not adopted:
                shutil.rmtree(owned_dir, ignore_errors=True)
            raise LaunchError(f"worker supervisor for {provider} exited before reporting")
        if not report.get("ok"):
            self._reap(process)
            if not adopted:
                shutil.rmtree(owned_dir, ignore_errors=True)
            if report.get("error") == "busy":
                raise JobBusyError(provider, report.get("owner"))
            raise LaunchError(f"worker supervisor for {provider} failed: "
                              f"{report.get('error')}")

        job = ScanJob(provider=provider, run_id=owned_dir.name, run_dir=owned_dir,
                      seed=seed, argv=argv, process=process,
                      worker_pid=report["worker_pid"], started_at=started_at)
        with self._jobs_guard:
            self._jobs[provider] = job
        self._start_reaper(job)
        return job

    def collect(self, job: ScanJob, timeout: float | None = None) -> int:
        """Wait for the SUPERVISOR and record its exit code.

        On the normal path the supervisor exits only after the worker, so
        this settles the worker too. If the supervisor was killed
        out-of-band, the worker may still run and hold the lock:
        worker_settled stays False until the worker is gone, and the job
        remains observable via active_job()/worker_alive()/lock_held().
        """
        rc = job.process.wait(timeout=timeout)
        job.exit_code = rc
        if not job.worker_alive():
            job.worker_settled = True
        return rc

    @staticmethod
    def _read_report(pipe_r: int, timeout: float) -> dict | None:
        ready, _, _ = select.select([pipe_r], [], [], timeout)
        if not ready:
            return None
        try:
            data = os.read(pipe_r, 65536)
        except OSError:
            return None
        if not data:
            return None
        try:
            return json.loads(data.decode("utf-8", "replace").splitlines()[0])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _lock_held(lock_file: Path) -> bool:
        """Probe whether any process holds the provider flock (PID-free)."""
        try:
            fd = os.open(lock_file, os.O_RDWR)
        except FileNotFoundError:
            return False
        except OSError:
            return True  # inaccessible ownership evidence must fail closed
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            return False
        finally:
            os.close(fd)

    def _reap(self, process: subprocess.Popen) -> int | None:
        """Bounded wait for a supervisor; SIGTERM then SIGKILL escalation.

        SIGTERM lets the supervisor's handler forward termination to its
        worker, so a stalled-but-responsive supervisor still takes the worker
        down with it. The lock probe afterwards decides run-dir retention.
        """
        try:
            return process.wait(timeout=self.reap_timeout_s)
        except subprocess.TimeoutExpired:
            pass
        try:
            process.terminate()
        except OSError:
            pass
        try:
            return process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def _new_run_id(provider: str) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        return f"run-{provider}-{stamp}-{uuid.uuid4().hex[:8]}"

    def _start_reaper(self, job: ScanJob) -> None:
        """Settle the job once its WORKER is gone, even without collect().

        The supervisor normally exits with the worker. If it dies first, the
        orphaned worker keeps the lock: the reaper keeps the job registered
        (and the lock therefore observable via active_job/lock_held) until
        the worker exits.
        """

        def reap() -> None:
            try:
                rc = job.process.wait()
            except Exception:
                rc = None
            job.exit_code = rc
            while job.worker_alive():
                time.sleep(0.2)
            job.worker_settled = True
            with self._jobs_guard:
                if self._jobs.get(job.provider) is job:
                    del self._jobs[job.provider]

        threading.Thread(target=reap, name=f"reaper-{job.provider}-{job.run_id}",
                         daemon=True).start()
