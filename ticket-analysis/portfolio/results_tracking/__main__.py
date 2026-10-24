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

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..registry import RunRegistry, RunNotFoundError, RunStateError
from .evaluation import evaluate_selection, match_decision_for_leg
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


def _parse_kickoff(payload, context):
    payload = _require_dict(payload, context)
    utc_raw = payload.get("utc")
    utc = datetime.fromisoformat(utc_raw) if utc_raw else None
    return KickoffTime(raw=_get(payload, "raw", context), precision=_get(payload, "precision", context),
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
        source=_get(provenance_payload, "source", context),
        source_fixture_id=_get(provenance_payload, "source_fixture_id", context),
        fetched_at=_get(provenance_payload, "fetched_at", context),
        content_sha256=_get(provenance_payload, "content_sha256", context))
    return Fixture(
        provenance=provenance, competition=_get(payload, "competition", context),
        season=payload.get("season"), home_team=_get(payload, "home_team", context),
        away_team=_get(payload, "away_team", context),
        kickoff=_parse_kickoff(_get(payload, "kickoff", context), context),
        state=FixtureState(_get(payload, "state", context)),
        regulation_score=_parse_regulation_score(payload.get("regulation_score"), context))


def _parse_alias(payload, index):
    context = f"aliases[{index}]"
    payload = _require_dict(payload, context)
    return Alias(namespace=_get(payload, "namespace", context),
                 display_name=_get(payload, "display_name", context),
                 canonical=_get(payload, "canonical", context))


def _parse_verified_mapping(payload, index):
    context = f"verified_mappings[{index}]"
    payload = _require_dict(payload, context)
    return VerifiedMapping(
        provider=_get(payload, "provider", context), provider_event_id=_get(payload, "provider_event_id", context),
        league_display=_get(payload, "league_display", context), home_display=_get(payload, "home_display", context),
        away_display=_get(payload, "away_display", context), source=_get(payload, "source", context),
        source_fixture_id=_get(payload, "source_fixture_id", context),
        source_competition=_get(payload, "source_competition", context),
        source_home_team=_get(payload, "source_home_team", context),
        source_away_team=_get(payload, "source_away_team", context),
        verified_at=_get(payload, "verified_at", context), evidence=_get(payload, "evidence", context))


def _parse_leg(payload, ticket_index, leg_index):
    context = f"tickets[{ticket_index}].legs[{leg_index}]"
    payload = _require_dict(payload, context)
    return dict(
        leg_id=_get(payload, "leg_id", context), provider=_get(payload, "provider", context),
        provider_event_id=payload.get("provider_event_id"),
        event_display=_get(payload, "event_display", context),
        league_display=_get(payload, "league_display", context), sport=_get(payload, "sport", context),
        market=_get(payload, "market", context), pick=_get(payload, "pick", context),
        specifier=payload.get("specifier", ""),
        kickoff=_parse_kickoff(_get(payload, "kickoff", context), context))


def _serialize_rate(rate: Rate):
    return {"numerator": rate.numerator, "denominator": rate.denominator,
            "value": rate.value, "reason": rate.reason}


def _load_audited_population(audit_dir: Path) -> dict:
    """Reads a prior `audit` command's output directory and returns
    row_id -> {run_id, code, num_legs, num_selections} for every retained
    ticket row across every run it inventoried. Verifies every manifested
    file's hash first, so a changed/corrupted audit artifact is rejected
    rather than silently trusted (F9_STEP4_REVIEW.md S4-03)."""
    manifest_path = audit_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvaluationInputError(f"--audit-dir {audit_dir}: cannot read manifest.json: {error}")
    for rel_path, expected_sha256 in _require_dict(manifest, "audit manifest").get("files", {}).items():
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
        run_report = json.loads((audit_dir / f"{run_id}.json").read_text(encoding="utf-8"))
        for row in run_report.get("rows", []):
            row_id = row["row_id"]
            if row_id in audit_rows:
                raise EvaluationInputError(
                    f"--audit-dir {audit_dir}: row_id {row_id!r} appears in more than one "
                    "audited run")
            audit_rows[row_id] = {"run_id": run_id, "code": row.get("code"),
                                   "num_legs": row.get("num_legs"),
                                   "num_selections": row.get("num_selections")}
    return audit_rows


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
        ticket_id = _get(ticket, "ticket_id", t_context)
        provider = _get(ticket, "provider", t_context)
        raw_bet_type = _get(ticket, "raw_bet_type", t_context)
        structure_verified = verify_bet_type(provider, raw_bet_type)["status"] == "verified"

        row_id = None
        if audit_rows is not None:
            row_id = _get(ticket, "row_id", t_context)
            if row_id not in audit_rows:
                raise EvaluationInputError(
                    f"{t_context}: row_id {row_id!r} is not in the audited population")
            if row_id in seen_row_ids:
                raise EvaluationInputError(f"{t_context}: row_id {row_id!r} repeated across tickets")
            seen_row_ids.add(row_id)

        leg_outcomes = []
        seen_leg_ids = set()
        for l_index, leg_payload in enumerate(_require_list(
                _get(ticket, "legs", t_context), f"{t_context}.legs")):
            leg_kwargs = _parse_leg(leg_payload, t_index, l_index)
            if leg_kwargs["leg_id"] in seen_leg_ids:
                raise EvaluationInputError(
                    f"{t_context}: duplicate leg_id {leg_kwargs['leg_id']!r}")
            seen_leg_ids.add(leg_kwargs["leg_id"])
            leg_outcomes.append(evaluate_selection(
                **leg_kwargs, candidate_fixtures=fixtures, aliases=aliases,
                verified_mappings=verified_mappings))
        ticket_outcome = aggregate_ticket(leg_outcomes, structure_verified=structure_verified)
        ticket_outcomes.append(ticket_outcome)

        # Retain, per leg, which fixture actually matched (tier + source +
        # source_fixture_id) alongside the leg's own evaluated result, so a
        # later caller can independently replay/audit the matching decision
        # without re-deriving it (F9_STEP4_REVIEW.md S4-04).
        leg_reports = []
        for leg_payload, leg in zip(_get(ticket, "legs", t_context), ticket_outcome.legs):
            leg_kwargs = _parse_leg(leg_payload, t_index, len(leg_reports))
            decision = match_decision_for_leg(
                provider=leg_kwargs["provider"], provider_event_id=leg_kwargs["provider_event_id"],
                event_display=leg_kwargs["event_display"], league_display=leg_kwargs["league_display"],
                kickoff=leg_kwargs["kickoff"], candidate_fixtures=fixtures, aliases=aliases,
                verified_mappings=verified_mappings)
            matched_fixture = None
            if decision.fixture is not None:
                matched_fixture = {"source": decision.fixture.provenance.source,
                                    "source_fixture_id": decision.fixture.provenance.source_fixture_id}
            leg_reports.append({
                "leg_id": leg.leg_id, "status": leg.status.value,
                "rule_id": leg.rule_id, "reason": leg.reason,
                "match_tier": decision.tier, "matched_fixture": matched_fixture,
            })
        ticket_reports.append({
            "ticket_id": ticket_id,
            "row_id": row_id,
            "status": ticket_outcome.status.value,
            "reason": ticket_outcome.reason,
            "proxy_all_legs_won": ticket_outcome.proxy_all_legs_won,
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
        raw_evidence_bytes = args.input.read_bytes()
        evidence = json.loads(raw_evidence_bytes)
        audit_rows = _load_audited_population(args.audit_dir) if args.audit_dir else None
        report = evaluate_run(evidence, audit_rows=audit_rows)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        report["population_source"] = str(args.audit_dir.resolve()) if args.audit_dir else None
        args.output.mkdir(parents=True, exist_ok=False)
        # The raw evidence bytes are preserved verbatim (not re-serialized)
        # and hashed into the manifest, so a later caller can independently
        # replay this exact evaluation even after the original --input file
        # is deleted or changed (F9_STEP4_REVIEW.md S4-04).
        (args.output / "evidence.json").write_bytes(raw_evidence_bytes)
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
    evaluate_parser.add_argument("--input", required=True, type=Path,
                                 help="Evidence JSON file (fixtures, aliases, verified_mappings, tickets)")
    evaluate_parser.add_argument(
        "--audit-dir", type=Path, default=None,
        help="A prior 'audit' command's output directory. When given, every evidence "
             "ticket must carry a row_id from that audited population, and the "
             "denominator is the full audited row set (an audited row missing from "
             "evidence becomes an explicit unresolved ticket). When omitted, evaluate "
             "runs as a synthetic/offline harness with no population anchoring.")
    evaluate_parser.add_argument("--output", required=True, type=Path,
                                 help="New evaluation directory; existing directories are refused")
    evaluate_parser.set_defaults(handler=_evaluate_command)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
