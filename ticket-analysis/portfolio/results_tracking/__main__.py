"""Run with python3 -m portfolio.results_tracking --help from ticket-analysis/.

Two subcommands:

- audit (F9_RESULTS_PLAN.md section 10 step 1): freezes and inventories
  retained extracts for named runs. No source fetching, fixture matching, or
  settlement happens here.
- evaluate (section 10 step 4): a deterministic, offline evaluator over a
  single evidence file of already-resolved fixtures/aliases/verified
  mappings and tickets/legs -- no network, no source adapters. This is the
  "deterministic offline replay" the plan requires before any network
  batching or live API integration.
"""
from __future__ import annotations

from dataclasses import asdict
import base64
import binascii
from collections import Counter

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..registry import RunRegistry, RunNotFoundError, RunStateError
from .evaluation import evaluate_selection_detailed
from .fixtures import Fixture, FixtureProvenance, FixtureState, InvalidFixtureError, KickoffTime
from .inputs import PopulationAuditError, audit_run
from .matching import Alias, MatchingError, VerifiedMapping
from .reporting import Outcome, Rate, TicketOutcome, aggregate_ticket, compute_percentages, counts_from_ticket_outcomes
from .rules import registry as rule_registry
from .rules.provider_types import verify_bet_type
from .rules.scores import InvalidScoreError, RegulationScore


class EvaluationInputError(ValueError):
    """The supplied evidence file is malformed, as distinct from a normal
    unresolved/pending/unmatched outcome for a well-formed leg."""


def _get(container, key, context):
    container = _require_dict(container, context)
    if key not in container:
        raise EvaluationInputError(f"{context}: missing required field {key!r}")
    return container[key]


def _require_dict(value, context):
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{context}: expected a JSON object, got {type(value).__name__}")
    return value


def _require_list(value, context):
    if not isinstance(value, list):
        raise EvaluationInputError(f"{context}: expected a JSON array, got {type(value).__name__}")
    return value


def _require_str(value, context):
    if not isinstance(value, str):
        raise EvaluationInputError(f"{context}: expected a string, got {type(value).__name__}")
    return value


def _get_str(container, key, context):
    return _require_str(_get(container, key, context), f"{context}.{key}")


def _parse_kickoff(payload, context):
    payload = _require_dict(payload, context)
    utc_raw = payload.get("utc")
    if utc_raw is not None:
        _require_str(utc_raw, f"{context}.utc")
    utc = datetime.fromisoformat(utc_raw) if utc_raw else None
    return KickoffTime(raw=_get_str(payload, "raw", context), precision=_get_str(payload, "precision", context),
                        utc=utc, timezone_evidence=payload.get("timezone_evidence"))


def _parse_regulation_score(payload, context):
    if payload is None:
        return None
    payload = _require_dict(payload, context)
    return RegulationScore(home=_get(payload, "home", context), away=_get(payload, "away", context),
                            ht_home=payload.get("ht_home"), ht_away=payload.get("ht_away"))


def _parse_fixture(payload, index):
    context = f"fixtures[{index}]"
    payload = _require_dict(payload, context)
    provenance_payload = _get(payload, "provenance", context)
    provenance = FixtureProvenance(
        source=_get_str(provenance_payload, "source", context),
        source_fixture_id=_get_str(provenance_payload, "source_fixture_id", context),
        fetched_at=_get_str(provenance_payload, "fetched_at", context),
        content_sha256=_get_str(provenance_payload, "content_sha256", context))
    season = payload.get("season")
    if season is not None:
        _require_str(season, f"{context}.season")
    return Fixture(
        provenance=provenance, competition=_get_str(payload, "competition", context),
        season=season, home_team=_get_str(payload, "home_team", context),
        away_team=_get_str(payload, "away_team", context),
        kickoff=_parse_kickoff(_get(payload, "kickoff", context), context),
        state=FixtureState(_get_str(payload, "state", context)),
        regulation_score=_parse_regulation_score(payload.get("regulation_score"), context))


def _parse_alias(payload, index):
    context = f"aliases[{index}]"
    payload = _require_dict(payload, context)
    return Alias(namespace=_get_str(payload, "namespace", context),
                 display_name=_get_str(payload, "display_name", context),
                 canonical=_get_str(payload, "canonical", context))


def _parse_verified_mapping(payload, index):
    context = f"verified_mappings[{index}]"
    payload = _require_dict(payload, context)
    return VerifiedMapping(
        provider=_get_str(payload, "provider", context),
        provider_event_id=_get_str(payload, "provider_event_id", context),
        league_display=_get_str(payload, "league_display", context),
        home_display=_get_str(payload, "home_display", context),
        away_display=_get_str(payload, "away_display", context), source=_get_str(payload, "source", context),
        source_fixture_id=_get_str(payload, "source_fixture_id", context),
        source_competition=_get_str(payload, "source_competition", context),
        source_home_team=_get_str(payload, "source_home_team", context),
        source_away_team=_get_str(payload, "source_away_team", context),
        source_kickoff_raw=_get_str(payload, "source_kickoff_raw", context),
        verified_at=_get_str(payload, "verified_at", context), evidence=_get_str(payload, "evidence", context))


def _parse_leg(payload, ticket_index, leg_index):
    context = f"tickets[{ticket_index}].legs[{leg_index}]"
    payload = _require_dict(payload, context)
    provider_event_id = payload.get("provider_event_id")
    if provider_event_id is not None:
        _require_str(provider_event_id, f"{context}.provider_event_id")
    specifier = payload.get("specifier", "")
    _require_str(specifier, f"{context}.specifier")
    return dict(
        leg_id=_get_str(payload, "leg_id", context), provider=_get_str(payload, "provider", context),
        provider_event_id=provider_event_id,
        event_display=_get_str(payload, "event_display", context),
        league_display=_get_str(payload, "league_display", context), sport=_get_str(payload, "sport", context),
        market=_get_str(payload, "market", context), pick=_get_str(payload, "pick", context),
        specifier=specifier,
        kickoff=_parse_kickoff(_get(payload, "kickoff", context), context))


def _serialize_rate(rate: Rate):
    return {"numerator": rate.numerator, "denominator": rate.denominator,
            "value": rate.value, "reason": rate.reason}


def _load_audited_population(audit_dir: Path) -> dict:
    """Reads a prior `audit` command's output directory and returns
    row_id -> {run_id, code, num_legs, num_selections, provider,
    original_selections} for every retained ticket row across every run it
    inventoried. Verifies every manifested file's hash first, so a
    changed/corrupted audit artifact is rejected rather than silently
    trusted (F9_STEP4_REVIEW.md S4-03).

    original_selections/provider are read from the run's preserved extract
    (not just the run report's row metadata), so a later caller can bind a
    supplied row_id to what the audited ticket actually said -- not merely
    that the row_id exists in the population -- and reject a row_id reused
    against unrelated ticket content (F9_STEP4_REREVIEW.md S4R-03)."""
    manifest_path = audit_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvaluationInputError(f"--audit-dir {audit_dir}: cannot read manifest.json: {error}")
    manifested_files = _require_dict(manifest, "audit manifest").get("files", {})
    for rel_path, expected_sha256 in manifested_files.items():
        actual_path = audit_dir / rel_path
        actual_sha256 = hashlib.sha256(actual_path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise EvaluationInputError(
                f"--audit-dir {audit_dir}: {rel_path} does not match its manifested sha256 "
                "-- audit artifact changed or corrupted since it was written")

    summary = json.loads((audit_dir / "summary.json").read_text(encoding="utf-8"))
    audit_rows = {}
    for run in _require_dict(summary, "audit summary").get("runs", []):
        run_id = run["run_id"]
        run_report_rel_path = f"{run_id}.json"
        if run_report_rel_path not in manifested_files:
            raise EvaluationInputError(
                f"--audit-dir {audit_dir}: {run_report_rel_path} is not covered by manifest.json")
        run_report = json.loads((audit_dir / run_report_rel_path).read_text(encoding="utf-8"))

        extract_rel_path = run_report.get("preserved_extract_path")
        if not extract_rel_path or extract_rel_path not in manifested_files:
            raise EvaluationInputError(
                f"--audit-dir {audit_dir}: run {run_id} has no manifest-covered "
                "preserved_extract_path -- cannot bind its row_ids to original ticket content")
        extract_path = audit_dir / extract_rel_path
        try:
            extract_bytes = extract_path.read_bytes()
        except OSError as error:
            raise EvaluationInputError(
                f"--audit-dir {audit_dir}: cannot read preserved extract for run {run_id}: {error}")
        if hashlib.sha256(extract_bytes).hexdigest() != run_report.get("extract_sha256"):
            raise EvaluationInputError(
                f"--audit-dir {audit_dir}: preserved extract for run {run_id} does not match its "
                "recorded extract_sha256")
        extract_data = json.loads(extract_bytes)
        qualifying = extract_data.get("qualifying") if isinstance(extract_data, dict) else None
        if not isinstance(qualifying, list):
            raise EvaluationInputError(
                f"--audit-dir {audit_dir}: run {run_id}'s preserved extract has no "
                "'qualifying' list")
        run_provider = extract_data.get("provider")

        for row in run_report.get("rows", []):
            row_id = row["row_id"]
            if row_id in audit_rows:
                raise EvaluationInputError(
                    f"--audit-dir {audit_dir}: row_id {row_id!r} appears in more than one "
                    "audited run")
            index = row.get("index")
            original_selections = []
            if isinstance(index, int) and 0 <= index < len(qualifying):
                original_ticket = qualifying[index]
                if isinstance(original_ticket, dict):
                    raw_selections = original_ticket.get("selections")
                    if isinstance(raw_selections, list):
                        original_selections = [s for s in raw_selections if isinstance(s, dict)]
            audit_rows[row_id] = {"run_id": run_id, "code": row.get("code"),
                                   "num_legs": row.get("num_legs"),
                                   "num_selections": row.get("num_selections"),
                                   "provider": run_provider,
                                   "original_selections": original_selections}
    return audit_rows


def _validate_ticket_matches_audited_original(ticket, provider, legs_payload, audited_row, t_context):
    """Rejects a supplied ticket whose provider or legs disagree with the
    audited original ticket that the same row_id was retained from --
    checking only that a row_id exists (its membership in the audited
    population) is not the same as checking that the supplied ticket is the
    one that was actually audited (F9_STEP4_REREVIEW.md S4R-03)."""
    audited_provider = audited_row.get("provider")
    if audited_provider is not None and provider != audited_provider:
        raise EvaluationInputError(
            f"{t_context}: provider {provider!r} does not match the audited original "
            f"ticket's provider {audited_provider!r} for this row_id")
    original_ticket = audited_row.get("original_ticket")
    if isinstance(original_ticket, dict):
        original_type = str(original_ticket["bet_type"]) if original_ticket.get("bet_type") is not None else ""
        if (ticket.get("raw_bet_type") != original_type
                or ticket.get("selected_systems") != original_ticket.get("selected_systems")
                or ticket.get("ticket_id") != (original_ticket.get("code") or ticket.get("row_id"))):
            raise EvaluationInputError(f"{t_context}: ticket structure/code differs from audited original")
    original_selections = audited_row.get("original_selections") or []
    if len(legs_payload) != len(original_selections):
        raise EvaluationInputError(
            f"{t_context}: supplied {len(legs_payload)} legs but the audited original "
            f"ticket for this row_id has {len(original_selections)} selections")
    for leg_index, (leg_payload, original) in enumerate(zip(legs_payload, original_selections)):
        leg_payload = _require_dict(leg_payload, f"{t_context}.legs[{leg_index}]")
        kickoff_payload = leg_payload.get("kickoff")
        kickoff_raw = kickoff_payload.get("raw") if isinstance(kickoff_payload, dict) else None
        field_pairs = (
            ("event_display", "event"), ("league_display", "league"),
            ("market", "market"), ("pick", "pick"), ("sport", "sport"),
        )
        mismatched = [supplied_field for supplied_field, original_field in field_pairs
                      if leg_payload.get(supplied_field) != original.get(original_field)]
        if kickoff_raw != original.get("kickoff"):
            mismatched.append("kickoff.raw")
        if isinstance(original_ticket, dict):
            native = original.get("native_event_id")
            if native is None:
                native = original.get("event_id")
            if leg_payload.get("provider_event_id") != (str(native) if native is not None else None):
                mismatched.append("provider_event_id")
            if leg_payload.get("specifier") != (original.get("specifier") or ""):
                mismatched.append("specifier")
        if mismatched:
            raise EvaluationInputError(
                f"{t_context}.legs[{leg_index}]: {', '.join(mismatched)} do not match the "
                "audited original ticket's selection at the same position for this row_id -- "
                "a row_id is bound to its original content, not just its membership in the "
                "audited population")


def _load_prior_evaluation(prior_dir: Path):
    """Reads a prior `evaluate` command's output directory and returns the
    raw evidence bytes, the parsed evidence dict and the audit_rows dict (or
    None if that run was unanchored) it was produced from -- verifying every
    manifested file's hash first, exactly as _load_audited_population does
    for an audit directory. This is what lets a report be replayed, with the
    same N and row decisions, after both the original --input file and
    --audit-dir have been deleted or moved away (F9_STEP4_REREVIEW.md
    S4R-04): the population snapshot itself was archived alongside the
    report, not just an absolute path to where it used to live."""
    manifest_path = prior_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvaluationInputError(f"--replay {prior_dir}: cannot read manifest.json: {error}")
    for rel_path, expected_sha256 in _require_dict(manifest, "prior evaluation manifest").get("files", {}).items():
        actual_path = prior_dir / rel_path
        actual_sha256 = hashlib.sha256(actual_path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise EvaluationInputError(
                f"--replay {prior_dir}: {rel_path} does not match its manifested sha256 "
                "-- archived evaluation artifact changed or corrupted since it was written")

    raw_evidence_bytes = (prior_dir / "evidence.json").read_bytes()
    evidence = json.loads(raw_evidence_bytes)
    audit_population_path = prior_dir / "audit_population.json"
    audit_rows = (json.loads(audit_population_path.read_text(encoding="utf-8"))
                  if audit_population_path.exists() else None)
    return raw_evidence_bytes, evidence, audit_rows


def evaluate_run(evidence: dict, audit_rows: dict | None = None) -> dict:
    """Pure: evidence in, a report dict out. No filesystem access, so this
    is directly unit-testable and is exactly what offline replay re-runs.

    When audit_rows is given (from _load_audited_population), every ticket
    in evidence["tickets"] must carry a "row_id" naming one of its keys;
    unknown or duplicate row_ids are rejected, and the denominator is the
    full audited row set -- an audited row missing from evidence becomes an
    explicit unresolved ticket rather than silently shrinking the population
    (F9_STEP4_REVIEW.md S4-03). When audit_rows is None, evaluate_run runs as
    a synthetic/offline harness over whatever tickets evidence supplies,
    with no population anchoring at all."""
    evidence = _require_dict(evidence, "evidence")
    for observation in _require_list(evidence.get("raw_sources", []), "raw_sources"):
        observation = _require_dict(observation, "raw_sources entry")
        try:
            raw = base64.b64decode(_get_str(observation, "base64", "raw_sources"), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise EvaluationInputError("invalid raw source bytes") from exc
        if hashlib.sha256(raw).hexdigest() != observation.get("sha256"):
            raise EvaluationInputError("raw source digest mismatch")
    fixtures = tuple(_parse_fixture(f, i) for i, f in enumerate(_require_list(
        evidence.get("fixtures", []), "evidence.fixtures")))
    aliases = tuple(_parse_alias(a, i) for i, a in enumerate(_require_list(
        evidence.get("aliases", []), "evidence.aliases")))
    verified_mappings = tuple(_parse_verified_mapping(m, i) for i, m in enumerate(_require_list(
        evidence.get("verified_mappings", []), "evidence.verified_mappings")))

    ticket_reports = []
    ticket_outcomes = []
    seen_row_ids = set()
    for t_index, ticket in enumerate(_require_list(_get(evidence, "tickets", "evidence"), "evidence.tickets")):
        t_context = f"tickets[{t_index}]"
        ticket = _require_dict(ticket, t_context)
        ticket_id = _get_str(ticket, "ticket_id", t_context)
        provider = _get_str(ticket, "provider", t_context)
        raw_bet_type = _get_str(ticket, "raw_bet_type", t_context)
        try:
            type_verification = verify_bet_type(provider, raw_bet_type,
                row_id=ticket.get("row_id"), selected_systems=ticket.get("selected_systems"),
                num_legs=len(_require_list(ticket.get("legs"), f"{t_context}.legs")),
                records=_require_list(evidence.get("ticket_structures", []), "ticket_structures"))
        except ValueError as exc:
            raise EvaluationInputError(str(exc)) from exc
        structure_verified = type_verification["status"] == "verified"

        row_id = None
        if audit_rows is not None:
            row_id = _get_str(ticket, "row_id", t_context)
            if row_id not in audit_rows:
                raise EvaluationInputError(
                    f"{t_context}: row_id {row_id!r} is not in the audited population")
            if row_id in seen_row_ids:
                raise EvaluationInputError(f"{t_context}: row_id {row_id!r} repeated across tickets")
            seen_row_ids.add(row_id)
            _validate_ticket_matches_audited_original(
                ticket, provider,
                _require_list(_get(ticket, "legs", t_context), f"{t_context}.legs"),
                audit_rows[row_id], t_context)

        leg_outcomes = []
        leg_reports = []
        seen_leg_ids = set()
        for l_index, leg_payload in enumerate(_require_list(
                _get(ticket, "legs", t_context), f"{t_context}.legs")):
            leg_kwargs = _parse_leg(leg_payload, t_index, l_index)
            if leg_kwargs["leg_id"] in seen_leg_ids:
                raise EvaluationInputError(
                    f"{t_context}: duplicate leg_id {leg_kwargs['leg_id']!r}")
            seen_leg_ids.add(leg_kwargs["leg_id"])
            # Single pass: evaluate_selection_detailed() returns both the
            # LegOutcome and the MatchDecision (or None when the sport/market
            # was unsupported and matching was never attempted) it computed
            # along the way, so the report's matching provenance
            # (F9_STEP4_REVIEW.md S4-04) never requires a second, ungated
            # match_selection_event() call that could raise for a leg
            # evaluate_selection_detailed() itself never attempted to match
            # (F9_STEP4_REREVIEW.md S4R-06).
            leg_outcome, decision = evaluate_selection_detailed(
                **leg_kwargs, candidate_fixtures=fixtures, aliases=aliases,
                verified_mappings=verified_mappings)
            leg_outcomes.append(leg_outcome)
            matched_fixture = None
            match_tier = None
            if decision is not None:
                match_tier = decision.tier
                if decision.fixture is not None:
                    matched_fixture = {"source": decision.fixture.provenance.source,
                                        "source_fixture_id": decision.fixture.provenance.source_fixture_id}
            leg_reports.append({
                "leg_id": leg_outcome.leg_id, "status": leg_outcome.status.value,
                "rule_id": leg_outcome.rule_id, "reason": leg_outcome.reason,
                "match_tier": match_tier, "matched_fixture": matched_fixture,
                "selection": {k: leg_payload.get(k) for k in
                    ("event_display", "league_display", "sport", "market", "pick", "specifier", "kickoff")},
                "fixture": None if decision is None or decision.fixture is None else {
                    "home_team": decision.fixture.home_team, "away_team": decision.fixture.away_team,
                    "competition": decision.fixture.competition, "state": decision.fixture.state.value,
                    "kickoff": decision.fixture.kickoff.raw,
                    "score": None if decision.fixture.regulation_score is None else
                        asdict(decision.fixture.regulation_score),
                    "provenance": asdict(decision.fixture.provenance)},
                "candidates": [] if decision is None else [{"source": c.fixture.provenance.source, "source_fixture_id": c.fixture.provenance.source_fixture_id, "event": f"{c.fixture.home_team} - {c.fixture.away_team}", "kickoff": c.fixture.kickoff.raw, "evidence": list(c.evidence)} for c in decision.candidates],
            })
        ticket_outcome = aggregate_ticket(leg_outcomes, structure_verified=structure_verified)
        ticket_outcomes.append(ticket_outcome)

        ticket_reports.append({
            "ticket_id": ticket_id,
            "row_id": row_id,
            "status": ticket_outcome.status.value,
            "reason": ticket_outcome.reason,
            "proxy_all_legs_won": ticket_outcome.proxy_all_legs_won,
            "structure_verification": type_verification,
            "legs": leg_reports,
        })

    if audit_rows is not None:
        for row_id in sorted(set(audit_rows) - seen_row_ids):
            not_supplied = TicketOutcome(Outcome.UNRESOLVED, "not_supplied_in_evidence", False, ())
            ticket_outcomes.append(not_supplied)
            ticket_reports.append({
                "ticket_id": None, "row_id": row_id, "status": not_supplied.status.value,
                "reason": not_supplied.reason, "proxy_all_legs_won": False, "legs": [],
            })

    counts = counts_from_ticket_outcomes(ticket_outcomes)
    checked = evidence.get("checked_row_ids")
    retrieval_errors = evidence.get("retrieval_errors", {})
    originals = evidence.get("original_tickets", {})
    if checked is not None:
        checked = _require_list(checked, "checked_row_ids")
        if any(not isinstance(key, str) for key in checked) or len(set(checked)) != len(checked):
            raise EvaluationInputError("checked_row_ids must contain unique row identities")
        if audit_rows is not None and not set(checked).issubset(audit_rows):
            raise EvaluationInputError("checked_row_ids outside audited population")
        for ticket_report in ticket_reports:
            key = ticket_report["row_id"]
            ticket_report["original_ticket"] = originals.get(key)
            ticket_report["checked"] = key in checked
            if key not in checked:
                ticket_report["reason"] = "not_checked"
            elif ticket_report["reason"] == "not_supplied_in_evidence":
                ticket_report["reason"] = "malformed_original_ticket"
            for leg in ticket_report.get("legs", []):
                if leg["reason"] == "fixture_not_matched":
                    raw = (leg["selection"].get("kickoff") or {}).get("raw", "")
                    # Query dates use UTC for explicit offsets, saved calendar date
                    # otherwise. No timezone interpretation is invented here.
                    try:
                        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        date = instant.astimezone(timezone.utc).date().isoformat() if instant.tzinfo else raw[:10]
                    except ValueError:
                        date = raw[:10]
                    error = retrieval_errors.get(date)
                    if error:
                        leg["reason"] = error["code"]
                        leg["retrieval_error"] = error
    percentages = compute_percentages(counts)
    return {
        "tickets": ticket_reports,
        "counts": {"won": counts.won, "lost": counts.lost, "void": counts.void,
                   "pending": counts.pending, "unresolved": counts.unresolved, "total": counts.total},
        "percentages": {
            "confirmed_win_rate": _serialize_rate(percentages.confirmed_win_rate),
            "decided_win_loss_rate": _serialize_rate(percentages.decided_win_loss_rate),
            "classification_coverage": _serialize_rate(percentages.classification_coverage),
            "eventual_binary_win_bound_low": _serialize_rate(percentages.eventual_binary_win_bound_low),
            "eventual_binary_win_bound_high": _serialize_rate(percentages.eventual_binary_win_bound_high),
        },
        "rule_versions": dict(rule_registry.rule_versions()),
        "leg_counts": dict(Counter(leg["status"] for ticket in ticket_reports for leg in ticket["legs"])),
        "ticket_unresolved_reasons": dict(Counter(ticket["reason"] for ticket in ticket_reports
            if ticket["status"] == "unresolved" and ticket["reason"])),
        **({"checked_row_ids": checked, "retrieval_errors": retrieval_errors,
            "population_complete": evidence.get("population_complete", True)} if checked is not None else {}),
    }


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def write_json_atomic(path, value):
    """Writes to a sibling temp file then renames into place, so a reader
    never observes a partially written file at `path` -- the manifest is
    the completion marker, so it must appear whole or not at all."""
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    tmp_path.replace(path)


def _audit_command(args):
    try:
        if args.providers and len(args.providers) != len(args.run_ids):
            raise ValueError("--provider must be supplied once per --run-id, or not at all")
        if args.expect_rows and len(args.expect_rows) != len(args.run_ids):
            raise ValueError("--expect-rows must be supplied once per --run-id, or not at all")
        registry = RunRegistry(args.workspace)
        reports = []
        for i, run_id in enumerate(args.run_ids):
            expected_provider = args.providers[i] if args.providers else None
            expected_rows = args.expect_rows[i] if args.expect_rows else None
            reports.append(audit_run(registry, run_id, expected_provider, expected_rows))
        generated_at = datetime.now(timezone.utc).isoformat()
        args.output.mkdir(parents=True, exist_ok=False)
        extracts_dir = args.output / "extracts"
        extracts_dir.mkdir()
        for report in reports:
            raw_extract_bytes = report.pop("_raw_extract_bytes")
            extract_path = extracts_dir / f"{report['run_id']}.extract.json"
            extract_path.write_bytes(raw_extract_bytes)
            if hashlib.sha256(extract_path.read_bytes()).hexdigest() != report["extract_sha256"]:
                raise PopulationAuditError(
                    f"run {report['run_id']}: preserved extract bytes do not match the "
                    "verified digest")
            report["preserved_extract_path"] = str(extract_path.relative_to(args.output))
            write_json(args.output / f"{report['run_id']}.json", report)
        summary = {
            "generated_at": generated_at,
            "runs": [{"run_id": r["run_id"], "provider": r["provider"], "row_count": r["row_count"],
                      "extract_sha256": r["extract_sha256"], "unique_fixtures": r["unique_fixtures"],
                      "identity_modes": r["identity_modes"],
                      "preserved_extract_path": r["preserved_extract_path"],
                      "conflicting_observation_count": len(r["conflicting_observations"]),
                      "conflicting_fixture_observation_count": len(r["conflicting_fixture_observations"]),
                      "duplicate_ticket_code_count": len(r["duplicate_ticket_codes"]),
                      "conflicting_ticket_code_count": len(r["conflicting_ticket_codes"]),
                      "raw_response_missing_count": len(r["raw_response_missing"])}
                     for r in reports],
        }
        write_json(args.output / "summary.json", summary)
        write_json_atomic(args.output / "manifest.json", {
            "generated_at": generated_at,
            "files": {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(args.output.rglob("*")) if p.is_file()},
        })
        for r in reports:
            print(f"{r['run_id']} ({r['provider']}): {r['row_count']} retained rows, "
                  f"{r['unique_fixtures']} unique fixtures, "
                  f"{len(r['conflicting_observations'])} conflicting leg observations, "
                  f"{len(r['raw_response_missing'])} rows missing a raw response path")
        print(f"Audit written to {args.output.resolve()}")
        return 0
    except (OSError, ValueError, TypeError, RunNotFoundError, RunStateError, PopulationAuditError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


def _evaluate_command(args):
    try:
        if args.replay and (args.input or args.audit_dir):
            raise ValueError("--replay is mutually exclusive with --input/--audit-dir")
        if not args.replay and not args.input:
            raise ValueError("--input is required unless --replay is given")

        if args.replay:
            raw_evidence_bytes, evidence, audit_rows = _load_prior_evaluation(args.replay)
            population_source = f"replay:{args.replay.resolve()}"
        else:
            raw_evidence_bytes = args.input.read_bytes()
            evidence = json.loads(raw_evidence_bytes)
            audit_rows = _load_audited_population(args.audit_dir) if args.audit_dir else None
            population_source = str(args.audit_dir.resolve()) if args.audit_dir else None

        report = evaluate_run(evidence, audit_rows=audit_rows)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        report["population_source"] = population_source
        args.output.mkdir(parents=True, exist_ok=False)
        # The raw evidence bytes are preserved verbatim (not re-serialized)
        # and hashed into the manifest, so a later caller can independently
        # replay this exact evaluation even after the original --input file
        # is deleted or changed (F9_STEP4_REVIEW.md S4-04). When the run was
        # anchored to an audited population, that population snapshot itself
        # -- not just the --audit-dir path, which may not exist by replay
        # time -- is archived the same way, so N and every row's decision
        # (including audited rows omitted from evidence) survive replay even
        # after both the original evidence file and audit directory are gone
        # (F9_STEP4_REREVIEW.md S4R-04).
        (args.output / "evidence.json").write_bytes(raw_evidence_bytes)
        if audit_rows is not None:
            write_json(args.output / "audit_population.json", audit_rows)
        write_json(args.output / "report.json", report)
        write_json_atomic(args.output / "manifest.json", {
            "generated_at": report["generated_at"],
            "files": {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(args.output.rglob("*")) if p.is_file()},
        })
        counts = report["counts"]
        print(f"{len(report['tickets'])} tickets evaluated: {counts['won']} won, {counts['lost']} lost, "
              f"{counts['void']} void, {counts['pending']} pending, {counts['unresolved']} unresolved")
        print(f"Evaluation written to {args.output.resolve()}")
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, EvaluationInputError,
            InvalidFixtureError, InvalidScoreError, MatchingError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="F9 results-tracking CLI: population audit and offline ticket evaluation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser(
        "audit", help="Freeze and inventory retained run extracts; no outcome or settlement claim.")
    audit_parser.add_argument("--run-id", action="append", dest="run_ids", required=True,
                              help="A succeeded run id to audit (repeatable)")
    audit_parser.add_argument("--provider", action="append", dest="providers", default=[],
                              help="Expected provider for the run at the same position (optional, repeatable)")
    audit_parser.add_argument("--expect-rows", action="append", dest="expect_rows", default=[], type=int,
                              help="Expected retained row count for the run at the same position (optional, repeatable)")
    audit_parser.add_argument("--workspace", type=Path, help="Repo root holding results/runs/ (default: autodetect)")
    audit_parser.add_argument("--output", required=True, type=Path,
                              help="New audit directory; existing directories are refused")
    audit_parser.set_defaults(handler=_audit_command)

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Deterministic offline evaluation over an evidence file of "
                         "fixtures/aliases/verified mappings/tickets. No network.")
    evaluate_parser.add_argument("--input", type=Path, default=None,
                                 help="Evidence JSON file (fixtures, aliases, verified_mappings, tickets). "
                                      "Required unless --replay is given.")
    evaluate_parser.add_argument(
        "--audit-dir", type=Path, default=None,
        help="A prior 'audit' command's output directory. When given, every evidence "
             "ticket must carry a row_id from that audited population, and the "
             "denominator is the full audited row set (an audited row missing from "
             "evidence becomes an explicit unresolved ticket). When omitted, evaluate "
             "runs as a synthetic/offline harness with no population anchoring. Mutually "
             "exclusive with --replay.")
    evaluate_parser.add_argument(
        "--replay", type=Path, default=None,
        help="A prior 'evaluate' command's output directory. Replays its archived "
             "evidence.json and audit_population.json (if that run was anchored), "
             "reproducing the same report even if the original --input file and "
             "--audit-dir no longer exist. Mutually exclusive with --input/--audit-dir.")
    evaluate_parser.add_argument("--output", required=True, type=Path,
                                 help="New evaluation directory; existing directories are refused")
    evaluate_parser.set_defaults(handler=_evaluate_command)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
