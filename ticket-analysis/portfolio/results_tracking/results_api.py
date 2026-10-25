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
import fcntl
import time
import base64
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
from .local_sources import freeze_local_sources
from .cache import CacheKey, FixtureCache, QuotaExceededError, QuotaTracker
from .jobs import FatalTaskError, JobBudget, JobError, JobRecord, JobState, JobStore, Task, WorkerOwnership, run_result_job
from .sources.api_football import (
    ApiFootballClient, ApiFootballError, ApiFootballTransportError, ApiFootballAccessError, build_fixture, filter_in_scope,
)

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
    Never populated from a client-supplied request body.

    identity_config_path, when set, names a reviewed, server-controlled JSON
    file shaped like evidence's own {"aliases": [...], "verified_mappings":
    [...]}: the only source of alias/verified-mapping identity evidence the
    HTTP path ever supplies (F9_STEP5_REVIEW.md S5-01). A client request
    never influences this. Its contents are folded into
    compute_config_digest() below, so editing it changes job identity
    instead of silently reusing a stale cached result produced under a
    different identity configuration."""
    root: Path
    daily_quota: int = 95
    max_workers: int = 4
    max_retries: int = 2
    per_call_timeout: float = 15.0
    max_requests_per_job: int = 90
    identity_config_path: Path | None = None
    network_enabled: bool = True
    # Whether a report may still publish when one or more needed dates
    # never finished fetching (F9_STEP5_REVIEW.md S5-10). Deliberately a
    # server-controlled setting, not the client-supplied `allow_partial`
    # request flag: that flag controls population-loading mode (a frozen
    # checkpoint vs. a committed extract) -- an unrelated question from
    # whether this server tolerates an incomplete source fetch.
    tolerate_incomplete_fetch: bool = False
    # Bounded exponential backoff applied before a retry of a real
    # (retryable) source failure, not the first attempt (F9_STEP5_REVIEW.md
    # S5-11) -- see jobs.JobBudget.
    retry_backoff_base: float = 0.5
    retry_backoff_max: float = 8.0

    def cache_root(self) -> Path:
        return self.root / "cache"

    def quota_root(self) -> Path:
        return self.root / "quota"

    def jobs_root(self) -> Path:
        return self.root / "jobs"

    def revisions_root(self, run_id: str) -> Path:
        return self.root / "revisions" / run_id


def load_identity_evidence(config: ResultsApiConfig) -> tuple[list, list]:
    """Reads config.identity_config_path (if set) and returns the
    (aliases, verified_mappings) evidence-shaped lists it holds, unparsed --
    evaluate_run's own _parse_alias/_parse_verified_mapping/validate_aliases/
    validate_verified_mappings already validate these exactly the same way
    the CLI's --input evidence file is validated, so this does not duplicate
    that validation, only locates the data. Returns ([], []) when no
    identity config is configured, matching the prior (empty) behavior."""
    if config.identity_config_path is None:
        return [], []
    try:
        data = json.loads(config.identity_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultsApiError(f"identity config {config.identity_config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ResultsApiError(f"identity config {config.identity_config_path}: not a JSON object")
    aliases = data.get("aliases", [])
    verified_mappings = data.get("verified_mappings", [])
    if not isinstance(aliases, list) or not isinstance(verified_mappings, list):
        raise ResultsApiError(
            f"identity config {config.identity_config_path}: "
            "'aliases' and 'verified_mappings' must be lists")
    return aliases, verified_mappings


def compute_config_digest(config: ResultsApiConfig, bundle=None) -> str:
    bundle = freeze_local_sources(config.identity_config_path) if bundle is None else bundle
    identity_bytes = json.dumps([(o["kind"], o["sha256"]) for o in bundle["raw_sources"]]).encode()
    payload = json.dumps({
        "daily_quota": config.daily_quota, "max_workers": config.max_workers,
        "max_retries": config.max_retries, "per_call_timeout": config.per_call_timeout,
        "max_requests_per_job": config.max_requests_per_job, "parser_version": PARSER_VERSION,
        "tolerate_incomplete_fetch": config.tolerate_incomplete_fetch,
        "network_enabled": config.network_enabled, "evaluation_version": "historical_results_v1",
        "retry_backoff_base": config.retry_backoff_base, "retry_backoff_max": config.retry_backoff_max,
        "identity_config_sha256": hashlib.sha256(identity_bytes).hexdigest(),
    }, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_population(registry, run_id: str, *, allow_partial: bool = False):
    """-> (provider, input_digest, rows, population_complete) where rows is
    row_id -> raw ticket dict, row_id = f"{digest}:{index}" (same scheme as
    inputs.audit_run).

    allow_partial implements the plan's frozen-checkpoint mode
    (F9_RESULTS_PLAN.md section 2, F9_STEP5_REVIEW.md S5-10): when the run
    has not succeeded, it reads the worker's last-saved checkpoint instead
    of raising, and freezes that checkpoint's exact bytes as this job's
    input (same digest/row-identity scheme as a committed extract) with
    population_complete=False. When the run has succeeded, the checkpoint
    path is never taken -- registry.saved_codes_snapshot() itself returns
    the committed extract with population_complete=True regardless of
    allow_partial, so a complete run is never silently weakened to a
    partial one. allow_partial=False (the default) preserves the original
    behavior: only a succeeded run's committed extract is ever read."""
    if allow_partial:
        path, raw, partial = registry.saved_codes_snapshot(run_id)
    else:
        path, raw = registry.committed_extract_snapshot(run_id)
        partial = False
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
    return data.get("provider"), digest, rows, not partial


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
                               "original_selections": original_selections, "original_ticket": ticket}
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
                normalized = _selection_kickoff_evidence(kickoff)
                dates.add(normalized["utc"][:10] if normalized["utc"] else kickoff[:10])
    return sorted(dates)


def _selection_kickoff_evidence(kickoff_raw) -> dict:
    """Mirrors inputs.py's _kickoff_timezone_bucket detection: an
    offset-aware raw kickoff string (an explicit "Z" suffix, or a
    "+hh:mm"/"-hh:mm" offset after the date portion) carries real,
    independently parseable timezone evidence and gets precision="exact"
    with a UTC-normalized instant; a naive string (no offset) stays
    "date_only" -- never assumed to be UTC or any particular local time
    (fixtures.py's own documented rule, S4-08). raw is preserved
    byte-for-byte either way, since evaluate_run's audit binding
    (_validate_ticket_matches_audited_original) compares it against the
    original selection's kickoff field verbatim. A missing/malformed raw
    value becomes the literal "unknown" placeholder KickoffTime.raw requires
    to be nonempty; it is never treated as if it were a real timestamp."""
    if not isinstance(kickoff_raw, str) or not kickoff_raw:
        return {"raw": "unknown", "precision": "date_only", "utc": None, "timezone_evidence": None}
    tail = kickoff_raw[10:]
    if kickoff_raw.endswith("Z") or "+" in tail or "-" in tail:
        try:
            parsed = datetime.fromisoformat(
                kickoff_raw[:-1] + "+00:00" if kickoff_raw.endswith("Z") else kickoff_raw)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            utc = parsed.astimezone(timezone.utc)
            return {"raw": kickoff_raw, "precision": "exact", "utc": utc.isoformat(),
                    "timezone_evidence": "explicit_offset_in_raw_kickoff_string"}
    return {"raw": kickoff_raw, "precision": "date_only", "utc": None, "timezone_evidence": None}


def _can_evaluate_ticket(ticket):
    if not isinstance(ticket, dict):
        return False
    if ticket.get("code") is not None and not isinstance(ticket["code"], str):
        return False
    legs = ticket.get("selections")
    if not isinstance(legs, list) or not legs:
        return False
    if isinstance(ticket.get("num_legs"), int) and ticket["num_legs"] != len(legs):
        return False
    return all(isinstance(leg, dict) and (leg.get("specifier") is None or isinstance(leg["specifier"], str))
        and all(isinstance(leg.get(k), str) and leg[k]
        for k in ("event", "league", "sport", "market", "pick", "kickoff")) for leg in legs)


def _ticket_to_evidence(row_id: str, ticket: dict, provider: str | None) -> dict:
    legs = []
    selections = ticket.get("selections")
    for leg_index, selection in enumerate(selections if isinstance(selections, list) else []):
        if not isinstance(selection, dict):
            continue
        kickoff_raw = selection.get("kickoff")
        # A real selection carries its own native source event id (and,
        # for lineless markets, an explicit "" specifier) -- discarding
        # these forfeits VerifiedMapping's exact-id matching tier and
        # bare over/under line resolution for no reason (F9_STEP5_REVIEW.md
        # S5-01).
        native_event_id = selection.get("native_event_id")
        if native_event_id is None:
            native_event_id = selection.get("event_id")
        provider_event_id = str(native_event_id) if native_event_id is not None else None
        legs.append({
            "leg_id": f"{row_id}:{leg_index}",
            "provider": provider or "",
            "provider_event_id": provider_event_id,
            "event_display": selection.get("event") or "",
            "league_display": selection.get("league") or "",
            "sport": selection.get("sport") or "",
            "market": selection.get("market") or "",
            "pick": selection.get("pick") or "",
            "specifier": selection.get("specifier") or "",
            # kickoff.raw must equal the original selection's kickoff value
            # exactly -- evaluate_run's audit binding
            # (_validate_ticket_matches_audited_original) compares this leg
            # against the same original selection field-for-field.
            "kickoff": _selection_kickoff_evidence(kickoff_raw),
        })
    return {
        "ticket_id": ticket.get("code") or row_id,
        "row_id": row_id,
        "provider": provider or "",
        "raw_bet_type": str(ticket["bet_type"]) if ticket.get("bet_type") is not None else "",
        "selected_systems": ticket.get("selected_systems"),
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


def _make_fetch_one(client, cache: FixtureCache, quota: QuotaTracker, *,
                     refresh: bool = False, access_errors=None) -> Callable:
    def fetch_one(task: Task):
        date = task.payload
        key = _cache_key_for_date(date)

        def _fetch():
            # Runs only while cache.get_or_fetch holds this key's lock,
            # and only once per actual miss/stale result (S5-07) -- so a
            # concurrent fetch_one for the same date never also records a
            # quota request or issues its own source call for data this
            # call is already retrieving.
            try:
                quota.record_request()
            except QuotaExceededError as exc:
                raise FatalTaskError(str(exc)) from exc
            try:
                raw_fixtures = client.get_fixtures_by_date(date)
                fetched_at = datetime.now(timezone.utc).isoformat()
                fixtures = [build_fixture(item, fetched_at=fetched_at)
                            for item in filter_in_scope(raw_fixtures)]
            except ApiFootballAccessError as exc:
                if access_errors is None:
                    raise FatalTaskError(str(exc)) from exc
                access_errors[date] = {"code": "source_access_restricted", "message": str(exc)}
                return json.dumps({"access_error": str(exc)}).encode(), False
            except ApiFootballTransportError as exc:
                # A transport/5xx failure is evidence of nothing about
                # fixture data -- it reached no usable answer -- so it is
                # safe to retry through the generic runner's retry loop,
                # unlike every other ApiFootballError (F9_STEP5_REVIEW.md
                # S5-11). Raising a plain exception (not FatalTaskError)
                # is what makes _attempt_task retry it.
                raise RuntimeError(f"retryable api-football failure: {exc}") from exc
            except ApiFootballError as exc:
                raise FatalTaskError(str(exc)) from exc
            # An empty response (no in-scope fixtures returned for this
            # date) is no-result evidence, not a confirmed final state --
            # it means "nothing published/in-scope yet", which can still
            # change, so it gets the short pending refresh window rather
            # than the 24h terminal one (F9_STEP5_REVIEW.md S5-09). Only a
            # response that actually names finished fixtures is terminal.
            all_terminal = bool(fixtures) and all(f.is_finished for f in fixtures)
            return json.dumps(raw_fixtures).encode("utf-8"), all_terminal

        if refresh:
            # refresh=True forces a bypass of the normal staleness check
            # (F9_STEP5_REVIEW.md S5-10) -- every date is refetched
            # regardless of age -- but the S5-09 terminal-entry guard
            # inside get_or_fetch still applies underneath this, so a
            # forced refresh can never erase a previously validated
            # terminal fact with a worse (non-terminal) result.
            cache.get_or_fetch(key, _fetch, is_stale=lambda entry: True)
        else:
            cache.get_or_fetch(key, _fetch)
        entry = cache.get(key)
        if entry is not None:
            cached = json.loads(entry.raw_bytes)
            if isinstance(cached, dict) and "access_error" in cached and access_errors is not None:
                access_errors[date] = {"code": "source_access_restricted", "message": cached["access_error"]}
        return date
    return fetch_one


def _fixtures_for_date(cache: FixtureCache, date: str) -> list:
    entry = cache.get(_cache_key_for_date(date))
    if entry is None:
        return []
    return _fixtures_from_entry(entry)


def _fixtures_from_entry(entry):
    raw_fixtures = json.loads(entry.raw_bytes)
    if not isinstance(raw_fixtures, list):
        return []
    return [build_fixture(item, fetched_at=entry.fetched_at.isoformat())
            for item in filter_in_scope(raw_fixtures)]


def _stage_revision(config: ResultsApiConfig, run_id: str, evidence: dict, report: dict, *,
                      input_digest: str, config_digest: str, audit_rows: dict | None = None) -> dict:
    """Stage and verify revision files without exposing a completion marker.

    The worker may be cancelled while this writes. Publication happens later
    under the job status lock, so a cancelled draft leaves the last report intact.

    When given, audit_rows is archived as audit_population.json alongside
    evidence.json/manifest.json, in exactly the layout __main__.py's
    --replay already expects from a prior `evaluate` command's output
    directory (F9_STEP4_REREVIEW.md S4R-04) -- so an HTTP-published revision
    directory becomes a valid --replay target after commit, with row_id/decisions
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
    return manifest


def _publish_staged_revision(config: ResultsApiConfig, run_id: str, staged: dict) -> str:
    revision_id = staged["revision_id"]
    _write_atomic(config.revisions_root(run_id) / revision_id / "manifest.json",
                  json.dumps(staged, indent=2, sort_keys=True).encode("utf-8"))
    _write_atomic(config.revisions_root(run_id) / "latest.json",
                  json.dumps({"revision_id": revision_id}, indent=2).encode("utf-8"))
    return revision_id


def publish_revision(config: ResultsApiConfig, run_id: str, evidence: dict, report: dict, *,
                     input_digest: str, config_digest: str, audit_rows: dict | None = None) -> str:
    """Publish a standalone revision; job workers use the guarded commit below."""
    staged = _stage_revision(config, run_id, evidence, report, input_digest=input_digest,
                             config_digest=config_digest, audit_rows=audit_rows)
    return _publish_staged_revision(config, run_id, staged)


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
            "rule_versions": report["rule_versions"],
            "population_complete": report.get("population_complete", True),
            "checked_count": len(report.get("checked_row_ids", [])),
            "leg_counts": report.get("leg_counts", {}),
            "ticket_unresolved_reasons": report.get("ticket_unresolved_reasons", {}),
            "retrieval_errors": report.get("retrieval_errors", {})}


def list_codes(config: ResultsApiConfig, run_id: str, revision_id: str, *,
                offset: int = 0, limit: int = 50, status=None, row_id=None) -> dict:
    if offset < 0:
        raise ResultsApiError("offset must be >= 0")
    if limit <= 0:
        raise ResultsApiError("limit must be > 0")
    limit = min(limit, MAX_LIST_LIMIT)
    _, report = _verified_revision_report(config, run_id, revision_id)
    tickets = report["tickets"]
    filtered = _filter_tickets(tickets, status)
    if row_id is not None:
        if not any(t.get("row_id") == row_id for t in tickets):
            raise ResultsApiError("row_id is not in this report")
        filtered = [t for t in filtered if t.get("row_id") == row_id]
    return {"revision_id": revision_id, "total": len(tickets), "filtered_total": len(filtered),
            "offset": offset, "limit": limit, "rows": filtered[offset:offset + limit]}


def _filter_tickets(tickets, status):
    if status not in (None, "", "won", "lost", "void", "pending", "unresolved", "not_checked"):
        raise ResultsApiError("unknown outcome filter")
    if not status:
        return tickets
    if status == "not_checked":
        return [t for t in tickets if t.get("checked") is False]
    return [t for t in tickets if t["status"] == status]


def list_saved_codes(registry, run_id, *, offset=0, limit=50, snapshot=None, allow_partial=False, status=None, row_id=None):
    provider, digest, rows, complete = load_population(registry, run_id, allow_partial=allow_partial)
    if snapshot is not None and snapshot != digest:
        raise ResultsApiError("saved population changed; reload this run")
    if offset < 0 or limit <= 0:
        raise ResultsApiError("invalid pagination")
    _filter_tickets([], status)
    keys = list(rows) if status in (None, "", "unresolved", "not_checked") else []
    if row_id is not None:
        if row_id not in rows:
            raise ResultsApiError("row_id is not in this saved population")
        keys = [key for key in keys if key == row_id]
    filtered_total = len(keys)
    keys = keys[offset:offset + min(limit, MAX_LIST_LIMIT)]
    return {"revision_id": None, "input_digest": digest, "total": len(rows), "filtered_total": filtered_total,
            "provider": provider, "population_complete": complete, "offset": offset,
            "limit": min(limit, MAX_LIST_LIMIT), "rows": [
                {"row_id": key, "ticket_id": rows[key].get("code") if isinstance(rows[key], dict) else None,
                 "original_ticket": rows[key], "status": "unresolved", "reason": "not_checked",
                 "checked": False, "legs": []} for key in keys]}


def _default_client_factory(config: ResultsApiConfig) -> Callable[[], object]:
    """Binds the configured per-call timeout into the default client's own
    transport timeout, so the two are not two independent clocks
    (F9_STEP5_REVIEW.md S5-08): config.per_call_timeout already bounds how
    long run_result_job waits on one fetch_one attempt, and this makes the
    client's own HTTP request time out at the same budget rather than its
    unrelated 10-second default."""
    return lambda: ApiFootballClient(timeout_s=config.per_call_timeout)


def _job_identity_digest(input_digest: str, allow_partial: bool) -> str:
    """Folds allow_partial into job identity (F9_STEP5_REVIEW.md S5-10): a
    frozen-checkpoint (partial) evaluation and a committed-extract
    (complete) evaluation of the same run must never share identity even
    if their content digests happened to coincide, since they carry
    different population_complete guarantees that a reused job record
    would silently paper over."""
    return hashlib.sha256(f"{input_digest}:{allow_partial}".encode()).hexdigest()


def submit_evaluation(registry, config: ResultsApiConfig, job_store: JobStore, run_id: str, *,
                       refresh: bool = False, allow_partial: bool = False,
                       client_factory: Callable[[], object] | None = None, row_id: str | None = None):
    """Starts (or returns the existing active job for) one evaluation of
    run_id. Never blocks on fetching/evaluating/publishing -- the real work
    runs on a background thread; this returns as soon as the job record
    exists.

    allow_partial implements the plan's frozen-checkpoint mode (see
    load_population) -- a cancelled/failed run with a saved checkpoint can
    still be evaluated, with the published report labeled
    population_complete=False, instead of always raising ResultsApiError
    for any non-succeeded run (F9_STEP5_REVIEW.md S5-10).

    refresh forces every date this run needs past the cache's own
    staleness check, so a bypassed source re-fetch happens even for an
    entry that would otherwise still be considered fresh -- but S5-09's
    terminal-entry guard still applies underneath it, so a forced refresh
    can never erase a previously validated terminal fact with a worse
    (non-terminal) result.

    client_factory defaults to a real ApiFootballClient bound to this
    config's per_call_timeout (S5-08) -- left None rather than defaulted at
    def-time so it can see the config this call actually received."""
    if not isinstance(run_id, str) or not _SAFE_ID_RE.fullmatch(run_id):
        raise ResultsApiError("invalid run id")
    if client_factory is None:
        client_factory = _default_client_factory(config)
    provider, input_digest, rows, population_complete = load_population(
        registry, run_id, allow_partial=allow_partial)
    if row_id is not None and (not isinstance(row_id, str) or row_id not in rows):
        raise ResultsApiError("row_id is not in this saved population")
    try:
        bundle = freeze_local_sources(config.identity_config_path)
        config_digest = compute_config_digest(config, bundle)
    except (OSError, ValueError, TypeError) as exc:
        raise ResultsApiError(f"invalid historical source configuration: {exc}") from exc
    _, extract_bytes = registry.saved_codes_snapshot(run_id)[:2] if allow_partial else registry.committed_extract_snapshot(run_id)
    if hashlib.sha256(extract_bytes).hexdigest() != input_digest:
        raise ResultsApiError("saved population changed while freezing; retry")
    bundle["raw_sources"].append({"kind": "original_extract", "sha256": input_digest,
        "base64": base64.b64encode(extract_bytes).decode("ascii")})
    job_identity = _job_identity_digest(input_digest, allow_partial)
    if row_id is not None:
        job_identity = hashlib.sha256(f"{job_identity}:{row_id}".encode()).hexdigest()
    outer, ownership = job_store.claim_worker(run_id, job_identity, config_digest)
    if ownership is not None:
        try:
            thread = threading.Thread(
                target=_run_evaluation_job,
                args=(config, job_store, outer.job_id, run_id, provider, input_digest, config_digest,
                      rows, population_complete, refresh, client_factory, ownership, row_id, bundle),
                daemon=True)
            thread.start()
        except Exception as exc:
            try:
                job_store.update(outer.job_id, lambda r: r.update(
                    state=JobState.FAILED,
                    error={"code": "worker_launch_failed", "message": str(exc)}))
            finally:
                ownership.close()
    return job_store.get(outer.job_id)


def _commit_publication(job_store: JobStore, outer_job_id: str,
                        ownership: WorkerOwnership, publish: Callable[[], dict]) -> JobRecord:
    """Linearize cancellation and publication under the same status lock.

    Files are staged first. Only an active owner with no accepted cancellation
    may publish the completion marker/latest pointer. A cancellation waiting for
    this lock after publication began observes SUCCEEDED and is a terminal no-op.
    Superseded/failed workers never invoke the side-effecting callback.
    """
    ownership.require(outer_job_id)

    def commit(record):
        ownership.require(outer_job_id)
        if record.get("cancel_requested"):
            record.update(state=JobState.CANCELLED, result=None)
        else:
            result = publish()
            record.update(state=JobState.SUCCEEDED, result=result)

    return job_store.update(outer_job_id, commit)


def _run_evaluation_job(config, job_store, outer_job_id, run_id, *args):
    # Different single-code jobs still publish into one run. Serialize their read /
    # merge / publish cycle so simultaneous checks cannot lose checked membership.
    root = config.revisions_root(run_id)
    owner = args[7]
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / "evaluation.lock").open("a+b") as handle:
            while True:
                if job_store.get(outer_job_id).cancel_requested:
                    job_store.update(outer_job_id, lambda r: r.update(state=JobState.CANCELLED))
                    return
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.05)
            _evaluate_owned_population(config, job_store, outer_job_id, run_id, *args)
    except Exception as exc:
        job_store.update(outer_job_id, lambda r: r.update(state=JobState.FAILED,
            error={"code": "evaluation_failed", "message": str(exc)}))
    finally:
        owner.close()


def _evaluate_owned_population(config: ResultsApiConfig, job_store: JobStore, outer_job_id: str, run_id: str,
                         provider, input_digest: str, config_digest: str, rows: dict,
                         population_complete: bool, refresh: bool,
                         client_factory: Callable[[], object], ownership: WorkerOwnership,
                         row_id=None, bundle=None) -> None:
    try:
        ownership.require(outer_job_id)
        cache = FixtureCache(config.cache_root())
        quota = QuotaTracker(config.quota_root(), "api_football", config.daily_quota)
        bundle = bundle or freeze_local_sources(config.identity_config_path)
        checked = set(rows) if row_id is None else {row_id}
        previous_fixtures = []
        previous_raw_sources = []
        previous_errors = {}
        try:
            previous = get_latest_report(config, run_id)
            manifest, previous_report = _verified_revision_report(config, run_id, previous["revision_id"])
            if manifest["input_digest"] == input_digest and manifest["config_digest"] == config_digest:
                checked.update(previous_report.get("checked_row_ids", []))
                prior_evidence = json.loads((config.revisions_root(run_id) / previous["revision_id"] / "evidence.json").read_bytes())
                previous_fixtures = prior_evidence.get("fixtures", [])
                previous_raw_sources = prior_evidence.get("raw_sources", [])
                previous_errors = prior_evidence.get("retrieval_errors", {})
        except ReportNotFoundError:
            pass
        selected_rows = {key: value for key, value in rows.items() if key in checked}
        fetch_rows = selected_rows if row_id is None else {row_id: rows[row_id]}
        dates = _dates_needed(fetch_rows)
        incomplete: tuple = ()
        access_errors = dict(previous_errors)
        for date in dates:
            access_errors.pop(date, None)
        network_enabled = config.network_enabled and bundle["network_enabled"]
        client = None
        if network_enabled and bundle["fixtures"]:
            local_report = evaluate_run({
                "fixtures": [_fixture_to_evidence_dict(f) for f in bundle["fixtures"]],
                "aliases": bundle["aliases"], "verified_mappings": bundle["verified_mappings"],
                "tickets": [_ticket_to_evidence(k, t, provider) for k, t in fetch_rows.items()
                            if _can_evaluate_ticket(t)]})
            missing_dates = set()
            for ticket in local_report["tickets"]:
                for leg in ticket["legs"]:
                    if leg["rule_id"] and leg["status"] in ("pending", "unresolved"):
                        raw = (leg["selection"].get("kickoff") or {}).get("raw")
                        missing_dates.update(_dates_needed({"r": {"selections": [{"kickoff": raw}]}}))
            dates = sorted(missing_dates)

        if dates and network_enabled:
            inner = job_store.create_or_reuse(f"{run_id}:fetch", input_digest, config_digest)
            job_store.update(outer_job_id, lambda r: r["progress"].update(fetch_job_id=inner.job_id))
            if job_store.get(outer_job_id).cancel_requested:
                job_store.request_cancel(inner.job_id)
            try:
                client = client_factory()
            except ApiFootballError as exc:
                if not config.tolerate_incomplete_fetch:
                    job_store.update(outer_job_id, lambda r: r.update(
                        state=JobState.FAILED, error={"code": "source_unavailable", "message": str(exc)}))
                    return
                client = None
                access_errors.update({d: {"code": "source_unavailable", "message": str(exc)} for d in dates})
            fetch_one = _make_fetch_one(client, cache, quota, refresh=refresh, access_errors=access_errors) if client is not None else None
            budget = JobBudget(max_requests=config.max_requests_per_job, max_retries=config.max_retries,
                                per_call_timeout=config.per_call_timeout, max_workers=config.max_workers,
                                retry_backoff_base=config.retry_backoff_base,
                                retry_backoff_max=config.retry_backoff_max)
            tasks = [Task(key=date, payload=date) for date in dates]
            try:
                if fetch_one is None:
                    job_store.update(inner.job_id, lambda r: r.update(state=JobState.FAILED))
                    outcome = None
                else:
                    outcome = run_result_job(job_store, inner.job_id, tasks, fetch_one=fetch_one, budget=budget)
            except FatalTaskError as exc:
                if not config.tolerate_incomplete_fetch:
                    job_store.update(outer_job_id, lambda r: r.update(
                        state=JobState.FAILED, error={"code": "fatal_fetch_error", "message": str(exc)}))
                    return
                access_errors.update({d: {"code": "source_unavailable", "message": str(exc)} for d in dates})
                outcome = None
            except JobError as exc:
                job_store.update(outer_job_id, lambda r: r.update(
                    state=JobState.FAILED, error={"code": "fetch_job_error", "message": str(exc)}))
                return
            if outcome is not None and outcome.cancelled:
                job_store.update(outer_job_id, lambda r: r.update(state=JobState.CANCELLED))
                return
            incomplete = outcome.incomplete_task_keys if outcome is not None else tuple(dates)
            for date in incomplete:
                access_errors.setdefault(date, {"code": "source_fetch_incomplete", "message": "Retrieval did not complete"})
            if incomplete and not config.tolerate_incomplete_fetch:
                job_store.update(outer_job_id, lambda r: r.update(
                    state=JobState.FAILED, error={
                        "code": "incomplete_fetch",
                        "message": f"{len(incomplete)} date(s) not fetched and "
                                   f"tolerate_incomplete_fetch is false"}))
                return

        if job_store.get(outer_job_id).cancel_requested:
            job_store.update(outer_job_id, lambda r: r.update(state=JobState.CANCELLED))
            return

        fixtures = []
        raw_sources = list(bundle["raw_sources"]) + previous_raw_sources
        for date, raw in list(getattr(client, "raw_observations", {}).items()):
            raw_sources.append({"kind": "api_football_http", "date": date,
                "sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode("ascii")})
        for date in dates:
            entry = cache.get(_cache_key_for_date(date))
            if entry is not None:
                raw = entry.raw_bytes
                raw_sources.append({"kind": "api_football", "sha256": hashlib.sha256(raw).hexdigest(),
                                    "base64": base64.b64encode(raw).decode("ascii")})
                cached = json.loads(raw)
                if isinstance(cached, dict) and "access_error" in cached:
                    access_errors.setdefault(date, {"code": "source_access_restricted", "message": cached["access_error"]})
                fixtures.extend(_fixtures_from_entry(entry))
            if not network_enabled:
                access_errors.setdefault(date, {"code": "source_network_disabled", "message": "Using local evidence only"})
        # A revision freezes every source observation it actually uses. Local imports
        # are independent observations; disagreements remain ambiguous, never guessed.
        normalized = {(f["provenance"]["source"], f["provenance"]["source_fixture_id"]): f for f in previous_fixtures}
        for fixture in fixtures + list(bundle["fixtures"]):
            item = _fixture_to_evidence_dict(fixture)
            normalized[(item["provenance"]["source"], item["provenance"]["source_fixture_id"])] = item
        evidence = {
            "fixtures": list(normalized.values()),
            "raw_sources": list({(r["kind"], r["sha256"]): r for r in raw_sources}.values()),
            "retrieval_errors": access_errors,
            "checked_row_ids": sorted(checked),
            "original_tickets": rows,
            "population_complete": population_complete,
            "ticket_structures": bundle["ticket_structures"],
            "aliases": bundle["aliases"], "verified_mappings": bundle["verified_mappings"],
            # A malformed row (not a dict) is never supplied here -- it has
            # no usable ticket content to convert -- but it is still a key
            # in audit_rows above, so evaluate_run reports it as an
            # explicit unresolved row instead of a silently shrunk N.
            "tickets": [_ticket_to_evidence(row_id, ticket, provider) for row_id, ticket in selected_rows.items()
                        if _can_evaluate_ticket(ticket)],
        }
        audit_rows = _build_audit_rows(provider, rows)
        try:
            report = evaluate_run(evidence, audit_rows=audit_rows)
        except EvaluationInputError as exc:
            job_store.update(outer_job_id, lambda r: r.update(
                state=JobState.FAILED, error={"code": "evaluation_failed", "message": str(exc)}))
            return
        # A partial-run report must not read as if it carries a full-run's
        # claim (F9_RESULTS_PLAN.md): population_complete=False marks every
        # report built from a frozen checkpoint rather than a committed
        # extract.


        try:
            staged = _stage_revision(config, run_id, evidence, report, audit_rows=audit_rows,
                                     input_digest=input_digest, config_digest=config_digest)

            def publish():
                revision_id = _publish_staged_revision(config, run_id, staged)
                return {"revision_id": revision_id, "counts": report["counts"],
                        "incomplete_dates": list(incomplete)}

            _commit_publication(job_store, outer_job_id, ownership, publish)
        except (OSError, ResultsApiError) as exc:
            job_store.update(outer_job_id, lambda r: r.update(
                state=JobState.FAILED, error={"code": "publish_failed", "message": str(exc)}))

    except Exception as exc:  # the background thread must never die silently
        job_store.update(outer_job_id, lambda r: r.update(
            state=JobState.FAILED, error={"code": "internal_error", "message": str(exc)}))

    finally:
        ownership.close()


def cancel_job(job_store: JobStore, job_id: str):
    """Cancel a live owner, or promptly settle a record whose owner is gone.

    Never infer death from progress age. The same lifetime lock used at launch
    protects both outer evaluation and inner fetch workers.
    """
    record = job_store.request_cancel(job_id)
    fetch_job_id = record.progress.get("fetch_job_id")
    if fetch_job_id:
        job_store.request_cancel(fetch_job_id)
    if not record.is_terminal:
        ownership = job_store.try_acquire_worker(job_id)
        if ownership is not None:
            try:
                record = job_store.update(job_id, lambda r: r.update(
                    state=JobState.FAILED,
                    error={"code": "orphaned_worker",
                           "message": "worker lifetime lock released before completion"}))
            finally:
                ownership.close()
    return record
