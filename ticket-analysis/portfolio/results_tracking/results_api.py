"""Backend HTTP API orchestration for the F9 results plan, section 8 piece 4.

Ties together cache.py (persistent responses + quota), jobs.py (bounded
resumable fetch jobs) and the real API-Football adapter into the exact
5-endpoint contract the plan specifies, plus a report-revision publication
scheme. This module owns no transport-layer concerns (loopback/Origin/body
protections stay in serve.py, routing/validation conventions stay in
api.py) -- it is the pure orchestration layer those two call into.

Two-job design: `jobs.py`'s own SUCCEEDED state means "every fetch task
completed", not "a manifest was published" -- the plan requires the latter
for a publicly-visible success. So each submission creates an OUTER job
(exposed via the API, identity keyed on run_id) whose state this module
manages by hand through RUNNING -> [delegate to an INNER fetch job, never
exposed, identity keyed on f"{run_id}:fetch"] -> RUNNING -> [evaluate +
publish] -> SUCCEEDED/FAILED/CANCELLED. The inner job_id is recorded in the
outer job's progress dict so cancellation (given only the outer job_id) can
reach both.

The population (audit_rows for evaluate_run) is read directly from
registry.committed_extract_snapshot() -- no on-disk audit-dir artifact is
required. evaluate_run binds each supplied ticket to its audited original
selections field-for-field, not just row_id membership, so
_build_audit_rows() reconstructs that exact shape; a malformed row (not a
dict) still gets an audit_rows entry (so the denominator never silently
shrinks) but is never supplied as a ticket, surfacing instead through
evaluate_run's own not_supplied_in_evidence reconciliation.

No client-supplied keys, paths, URLs, rule overrides or unbounded budgets:
callers choose only run_id/refresh/allow_partial: everything else (cache
root, quota, worker/retry/timeout budgets, the API key) comes from
ResultsApiConfig and the environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .__main__ import EvaluationInputError, evaluate_run
from .cache import CacheKey, FixtureCache, QuotaExceededError, QuotaTracker
from .jobs import FatalTaskError, JobBudget, JobError, JobState, JobStore, Task, run_result_job
from .sources.api_football import ApiFootballClient, ApiFootballError, build_fixture, filter_in_scope

PARSER_VERSION = "api_football_v1"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Matches exactly what publish_revision() generates
# (strftime("%Y%m%dT%H%M%S%f") + "-" + uuid4().hex[:8]); any other string,
# including one containing "/" or "..", is rejected before it ever reaches
# a filesystem join.
_REVISION_ID_RE = re.compile(r"^\d{8}T\d{12}-[0-9a-f]{8}$")
# A generic safe single path segment: no "/", ".", or anything else that
# could change directory-join semantics. run_id's exact business shape is
# RunRegistry's concern (and it is independently validated there, on the
# submit path, via committed_extract_snapshot); this module only needs to
# know it is safe to join into a path, since the GET read routes build a
# revisions-directory path straight from the URL without ever consulting
# the registry.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
MAX_LIST_LIMIT = 200


def _validate_revision_id(revision_id) -> str:
    # Malformed and nonexistent look identical to a caller: both mean "no
    # such revision", and raising the same error either way avoids leaking
    # which one it was.
    if not isinstance(revision_id, str) or not _REVISION_ID_RE.fullmatch(revision_id):
        raise ReportNotFoundError(revision_id)
    return revision_id


def _validate_run_id(run_id) -> str:
    if not isinstance(run_id, str) or not _SAFE_ID_RE.fullmatch(run_id):
        raise ReportNotFoundError(run_id)
    return run_id


class ResultsApiError(RuntimeError):
    """A structural problem with a run's population, config or a stored
    revision -- distinct from a normal unresolved/pending outcome."""


class ReportNotFoundError(KeyError):
    """No published, hash-verified revision exists for this run (yet)."""


def _write_atomic(path: Path, raw_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(raw_bytes)
    os.replace(tmp, path)


@dataclass(frozen=True)
class ResultsApiConfig:
    """Everything a submission needs besides run_id/refresh/allow_partial.
    Never populated from a client-supplied request body."""
    root: Path
    daily_quota: int = 95
    max_workers: int = 4
    max_retries: int = 2
    per_call_timeout: float = 15.0
    max_requests_per_job: int = 90

    def cache_root(self) -> Path:
        return self.root / "cache"

    def quota_root(self) -> Path:
        return self.root / "quota"

    def jobs_root(self) -> Path:
        return self.root / "jobs"

    def revisions_root(self, run_id: str) -> Path:
        return self.root / "revisions" / run_id


def compute_config_digest(config: ResultsApiConfig) -> str:
    payload = json.dumps({
        "daily_quota": config.daily_quota, "max_workers": config.max_workers,
        "max_retries": config.max_retries, "per_call_timeout": config.per_call_timeout,
        "max_requests_per_job": config.max_requests_per_job, "parser_version": PARSER_VERSION,
    }, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_population(registry, run_id: str):
    """-> (provider, input_digest, rows) where rows is row_id -> raw ticket
    dict, row_id = f"{digest}:{index}" (same scheme as inputs.audit_run)."""
    path, raw = registry.committed_extract_snapshot(run_id)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResultsApiError(f"run {run_id}: committed extract is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ResultsApiError(f"run {run_id}: committed extract is not a JSON object")
    qualifying = data.get("qualifying")
    if not isinstance(qualifying, list):
        raise ResultsApiError(f"run {run_id}: 'qualifying' is not a list")
    # Every retained row keeps its row_id, even a malformed one (not a
    # dict) -- dropping it here would silently shrink the denominator
    # below the frozen source row count. A non-dict entry is carried
    # through as-is; _build_audit_rows()/_ticket_to_evidence() below treat
    # it as a row with no usable ticket content, and evaluate_run's own
    # audit_rows reconciliation reports it as an explicit unresolved row
    # rather than one that simply never existed.
    rows = {f"{digest}:{index}": ticket for index, ticket in enumerate(qualifying)}
    return data.get("provider"), digest, rows


def _build_audit_rows(provider, rows: dict) -> dict:
    """evaluate_run's audit_rows argument: row_id -> {provider,
    original_selections, ...}, matching exactly the shape
    __main__._load_audited_population builds from an on-disk audit
    directory (so _validate_ticket_matches_audited_original's field-by-field
    comparison against original_selections holds), but built directly from
    the same rows this module already read -- no audit-dir artifact or
    second extract read required."""
    audit_rows = {}
    for row_id, ticket in rows.items():
        if not isinstance(ticket, dict):
            # provider=None skips the audited-provider check entirely
            # (_validate_ticket_matches_audited_original), but this row_id
            # is never supplied back in evidence["tickets"] anyway -- it
            # surfaces solely through evaluate_run's own
            # not_supplied_in_evidence reconciliation.
            audit_rows[row_id] = {"code": None, "num_legs": None, "num_selections": 0,
                                   "provider": None, "original_selections": []}
            continue
        raw_selections = ticket.get("selections")
        original_selections = [s for s in raw_selections if isinstance(s, dict)] \
            if isinstance(raw_selections, list) else []
        audit_rows[row_id] = {"code": ticket.get("code"), "num_legs": ticket.get("num_legs"),
                               "num_selections": len(original_selections), "provider": provider,
                               "original_selections": original_selections}
    return audit_rows


def _dates_needed(rows: dict) -> list[str]:
    dates = set()
    for ticket in rows.values():
        if not isinstance(ticket, dict):
            continue
        selections = ticket.get("selections")
        if not isinstance(selections, list):
            continue
        for selection in selections:
            if not isinstance(selection, dict):
                continue
            kickoff = selection.get("kickoff")
            if isinstance(kickoff, str) and _DATE_RE.match(kickoff):
                dates.add(kickoff[:10])
    return sorted(dates)


def _ticket_to_evidence(row_id: str, ticket: dict, provider: str | None) -> dict:
    legs = []
    selections = ticket.get("selections")
    for leg_index, selection in enumerate(selections if isinstance(selections, list) else []):
        if not isinstance(selection, dict):
            continue
        kickoff_raw = selection.get("kickoff")
        legs.append({
            "leg_id": f"{row_id}:{leg_index}",
            "provider": provider or "",
            "provider_event_id": None,
            "event_display": selection.get("event") or "",
            "league_display": selection.get("league") or "",
            "sport": selection.get("sport") or "",
            "market": selection.get("market") or "",
            "pick": selection.get("pick") or "",
            "specifier": "",
            # kickoff.raw must equal the original selection's kickoff value
            # exactly -- evaluate_run's audit binding
            # (_validate_ticket_matches_audited_original) compares this leg
            # against the same original selection field-for-field. A missing
            # kickoff becomes the literal string "unknown" only because
            # KickoffTime.raw must be nonempty; it is never treated as if it
            # were a real timestamp.
            "kickoff": {"raw": kickoff_raw if isinstance(kickoff_raw, str) and kickoff_raw else "unknown",
                        "precision": "date_only", "utc": None, "timezone_evidence": None},
        })
    return {
        "ticket_id": ticket.get("code") or row_id,
        "row_id": row_id,
        "provider": provider or "",
        "raw_bet_type": ticket.get("bet_type") or "",
        "legs": legs,
    }


def _fixture_to_evidence_dict(fixture) -> dict:
    kickoff, score = fixture.kickoff, fixture.regulation_score
    return {
        "provenance": {"source": fixture.provenance.source,
                       "source_fixture_id": fixture.provenance.source_fixture_id,
                       "fetched_at": fixture.provenance.fetched_at,
                       "content_sha256": fixture.provenance.content_sha256},
        "competition": fixture.competition, "season": fixture.season,
        "home_team": fixture.home_team, "away_team": fixture.away_team,
        "kickoff": {"raw": kickoff.raw, "precision": kickoff.precision,
                    "utc": kickoff.utc.isoformat() if kickoff.utc else None,
                    "timezone_evidence": kickoff.timezone_evidence},
        "state": fixture.state.value,
        "regulation_score": None if score is None else
            {"home": score.home, "away": score.away, "ht_home": score.ht_home, "ht_away": score.ht_away},
    }


def _cache_key_for_date(date: str) -> CacheKey:
    return CacheKey.build("api_football", "fixtures_by_date", {"date": date},
                           parser_version=PARSER_VERSION)


def _make_fetch_one(client, cache: FixtureCache, quota: QuotaTracker) -> Callable:
    def fetch_one(task: Task):
        date = task.payload
        key = _cache_key_for_date(date)
        entry = cache.get(key)
        if entry is not None and not entry.is_stale():
            return date
        try:
            quota.record_request()
        except QuotaExceededError as exc:
            raise FatalTaskError(str(exc)) from exc
        try:
            raw_fixtures = client.get_fixtures_by_date(date)
            fetched_at = datetime.now(timezone.utc).isoformat()
            fixtures = [build_fixture(item, fetched_at=fetched_at)
                        for item in filter_in_scope(raw_fixtures)]
        except ApiFootballError as exc:
            raise FatalTaskError(str(exc)) from exc
        all_terminal = all(f.is_finished for f in fixtures) if fixtures else True
        cache.put(key, json.dumps(raw_fixtures).encode("utf-8"), all_terminal=all_terminal)
        return date
    return fetch_one


def _fixtures_for_date(cache: FixtureCache, date: str) -> list:
    entry = cache.get(_cache_key_for_date(date))
    if entry is None:
        return []
    raw_fixtures = json.loads(entry.raw_bytes)
    return [build_fixture(item, fetched_at=entry.fetched_at.isoformat())
            for item in filter_in_scope(raw_fixtures)]


def publish_revision(config: ResultsApiConfig, run_id: str, evidence: dict, report: dict, *,
                      input_digest: str, config_digest: str, audit_rows: dict | None = None) -> str:
    """Writes a new revision directory, verifies its own hashes, then
    publishes manifest.json and the latest.json pointer atomically. Never
    touches a prior revision -- a failed publish leaves the last good report
    (if any) exactly as it was.

    When given, audit_rows is archived as audit_population.json alongside
    evidence.json/manifest.json, in exactly the layout __main__.py's
    --replay already expects from a prior `evaluate` command's output
    directory (F9_STEP4_REREVIEW.md S4R-04) -- so an HTTP-published revision
    directory is itself a valid --replay target, with row_id/decisions
    preserved after the run and its cache entries are gone (F9_STEP5_REVIEW.md
    S5-05), rather than replaying as an unanchored evidence-only evaluation
    whose row_ids come back null."""
    if not isinstance(run_id, str) or not _SAFE_ID_RE.fullmatch(run_id):
        raise ResultsApiError(f"invalid run id: {run_id!r}")
    revision_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8]
    rev_dir = config.revisions_root(run_id) / revision_id
    rev_dir.mkdir(parents=True, exist_ok=False)
    evidence_bytes = json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    report_bytes = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    (rev_dir / "evidence.json").write_bytes(evidence_bytes)
    (rev_dir / "report.json").write_bytes(report_bytes)
    manifest_files = {"evidence.json": hashlib.sha256(evidence_bytes).hexdigest(),
                       "report.json": hashlib.sha256(report_bytes).hexdigest()}
    if audit_rows is not None:
        audit_population_bytes = json.dumps(audit_rows, indent=2, sort_keys=True,
                                             ensure_ascii=False).encode("utf-8")
        (rev_dir / "audit_population.json").write_bytes(audit_population_bytes)
        manifest_files["audit_population.json"] = hashlib.sha256(audit_population_bytes).hexdigest()
    manifest = {
        "revision_id": revision_id, "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_digest": input_digest, "config_digest": config_digest,
        "row_count": report["counts"]["total"],
        "files": manifest_files,
    }
    for rel_path, expected in manifest["files"].items():
        actual = hashlib.sha256((rev_dir / rel_path).read_bytes()).hexdigest()
        if actual != expected:
            raise ResultsApiError(
                f"revision {revision_id} for run {run_id} failed its own hash check for {rel_path}")
    _write_atomic(rev_dir / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
    _write_atomic(config.revisions_root(run_id) / "latest.json",
                  json.dumps({"revision_id": revision_id}, indent=2).encode("utf-8"))
    return revision_id


def _verified_revision_report(config: ResultsApiConfig, run_id: str, revision_id: str) -> dict:
    _validate_run_id(run_id)
    _validate_revision_id(revision_id)
    rev_dir = config.revisions_root(run_id) / revision_id
    try:
        manifest = json.loads((rev_dir / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReportNotFoundError(f"{run_id}:{revision_id}")
    # The identifier alone cannot cross a run boundary (regex-validated
    # above), but belt-and-suspenders: the manifest's own recorded identity
    # must also match what was requested.
    if manifest.get("run_id") != run_id or manifest.get("revision_id") != revision_id:
        raise ReportNotFoundError(f"{run_id}:{revision_id}")
    for rel_path, expected in manifest["files"].items():
        actual = hashlib.sha256((rev_dir / rel_path).read_bytes()).hexdigest()
        if actual != expected:
            raise ResultsApiError(
                f"revision {revision_id} for run {run_id}: {rel_path} failed hash verification")
    report = json.loads((rev_dir / "report.json").read_text(encoding="utf-8"))
    return manifest, report


def get_latest_report(config: ResultsApiConfig, run_id: str) -> dict:
    _validate_run_id(run_id)
    pointer_path = config.revisions_root(run_id) / "latest.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReportNotFoundError(run_id)
    manifest, report = _verified_revision_report(config, run_id, pointer["revision_id"])
    return {"revision_id": manifest["revision_id"], "run_id": run_id, "created_at": manifest["created_at"],
            "counts": report["counts"], "percentages": report["percentages"],
            "rule_versions": report["rule_versions"]}


def list_codes(config: ResultsApiConfig, run_id: str, revision_id: str, *,
                offset: int = 0, limit: int = 50) -> dict:
    if offset < 0:
        raise ResultsApiError("offset must be >= 0")
    if limit <= 0:
        raise ResultsApiError("limit must be > 0")
    limit = min(limit, MAX_LIST_LIMIT)
    _, report = _verified_revision_report(config, run_id, revision_id)
    tickets = report["tickets"]
    return {"revision_id": revision_id, "total": len(tickets), "offset": offset, "limit": limit,
            "rows": tickets[offset:offset + limit]}


def _default_client_factory() -> ApiFootballClient:
    return ApiFootballClient()


def submit_evaluation(registry, config: ResultsApiConfig, job_store: JobStore, run_id: str, *,
                       refresh: bool = False, allow_partial: bool = False,
                       client_factory: Callable[[], object] = _default_client_factory):
    """Starts (or returns the existing active job for) one evaluation of
    run_id. Never blocks on fetching/evaluating/publishing -- the real work
    runs on a background thread; this returns as soon as the job record
    exists. `refresh` is accepted for the API contract but delegates to
    cache.py's own staleness policy rather than forcing a refetch: every
    fetch_one call already re-checks staleness itself."""
    provider, input_digest, rows = load_population(registry, run_id)
    config_digest = compute_config_digest(config)
    outer = job_store.create_or_reuse(run_id, input_digest, config_digest)

    # Claiming the STARTING -> RUNNING transition here (under update()'s
    # flock) -- rather than inside the background thread -- is what makes
    # "one active evaluation per identity" hold even when two submissions
    # race immediately after create_or_reuse returns the same non-terminal
    # job to both: only the caller whose mutate actually observes STARTING
    # flips it, so exactly one of them spawns a thread for this job_id.
    claimed = {"did": False}

    def _claim(record: dict) -> None:
        if record.get("state") == JobState.STARTING:
            record.update(state=JobState.RUNNING)
            claimed["did"] = True

    job_store.update(outer.job_id, _claim)
    if claimed["did"]:
        thread = threading.Thread(
            target=_run_evaluation_job,
            args=(config, job_store, outer.job_id, run_id, provider, input_digest, config_digest,
                  rows, allow_partial, client_factory),
            daemon=True)
        thread.start()
    return job_store.get(outer.job_id)


def _run_evaluation_job(config: ResultsApiConfig, job_store: JobStore, outer_job_id: str, run_id: str,
                         provider, input_digest: str, config_digest: str, rows: dict,
                         allow_partial: bool, client_factory: Callable[[], object]) -> None:
    try:
        cache = FixtureCache(config.cache_root())
        quota = QuotaTracker(config.quota_root(), "api_football", config.daily_quota)
        dates = _dates_needed(rows)
        incomplete: tuple = ()

        if dates:
            inner = job_store.create_or_reuse(f"{run_id}:fetch", input_digest, config_digest)
            job_store.update(outer_job_id, lambda r: r["progress"].update(fetch_job_id=inner.job_id))
            if job_store.get(outer_job_id).cancel_requested:
                job_store.request_cancel(inner.job_id)
            try:
                client = client_factory()
            except ApiFootballError as exc:
                job_store.update(outer_job_id, lambda r: r.update(
                    state=JobState.FAILED, error={"code": "source_unavailable", "message": str(exc)}))
                return
            fetch_one = _make_fetch_one(client, cache, quota)
            budget = JobBudget(max_requests=config.max_requests_per_job, max_retries=config.max_retries,
                                per_call_timeout=config.per_call_timeout, max_workers=config.max_workers)
            tasks = [Task(key=date, payload=date) for date in dates]
            try:
                outcome = run_result_job(job_store, inner.job_id, tasks, fetch_one=fetch_one, budget=budget)
            except FatalTaskError as exc:
                job_store.update(outer_job_id, lambda r: r.update(
                    state=JobState.FAILED, error={"code": "fatal_fetch_error", "message": str(exc)}))
                return
            except JobError as exc:
                job_store.update(outer_job_id, lambda r: r.update(
                    state=JobState.FAILED, error={"code": "fetch_job_error", "message": str(exc)}))
                return
            if outcome.cancelled:
                job_store.update(outer_job_id, lambda r: r.update(state=JobState.CANCELLED))
                return
            incomplete = outcome.incomplete_task_keys
            if incomplete and not allow_partial:
                job_store.update(outer_job_id, lambda r: r.update(
                    state=JobState.FAILED, error={
                        "code": "incomplete_fetch",
                        "message": f"{len(incomplete)} date(s) not fetched and allow_partial is false"}))
                return

        if job_store.get(outer_job_id).cancel_requested:
            job_store.update(outer_job_id, lambda r: r.update(state=JobState.CANCELLED))
            return

        fixtures = []
        for date in dates:
            if date not in incomplete:
                fixtures.extend(_fixtures_for_date(cache, date))

        evidence = {
            "fixtures": [_fixture_to_evidence_dict(f) for f in fixtures],
            "aliases": [], "verified_mappings": [],
            # A malformed row (not a dict) is never supplied here -- it has
            # no usable ticket content to convert -- but it is still a key
            # in audit_rows above, so evaluate_run reports it as an
            # explicit unresolved row instead of a silently shrunk N.
            "tickets": [_ticket_to_evidence(row_id, ticket, provider) for row_id, ticket in rows.items()
                        if isinstance(ticket, dict)],
        }
        audit_rows = _build_audit_rows(provider, rows)
        try:
            report = evaluate_run(evidence, audit_rows=audit_rows)
        except EvaluationInputError as exc:
            job_store.update(outer_job_id, lambda r: r.update(
                state=JobState.FAILED, error={"code": "evaluation_failed", "message": str(exc)}))
            return

        try:
            revision_id = publish_revision(config, run_id, evidence, report, audit_rows=audit_rows,
                                            input_digest=input_digest, config_digest=config_digest)
        except (OSError, ResultsApiError) as exc:
            job_store.update(outer_job_id, lambda r: r.update(
                state=JobState.FAILED, error={"code": "publish_failed", "message": str(exc)}))
            return

        job_store.update(outer_job_id, lambda r: r.update(
            state=JobState.SUCCEEDED,
            result={"revision_id": revision_id, "counts": report["counts"],
                    "incomplete_dates": list(incomplete)}))
    except Exception as exc:  # the background thread must never die silently
        job_store.update(outer_job_id, lambda r: r.update(
            state=JobState.FAILED, error={"code": "internal_error", "message": str(exc)}))


def cancel_job(job_store: JobStore, job_id: str):
    """Idempotent: requests cancellation of job_id and, if it is an outer
    evaluation job that has already recorded an inner fetch job, propagates
    the request there too."""
    record = job_store.request_cancel(job_id)
    fetch_job_id = record.progress.get("fetch_job_id")
    if fetch_job_id:
        job_store.request_cancel(fetch_job_id)
    return record
