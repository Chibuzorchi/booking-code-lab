"""Offline worker-management tests: real subprocess boundaries, no provider
traffic. Fixture providers mirror the real scan_code CLI contract and the
load_dotenv(override=False) environment loader, so isolation and single-flight
behavior are exercised across actual interpreter processes.

Live acceptance (a real bet9ja-then-sportybet extract showing base36 codes and
sportybet's own band) remains under the whole-slice gate and is NOT covered
here: these tests never contact provider sites and never book fresh seeds.
"""
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from portfolio.worker import (
    SUPERVISOR_PAUSE_ENV,
    JobBusyError,
    LaunchError,
    UnknownProviderError,
    WorkerHost,
    default_workspace,
    isolated_env,
    scan_argv,
    supervisor_command,
)

TICKET_ANALYSIS = Path(__file__).resolve().parents[1]

FIXTURE_SCAN = '''\
"""Offline fixture mirroring the real scan_code CLI contract and env loader."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


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
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "results"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "started.marker").write_text(str(os.getpid()))
    time.sleep(float(os.getenv("FIXTURE_SLEEP_SECONDS", "0")))
    exit_code = int(os.getenv("FIXTURE_EXIT_CODE", "0"))
    if exit_code:
        print("fixture failing with", exit_code)
        return exit_code
    (run_dir / "extracts").mkdir(parents=True, exist_ok=True)
    (run_dir / "codes").mkdir(parents=True, exist_ok=True)
    extract = {
        "provider": os.getenv("PROVIDER", ""),
        "charset": os.getenv("CHARSET") or "BASE62",
        "min_total_odds": os.getenv("MIN_TOTAL_ODDS", ""),
        "max_total_odds": os.getenv("MAX_TOTAL_ODDS", ""),
        "browser_channel": os.getenv("BROWSER_CHANNEL", ""),
        "sports_url": os.getenv("SPORTS_URL", ""),
        "coupon_api_base": os.getenv("COUPON_API_BASE", ""),
        "site_url": os.getenv("SITE_URL", ""),
        "share_api_base": os.getenv("SHARE_API_BASE", ""),
        "shared_override": os.getenv("SHARED_OVERRIDE", ""),
        "locale": os.getenv("LOCALE", ""),
        "timezone_id": os.getenv("TIMEZONE_ID", ""),
        "request_timeout_s": os.getenv("REQUEST_TIMEOUT_S", ""),
        "pythonpath": os.getenv("PYTHONPATH"),
        "cwd": os.getcwd(),
        "seed": args.code,
        "max_qualifying": args.max_qualifying,
        "run_dir": args.run_dir,
    }
    (run_dir / "extracts" / "scan_fixture.json").write_text(json.dumps(extract, indent=2))
    (run_dir / "codes" / "codes.txt").write_text(extract["charset"] + "\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

BET9JA_ENV = """\
PROVIDER=bet9ja
MIN_TOTAL_ODDS=5000
MAX_TOTAL_ODDS=350000
MAX_CODES_TO_TRY=25000       # API-call cap (comment must not leak into the key)
BROWSER_CHANNEL=chrome
SPORTS_URL=https://sports.bet9ja.com/
COUPON_API_BASE=https://coupon.bet9ja.com/desktop/feapi/CouponAjax
SHARED_OVERRIDE=bet9ja-own
"""

SPORTYBET_ENV = """\
PROVIDER=sportybet
MIN_TOTAL_ODDS=5000
MAX_TOTAL_ODDS=350000
CHARSET=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ
SITE_URL=https://www.sportybet.com/ng/
SHARE_API_BASE=https://www.sportybet.com/api/ng/orders/share
SHARED_OVERRIDE=sportybet-own
"""

CHILD_START = (
    "import os, sys\n"
    "sys.path.insert(0, os.environ['TICKET_ANALYSIS'])\n"
    "from portfolio.worker import WorkerHost, JobBusyError\n"
    "host = WorkerHost(os.environ['WORKSPACE'])\n"
    "try:\n"
    "    job = host.start_scan(os.environ['PROVIDER'], 'SEED')\n"
    "except JobBusyError:\n"
    "    print('BUSY')\n"
    "else:\n"
    "    print('STARTED', host.collect(job))\n"
)

CHILD_SIMULTANEOUS = (
    "import os, sys\n"
    "sys.path.insert(0, os.environ['TICKET_ANALYSIS'])\n"
    "from portfolio.worker import WorkerHost, JobBusyError\n"
    "host = WorkerHost(os.environ['WORKSPACE'])\n"
    "try:\n"
    "    job = host.start_scan('bet9ja', 'SEED',\n"
    "                          env_extra={'FIXTURE_SLEEP_SECONDS': '3'})\n"
    "except JobBusyError:\n"
    "    print('BUSY')\n"
    "else:\n"
    "    print('STARTED', host.collect(job))\n"
)

CHILD_RELATIVE = (
    "import os, sys\n"
    "sys.path.insert(0, os.environ['TICKET_ANALYSIS'])\n"
    "from portfolio.worker import WorkerHost\n"
    "os.chdir(os.environ['SANDBOX'])\n"
    "host = WorkerHost(os.environ['WORKSPACE'])\n"
    "job = host.start_scan('bet9ja', 'SEED', run_dir='relative/run')\n"
    "rc = host.collect(job)\n"
    "print('RC', rc)\n"
    "print('RUN_DIR', job.run_dir)\n"
    "print('EXTRACT', (job.run_dir / 'extracts' / 'scan_fixture.json').is_file())\n"
)

CHILD_HOLDER = (
    "import os, sys, time\n"
    "sys.path.insert(0, os.environ['TICKET_ANALYSIS'])\n"
    "from portfolio.worker import WorkerHost\n"
    "host = WorkerHost(os.environ['WORKSPACE'])\n"
    "job = host.start_scan('bet9ja', 'SEED', env_extra={'FIXTURE_SLEEP_SECONDS': '60'})\n"
    "print('HELD', job.worker_pid, flush=True)\n"
    "time.sleep(120)\n"
)


class WorkerTestBase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Path(self._temp.name).resolve() / "workspace"
        self.workspace.mkdir()
        for provider, dotenv in (("bet9ja", BET9JA_ENV), ("sportybet", SPORTYBET_ENV)):
            scripts = self.workspace / provider / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "scan_code.py").write_text(FIXTURE_SCAN)
            (self.workspace / provider / ".env").write_text(dotenv)
        self._env_backup = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env_backup)))

    def host(self, **kwargs):
        return WorkerHost(self.workspace, **kwargs)

    def lock_held(self, provider):
        lock = self.workspace / "results" / "runs" / provider / "job.lock"
        try:
            fd = os.open(lock, os.O_RDWR)
        except OSError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            return False
        finally:
            os.close(fd)

    def extract(self, job):
        return json.loads((job.run_dir / "extracts" / "scan_fixture.json").read_text())


class IsolationTests(WorkerTestBase):
    def test_back_to_back_providers_isolated_environment(self):
        host = self.host()
        first = host.start_scan("bet9ja", "SEED-A")
        self.assertEqual(host.collect(first), 0)
        data = self.extract(first)
        self.assertEqual(data["provider"], "bet9ja")
        self.assertEqual(data["charset"], "BASE62")
        self.assertEqual(data["browser_channel"], "chrome")
        self.assertEqual(data["min_total_odds"], "5000")
        self.assertEqual(data["max_total_odds"], "350000")
        self.assertEqual(data["sports_url"], "https://sports.bet9ja.com/")
        self.assertEqual(data["coupon_api_base"],
                         "https://coupon.bet9ja.com/desktop/feapi/CouponAjax")
        self.assertEqual(data["site_url"], "")
        self.assertEqual(data["share_api_base"], "")
        self.assertEqual(data["shared_override"], "bet9ja-own")
        self.assertIsNone(data["pythonpath"])

        # Simulate bet9ja having leaked into the runner process environment,
        # including settings absent from both dotenv files.
        os.environ.update({
            "CHARSET": "TAINTED", "BROWSER_CHANNEL": "chromium-tainted",
            "SPORTS_URL": "https://tainted/", "COUPON_API_BASE": "https://tainted/",
            "SITE_URL": "https://tainted/", "SHARE_API_BASE": "https://tainted/",
            "MIN_TOTAL_ODDS": "999", "MAX_TOTAL_ODDS": "999999",
            "SHARED_OVERRIDE": "parent-taint", "PROVIDER": "bet9ja",
            "PYTHONPATH": "/tainted", "LOCALE": "de-DE",
            "TIMEZONE_ID": "Europe/Berlin", "REQUEST_TIMEOUT_S": "1",
        })
        second = host.start_scan("sportybet", "SEED-B")
        self.assertEqual(host.collect(second), 0)
        data = self.extract(second)
        self.assertEqual(data["provider"], "sportybet")
        self.assertEqual(data["charset"], "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        self.assertEqual(data["browser_channel"], "")
        self.assertEqual(data["sports_url"], "")
        self.assertEqual(data["coupon_api_base"], "")
        self.assertEqual(data["site_url"], "https://www.sportybet.com/ng/")
        self.assertEqual(data["share_api_base"],
                         "https://www.sportybet.com/api/ng/orders/share")
        self.assertEqual(data["min_total_odds"], "5000")
        self.assertEqual(data["max_total_odds"], "350000")
        self.assertEqual(data["shared_override"], "sportybet-own")
        self.assertEqual(data["locale"], "")
        self.assertEqual(data["timezone_id"], "")
        self.assertEqual(data["request_timeout_s"], "")
        self.assertIsNone(data["pythonpath"])

        # Reverse direction: sportybet's CHARSET must not leak into bet9ja's
        # code default (bet9ja's .env defines no CHARSET).
        os.environ.update({"CHARSET": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                           "BROWSER_CHANNEL": "", "PROVIDER": "sportybet"})
        third = host.start_scan("bet9ja", "SEED-C")
        self.assertEqual(host.collect(third), 0)
        data = self.extract(third)
        self.assertEqual(data["provider"], "bet9ja")
        self.assertEqual(data["charset"], "BASE62")
        self.assertEqual(data["browser_channel"], "chrome")
        self.assertEqual(data["shared_override"], "bet9ja-own")

    def test_concurrent_start_single_flight_in_process(self):
        host = self.host()
        first = host.start_scan("bet9ja", "SEED",
                                env_extra={"FIXTURE_SLEEP_SECONDS": "2"})
        with self.assertRaises(JobBusyError):
            host.start_scan("bet9ja", "SEED")
        other = WorkerHost(self.workspace)
        with self.assertRaises(JobBusyError):
            other.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(first), 0)
        followup = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(followup), 0)
        self.assertIsNone(host.active_job("bet9ja"))

    def test_concurrent_start_single_flight_across_processes(self):
        host = self.host()
        first = host.start_scan("bet9ja", "SEED",
                                env_extra={"FIXTURE_SLEEP_SECONDS": "3"})
        child_env = dict(os.environ)
        child_env.update(TICKET_ANALYSIS=str(TICKET_ANALYSIS),
                         WORKSPACE=str(self.workspace), PROVIDER="bet9ja")

        def child_start():
            return subprocess.run([sys.executable, "-c", CHILD_START],
                                  cwd=TICKET_ANALYSIS, env=child_env,
                                  capture_output=True, text=True, timeout=60)

        busy = child_start()
        self.assertEqual(busy.returncode, 0, busy.stderr)
        self.assertIn("BUSY", busy.stdout)
        self.assertEqual(host.collect(first), 0)
        started = child_start()
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn("STARTED 0", started.stdout)

    def test_simultaneous_start_in_process_exactly_one_wins(self):
        host = self.host()
        barrier = threading.Barrier(2)
        outcomes = []

        def attempt():
            barrier.wait()
            try:
                job = host.start_scan("bet9ja", "SEED",
                                      env_extra={"FIXTURE_SLEEP_SECONDS": "2"})
                outcomes.append("won")
                host.collect(job)
            except JobBusyError:
                outcomes.append("busy")

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(outcomes.count("won"), 1)
        self.assertEqual(outcomes.count("busy"), 1)
        self.assertIsNone(host.active_job("bet9ja"))

    def test_simultaneous_start_across_processes(self):
        child_env = dict(os.environ)
        child_env.update(TICKET_ANALYSIS=str(TICKET_ANALYSIS),
                         WORKSPACE=str(self.workspace))
        contenders = [subprocess.Popen([sys.executable, "-c", CHILD_SIMULTANEOUS],
                                       cwd=TICKET_ANALYSIS, env=child_env,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
                      for _ in range(2)]
        outputs = []
        for contender in contenders:
            out, _ = contender.communicate(timeout=60)
            self.assertEqual(contender.returncode, 0, out)
            outputs.append(out)
        self.assertEqual(sum("STARTED" in out for out in outputs), 1)
        self.assertEqual(sum("BUSY" in out for out in outputs), 1)

    def test_holder_process_death_keeps_lock_until_worker_exits(self):
        child_env = dict(os.environ)
        child_env.update(TICKET_ANALYSIS=str(TICKET_ANALYSIS),
                         WORKSPACE=str(self.workspace))
        holder = subprocess.Popen([sys.executable, "-c", CHILD_HOLDER],
                                  cwd=TICKET_ANALYSIS, env=child_env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, bufsize=1)
        line = holder.stdout.readline()
        self.assertIn("HELD", line)
        worker_pid = int(line.split()[1])
        holder.kill()  # simulate host crash: no cleanup runs
        holder.wait(timeout=30)
        try:
            os.kill(worker_pid, 0)
            worker_alive = True
        except ProcessLookupError:
            worker_alive = False
        self.assertTrue(worker_alive, "orphaned worker must survive its dead host")
        with self.assertRaises(JobBusyError):
            self.host().start_scan("bet9ja", "SEED")
        os.kill(worker_pid, signal.SIGTERM)
        host = self.host()
        deadline = time.monotonic() + 15
        job = None
        while job is None:
            try:
                job = host.start_scan("bet9ja", "SEED")
            except JobBusyError:
                self.assertLess(time.monotonic(), deadline,
                                "lock stayed held after the worker exited")
                time.sleep(0.1)
        self.assertEqual(host.collect(job), 0)
        self.assertIsNone(host.active_job("bet9ja"))

    def test_supervisor_death_before_sidecar_fails_closed(self):
        runs = self.workspace / "results" / "runs" / "bet9ja"
        run_dir = self.workspace / "sup-death-run"
        run_dir.mkdir()
        lock_file = runs / "job.lock"
        owner_file = runs / "job.owner.json"
        # The pause is only an upper bound: we kill the supervisor as soon as
        # the worker signals readiness, so this is timing-safe on slow hosts.
        env = isolated_env(self.workspace, {
            "FIXTURE_SLEEP_SECONDS": "2", SUPERVISOR_PAUSE_ENV: "120"})
        pipe_r, pipe_w = os.pipe()
        worker_argv = scan_argv(sys.executable, "SEED", run_dir, max_qualifying=0)
        argv = supervisor_command(sys.executable, lock_file, pipe_w, owner_file,
                                  run_dir / "worker.log",
                                  {"provider": "bet9ja", "run_id": "r",
                                   "started_at": "now"}, worker_argv)
        supervisor = subprocess.Popen(argv, cwd=self.workspace / "bet9ja", env=env,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, pass_fds=(pipe_w,))
        os.close(pipe_w)
        marker = run_dir / "started.marker"
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.exists(),
                        "worker must signal readiness before the supervisor is killed")
        supervisor.kill()  # dies after spawning the worker, before the sidecar
        supervisor.wait(timeout=30)
        os.close(pipe_r)
        self.assertFalse(owner_file.exists())
        self.assertTrue(self.lock_held("bet9ja"),
                        "surviving worker must keep the lock after its supervisor dies")
        with self.assertRaises(JobBusyError):
            self.host().start_scan("bet9ja", "SEED")
        deadline = time.monotonic() + 15
        while self.lock_held("bet9ja"):
            self.assertLess(time.monotonic(), deadline,
                            "lock must release when the surviving worker exits")
            time.sleep(0.1)
        host = self.host()
        job = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(job), 0)

    def test_host_death_before_ack_keeps_ownership(self):
        runs = self.workspace / "results" / "runs" / "bet9ja"
        run_dir = self.workspace / "ack-run"
        run_dir.mkdir()
        lock_file = runs / "job.lock"
        owner_file = runs / "job.owner.json"
        env = isolated_env(self.workspace, {"FIXTURE_SLEEP_SECONDS": "2"})
        pipe_r, pipe_w = os.pipe()
        os.close(pipe_r)  # the host dies before the ack can be delivered
        worker_argv = scan_argv(sys.executable, "SEED", run_dir, max_qualifying=0)
        argv = supervisor_command(sys.executable, lock_file, pipe_w, owner_file,
                                  run_dir / "worker.log",
                                  {"provider": "bet9ja", "run_id": "r",
                                   "started_at": "now"}, worker_argv)
        supervisor = subprocess.Popen(argv, cwd=self.workspace / "bet9ja", env=env,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, pass_fds=(pipe_w,))
        os.close(pipe_w)
        deadline = time.monotonic() + 10
        while not owner_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(owner_file.exists(),
                        "sidecar must be published even though the host is gone")
        worker_pid = json.loads(owner_file.read_text())["worker_pid"]
        try:
            os.kill(worker_pid, 0)
            worker_alive = True
        except ProcessLookupError:
            worker_alive = False
        self.assertTrue(worker_alive, "worker must survive the report-pipe closure")
        self.assertTrue(self.lock_held("bet9ja"),
                        "lock must stay held while the worker lives")
        with self.assertRaises(JobBusyError):
            self.host().start_scan("bet9ja", "SEED")
        self.assertEqual(supervisor.wait(timeout=30), 0,
                         "supervisor keeps waiting on the worker after the disconnect")
        deadline = time.monotonic() + 10
        while self.lock_held("bet9ja") and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.lock_held("bet9ja"))
        self.assertFalse(owner_file.exists(), "sidecar cleaned up after worker exit")
        self.assertTrue((run_dir / "extracts" / "scan_fixture.json").is_file())

    def test_contract_distinguishes_supervisor_exit_from_worker_completion(self):
        host = self.host()
        job = host.start_scan("bet9ja", "SEED",
                              env_extra={"FIXTURE_SLEEP_SECONDS": "60"})
        job.process.kill()  # supervisor dies out-of-band; the worker survives
        job.process.wait(timeout=30)
        self.assertIsNotNone(job.process.poll(), "supervisor exited")
        self.assertIsNotNone(job.exit_code)
        self.assertTrue(job.worker_alive(), "orphan worker still alive")
        self.assertTrue(job.is_running(),
                        "job with a live orphan worker must count as running")
        self.assertFalse(job.worker_settled)
        self.assertTrue(host.lock_held("bet9ja"))
        self.assertIs(host.active_job("bet9ja"), job,
                      "job stays registered while its worker holds the lock")
        with self.assertRaises(JobBusyError):
            host.start_scan("bet9ja", "SEED")
        os.kill(job.worker_pid, signal.SIGTERM)  # test owns this live-reported pid
        deadline = time.monotonic() + 15
        while not job.worker_settled and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(job.worker_settled)
        self.assertFalse(job.is_running())
        self.assertFalse(job.worker_alive())
        self.assertIsNone(host.active_job("bet9ja"))
        self.assertFalse(host.lock_held("bet9ja"))
        followup = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(followup), 0)

    def test_different_providers_run_independently(self):
        host = self.host()
        first = host.start_scan("bet9ja", "SEED-A",
                                env_extra={"FIXTURE_SLEEP_SECONDS": "1"})
        second = host.start_scan("sportybet", "SEED-B")
        self.assertEqual(host.collect(second), 0)
        self.assertEqual(host.collect(first), 0)
        self.assertEqual(self.extract(first)["provider"], "bet9ja")
        self.assertEqual(self.extract(second)["provider"], "sportybet")

    def test_launch_failure_releases_ownership(self):
        bad = WorkerHost(self.workspace, python="/nonexistent/python-xyz")
        with self.assertRaises(LaunchError):
            bad.start_scan("bet9ja", "SEED")
        runs = self.workspace / "results" / "runs" / "bet9ja"
        self.assertFalse(list(runs.glob("run-*")))
        self.assertFalse((runs / "job.owner.json").exists())
        host = self.host()
        job = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(job), 0)

    def test_collision_exhaustion_never_deletes_existing_run(self):
        host = self.host()
        runs = self.workspace / "results" / "runs" / "bet9ja"
        marker_dir = runs / "run-existing"
        marker_dir.mkdir(parents=True)
        (marker_dir / "marker.txt").write_text("keep me")
        original = host._new_run_id
        host._new_run_id = lambda provider: "run-existing"
        try:
            with self.assertRaises(LaunchError):
                host.start_scan("bet9ja", "SEED")
        finally:
            host._new_run_id = original
        self.assertTrue((marker_dir / "marker.txt").is_file())
        self.assertEqual((marker_dir / "marker.txt").read_text(), "keep me")
        job = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(job), 0)

    def test_sidecar_write_failure_releases_lock(self):
        runs = self.workspace / "results" / "runs" / "bet9ja"
        obstruction = runs / "job.owner.json"
        obstruction.mkdir(parents=True)
        host = self.host()
        with self.assertRaises(LaunchError):
            host.start_scan("bet9ja", "SEED")
        self.assertFalse(list(runs.glob("run-*")))
        shutil.rmtree(obstruction)
        job = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(job), 0)

    def test_worker_exit_nonzero_releases_ownership(self):
        host = self.host()
        failing = host.start_scan("bet9ja", "SEED",
                                  env_extra={"FIXTURE_EXIT_CODE": "3"})
        self.assertEqual(host.collect(failing), 3)
        self.assertIsNone(host.active_job("bet9ja"))
        self.assertFalse(failing.is_running())
        job = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(job), 0)

    def test_reaper_releases_without_collect(self):
        host = self.host()
        job = host.start_scan("bet9ja", "SEED",
                              env_extra={"FIXTURE_SLEEP_SECONDS": "0.3"})
        deadline = time.monotonic() + 15
        while host.active_job("bet9ja") is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNone(host.active_job("bet9ja"))
        self.assertEqual(job.exit_code, 0)
        followup = host.start_scan("bet9ja", "SEED")
        self.assertEqual(host.collect(followup), 0)

    def test_separate_output_paths_and_provider_attribution(self):
        host = self.host()
        first = host.start_scan("bet9ja", "SEED-A")
        second = host.start_scan("sportybet", "SEED-B")
        self.assertEqual(host.collect(first), 0)
        self.assertEqual(host.collect(second), 0)
        runs = self.workspace / "results" / "runs"
        self.assertEqual(first.run_dir, runs / "bet9ja" / first.run_id)
        self.assertEqual(second.run_dir, runs / "sportybet" / second.run_id)
        self.assertNotEqual(first.run_dir, second.run_dir)
        for job, provider, charset in ((first, "bet9ja", "BASE62"),
                                       (second, "sportybet",
                                        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")):
            data = self.extract(job)
            self.assertEqual(data["provider"], provider)
            self.assertEqual(data["charset"], charset)
            self.assertTrue((job.run_dir / "worker.log").is_file())
            self.assertEqual((job.run_dir / "codes" / "codes.txt")
                             .read_text(), charset + "\n")
        override_dir = self.workspace / "results" / "custom-place"
        custom = host.start_scan("bet9ja", "SEED-C", run_dir=override_dir)
        self.assertEqual(host.collect(custom), 0)
        self.assertEqual(custom.run_dir, override_dir)
        self.assertTrue((override_dir / "extracts" / "scan_fixture.json").is_file())

    def test_relative_run_dir_resolved_before_launch(self):
        sandbox = self.workspace / "sandbox"
        sandbox.mkdir()
        child_env = dict(os.environ)
        child_env.update(TICKET_ANALYSIS=str(TICKET_ANALYSIS),
                         WORKSPACE=str(self.workspace), SANDBOX=str(sandbox))
        result = subprocess.run([sys.executable, "-c", CHILD_RELATIVE],
                                cwd=TICKET_ANALYSIS, env=child_env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RC 0", result.stdout)
        expected = str((sandbox / "relative" / "run").resolve())
        self.assertIn(f"RUN_DIR {expected}", result.stdout)
        self.assertIn("EXTRACT True", result.stdout)
        self.assertTrue((sandbox / "relative" / "run" / "extracts"
                         / "scan_fixture.json").is_file())
        self.assertFalse((self.workspace / "bet9ja" / "relative").exists(),
                         "extract must not land relative to the provider cwd")

    def test_argument_list_not_shell_interpolation(self):
        host = self.host()
        seed = 'AB C;$(touch pwned)"x\'y`echo z`'
        job = host.start_scan("bet9ja", seed)
        self.assertIsInstance(job.argv, list)
        self.assertEqual(job.argv[0], sys.executable)
        self.assertEqual(job.argv[3], seed)
        self.assertEqual(host.collect(job), 0)
        self.assertEqual(self.extract(job)["seed"], seed)

    def test_isolated_env_strips_provider_keys_and_keeps_extras(self):
        os.environ.update({"CHARSET": "TAINTED", "BROWSER_CHANNEL": "chrome",
                           "MAX_CODES_TO_TRY": "1", "SHARED_OVERRIDE": "x",
                           "PYTHONPATH": "/tainted", "BET_ENV": "prod",
                           "LOCALE": "de-DE", "TIMEZONE_ID": "Europe/Berlin",
                           "REQUEST_TIMEOUT_S": "1", "USER_AGENT": "spoof"})
        env = isolated_env(self.workspace, extra={"BET_ENV": "dev", "KEEP": "yes"})
        for key in ("CHARSET", "BROWSER_CHANNEL", "MAX_CODES_TO_TRY",
                    "SHARED_OVERRIDE", "PYTHONPATH", "PROVIDER", "SEED_CODE",
                    "LOCALE", "TIMEZONE_ID", "REQUEST_TIMEOUT_S", "USER_AGENT"):
            self.assertNotIn(key, env)
        self.assertEqual(env["BET_ENV"], "dev")
        self.assertEqual(env["KEEP"], "yes")
        self.assertIn("PATH", env)

    def test_unknown_provider_and_bad_seed_rejected(self):
        host = self.host()
        with self.assertRaises(UnknownProviderError):
            host.provider_root("nope")
        with self.assertRaises(UnknownProviderError):
            host.start_scan("nope", "SEED")
        with self.assertRaises(ValueError):
            host.start_scan("bet9ja", "   ")


class RealProviderEntrypointTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env_backup)))

    def test_real_providers_launch_help_offline_without_imports(self):
        workspace = default_workspace()
        host = WorkerHost(workspace)
        host.provider_root("bet9ja")
        host.provider_root("sportybet")
        env = isolated_env(workspace)
        for provider in ("bet9ja", "sportybet"):
            with self.subTest(provider=provider):
                entrypoint = workspace / provider / "scripts" / "scan_code.py"
                if not entrypoint.is_file():
                    self.skipTest(f"real provider tree missing: {entrypoint}")
                result = subprocess.run(
                    [sys.executable, "-m", "scripts.scan_code", "--help"],
                    cwd=workspace / provider, env=env,
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--max-qualifying", result.stdout)
                self.assertIn("--run-dir", result.stdout)
        self.assertNotIn("engine", sys.modules)
        self.assertNotIn("infra", sys.modules)


if __name__ == "__main__":
    unittest.main()
