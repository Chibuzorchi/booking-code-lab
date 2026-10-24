"""Population audit: freeze and inventory a run's retained extract.

F9_RESULTS_PLAN.md section 10 step 1. This module makes no outcome or
settlement claim; it only establishes the denominator for later work. It
reuses contracts.identity() for leg/event identity rather than deriving a new
key scheme, and registry.committed_extract_snapshot() for hash-verified bytes
(never build_pool() or any stage1/stage2-filtered view, and never a bare file
read by path) so the population matches the plan's required source exactly.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from ..contracts import identity

REQUIRED_SELECTION_FIELDS = ("event", "league", "market", "pick", "kickoff", "sport")


class PopulationAuditError(ValueError):
    """The committed extract fails a structural precondition for auditing."""


def _raw_response_status(path_value):
    """None when real bytes are available at path_value; otherwise a short
    reason. A nonempty path is not taken on faith -- the file must actually
    exist, since availability at audit time is distinct from (and a
    precondition for) preserving it for replay."""
    if not path_value:
        return "path_absent"
    try:
        exists = Path(path_value).is_file()
    except (OSError, ValueError):
        return "path_absent"
    return None if exists else "file_not_found"


def _kickoff_timezone_bucket(kickoff):
    if not isinstance(kickoff, str) or not kickoff:
        return "missing"
    if kickoff.endswith("Z"):
        return "offset_aware"
    tail = kickoff[10:]  # after the date portion, so a date's own '-' is not mistaken for an offset
    if "+" in tail or "-" in tail:
        return "offset_aware"
    return "naive"


def audit_run(registry, run_id, expected_provider=None, expected_rows=None):
    """Freeze one run's committed extract and inventory it.

    Raises PopulationAuditError for a structural problem (wrong provider,
    wrong odds basis, row-count mismatch against an explicit expectation).
    Returns a JSON-serializable report; makes no outcome or settlement claim.
    """
    path, raw = registry.committed_extract_snapshot(run_id)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PopulationAuditError(f"run {run_id}: committed extract is not valid JSON") from exc
    if not isinstance(data, dict):
        raise PopulationAuditError(f"run {run_id}: committed extract is not a JSON object")

    provider = data.get("provider")
    if expected_provider is not None and provider != expected_provider:
        raise PopulationAuditError(
            f"run {run_id}: provider is {provider!r}, expected {expected_provider!r}")
    if data.get("odds_basis") != "parsed_leg_product":
        raise PopulationAuditError(
            f"run {run_id}: odds_basis is {data.get('odds_basis')!r}, expected 'parsed_leg_product'")
    qualifying = data.get("qualifying")
    if not isinstance(qualifying, list):
        raise PopulationAuditError(f"run {run_id}: 'qualifying' is not a list")
    if expected_rows is not None and len(qualifying) != expected_rows:
        raise PopulationAuditError(
            f"run {run_id}: {len(qualifying)} retained rows, expected {expected_rows}")

    rows = []
    sports = Counter()
    leagues = Counter()
    markets = Counter()
    identity_modes = Counter()
    bet_types = Counter()
    completeness = Counter()
    kickoff_timezones = Counter()
    missing_field_rows = defaultdict(set)
    fixtures = set()
    kickoffs_by_timezone = defaultdict(list)
    raw_response_missing = []
    observations_by_leg = defaultdict(set)
    observations_by_event = defaultdict(set)
    ticket_codes_seen = defaultdict(list)

    for index, ticket in enumerate(qualifying):
        row_id = f"{digest}:{index}"
        if not isinstance(ticket, dict):
            rows.append({"row_id": row_id, "index": index, "error": "ticket is not a JSON object"})
            continue
        completeness[ticket.get("retrieval_completeness")] += 1
        bet_types[ticket.get("bet_type")] += 1
        raw_status = _raw_response_status(ticket.get("raw_response_path"))
        if raw_status is not None:
            raw_response_missing.append({"row_id": row_id, "reason": raw_status})
        code = ticket.get("code")
        if code:
            ticket_content_hash = hashlib.sha256(
                json.dumps(ticket, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            ticket_codes_seen[code].append((row_id, ticket_content_hash))
        raw_selections = ticket.get("selections")
        if isinstance(raw_selections, list):
            selections = raw_selections
        else:
            selections = []
            missing_field_rows["selections_not_list"].add(row_id)
        for selection in selections:
            if not isinstance(selection, dict):
                missing_field_rows["selection_not_object"].add(row_id)
                continue
            sports[selection.get("sport")] += 1
            leagues[selection.get("league")] += 1
            markets[selection.get("market")] += 1
            for field in REQUIRED_SELECTION_FIELDS:
                if not selection.get(field):
                    missing_field_rows[field].add(row_id)
            kickoff = selection.get("kickoff")
            bucket = _kickoff_timezone_bucket(kickoff)
            kickoff_timezones[bucket] += 1
            if kickoff:
                kickoffs_by_timezone[bucket].append(kickoff)
            leg_identity, event_identity, mode, reason = identity(selection, provider)
            identity_modes[mode] += 1
            if event_identity is not None:
                fixtures.add(tuple(event_identity))
                observed_fixture = (selection.get("event"), selection.get("league"), kickoff,
                                     selection.get("sport"))
                observations_by_event[tuple(event_identity)].add(observed_fixture)
            if leg_identity is not None:
                observed = (selection.get("event"), selection.get("league"), kickoff,
                            selection.get("sport"), selection.get("market"), selection.get("pick"))
                observations_by_leg[tuple(leg_identity)].add(observed)
        rows.append({"row_id": row_id, "index": index, "code": ticket.get("code"),
                     "num_legs": ticket.get("num_legs"), "num_selections": len(selections)})

    conflicting_observations = [
        {"leg_identity": list(key),
         "observations": [list(o) for o in sorted(obs, key=lambda o: tuple(str(v) for v in o))]}
        for key, obs in observations_by_leg.items() if len(obs) > 1
    ]
    conflicting_fixture_observations = [
        {"event_identity": list(key),
         "observations": [list(o) for o in sorted(obs, key=lambda o: tuple(str(v) for v in o))]}
        for key, obs in observations_by_event.items() if len(obs) > 1
    ]
    duplicate_ticket_codes = []
    conflicting_ticket_codes = []
    for code, entries in ticket_codes_seen.items():
        if len(entries) <= 1:
            continue
        row_ids = [row_id for row_id, _ in entries]
        if len({content_hash for _, content_hash in entries}) == 1:
            duplicate_ticket_codes.append({"code": code, "row_ids": row_ids})
        else:
            conflicting_ticket_codes.append({"code": code, "row_ids": row_ids})

    return {
        "run_id": run_id,
        "provider": provider,
        "population_source": "committed_extract_snapshot",
        "extract_path": str(path),
        "extract_sha256": digest,
        "extract_size": len(raw),
        "row_count": len(qualifying),
        "rows": rows,
        "sports": dict(sports),
        "leagues": dict(leagues),
        "markets": dict(markets),
        "identity_modes": dict(identity_modes),
        "bet_types": dict(bet_types),
        "retrieval_completeness": dict(completeness),
        "kickoff_timezones": dict(kickoff_timezones),
        "first_last_kickoff_by_timezone": {
            bucket: {"first": min(values), "last": max(values)}
            for bucket, values in kickoffs_by_timezone.items()
        },
        "missing_fields": {field: sorted(row_ids) for field, row_ids in missing_field_rows.items()},
        "unique_fixtures": len(fixtures),
        "raw_response_missing": raw_response_missing,
        "conflicting_observations": conflicting_observations,
        "conflicting_fixture_observations": conflicting_fixture_observations,
        "duplicate_ticket_codes": duplicate_ticket_codes,
        "conflicting_ticket_codes": conflicting_ticket_codes,
        # Not JSON-serializable; the already-verified raw bytes read above,
        # for a caller (the CLI) to preserve to disk without reopening the
        # source path. Callers must pop this key before writing the rest of
        # this dict as JSON.
        "_raw_extract_bytes": raw,
    }
