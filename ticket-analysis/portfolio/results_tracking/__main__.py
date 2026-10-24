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
from .evaluation import evaluate_selection
from .fixtures import Fixture, FixtureProvenance, FixtureState, InvalidFixtureError, KickoffTime
from .inputs import PopulationAuditError, audit_run
from .matching import Alias, MatchingError, VerifiedMapping
from .reporting import Rate, aggregate_ticket, compute_percentages, counts_from_ticket_outcomes
from .rules.provider_types import verify_bet_type
from .rules.scores import InvalidScoreError, RegulationScore


class EvaluationInputError(ValueError):
    """The supplied evidence file is malformed, as distinct from a normal
    unresolved/pending/unmatched outcome for a well-formed leg."""


def _get(container, key, context):
    if key not in container:
        raise EvaluationInputError(f"{context}: missing required field {key!r}")
    return container[key]


def _parse_kickoff(payload, context):
    utc_raw = payload.get("utc")
    utc = datetime.fromisoformat(utc_raw) if utc_raw else None
    return KickoffTime(raw=_get(payload, "raw", context), precision=_get(payload, "precision", context),
                        utc=utc, timezone_evidence=payload.get("timezone_evidence"))


def _parse_regulation_score(payload):
    if payload is None:
        return None
    return RegulationScore(home=payload["home"], away=payload["away"],
                            ht_home=payload.get("ht_home"), ht_away=payload.get("ht_away"))


def _parse_fixture(payload, index):
    context = f"fixtures[{index}]"
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
        regulation_score=_parse_regulation_score(payload.get("regulation_score")))


def _parse_alias(payload, index):
    context = f"aliases[{index}]"
    return Alias(namespace=_get(payload, "namespace", context),
                 display_name=_get(payload, "display_name", context),
                 canonical=_get(payload, "canonical", context))


def _parse_verified_mapping(payload, index):
    context = f"verified_mappings[{index}]"
    return VerifiedMapping(
        provider=_get(payload, "provider", context), provider_event_id=_get(payload, "provider_event_id", context),
        league_display=_get(payload, "league_display", context), home_display=_get(payload, "home_display", context),
        away_display=_get(payload, "away_display", context), source=_get(payload, "source", context),
        source_fixture_id=_get(payload, "source_fixture_id", context),
        verified_at=_get(payload, "verified_at", context), evidence=_get(payload, "evidence", context))


def _parse_leg(payload, ticket_index, leg_index):
    context = f"tickets[{ticket_index}].legs[{leg_index}]"
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


def evaluate_run(evidence: dict) -> dict:
    """Pure: evidence in, a report dict out. No filesystem access, so this
    is directly unit-testable and is exactly what offline replay re-runs."""
    fixtures = tuple(_parse_fixture(f, i) for i, f in enumerate(evidence.get("fixtures", [])))
    aliases = tuple(_parse_alias(a, i) for i, a in enumerate(evidence.get("aliases", [])))
    verified_mappings = tuple(_parse_verified_mapping(m, i)
                               for i, m in enumerate(evidence.get("verified_mappings", [])))

    ticket_reports = []
    ticket_outcomes = []
    for t_index, ticket in enumerate(_get(evidence, "tickets", "evidence")):
        t_context = f"tickets[{t_index}]"
        ticket_id = _get(ticket, "ticket_id", t_context)
        provider = _get(ticket, "provider", t_context)
        raw_bet_type = _get(ticket, "raw_bet_type", t_context)
        structure_verified = verify_bet_type(provider, raw_bet_type)["status"] == "verified"

        leg_outcomes = []
        for l_index, leg_payload in enumerate(_get(ticket, "legs", t_context)):
            leg_kwargs = _parse_leg(leg_payload, t_index, l_index)
            leg_outcomes.append(evaluate_selection(
                **leg_kwargs, candidate_fixtures=fixtures, aliases=aliases,
                verified_mappings=verified_mappings))
        ticket_outcome = aggregate_ticket(leg_outcomes, structure_verified=structure_verified)
        ticket_outcomes.append(ticket_outcome)
        ticket_reports.append({
            "ticket_id": ticket_id,
            "status": ticket_outcome.status.value,
            "reason": ticket_outcome.reason,
            "proxy_all_legs_won": ticket_outcome.proxy_all_legs_won,
            "legs": [{"leg_id": leg.leg_id, "status": leg.status.value,
                      "rule_id": leg.rule_id, "reason": leg.reason} for leg in ticket_outcome.legs],
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
        with args.input.open("r", encoding="utf-8") as stream:
            evidence = json.load(stream)
        report = evaluate_run(evidence)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        args.output.mkdir(parents=True, exist_ok=False)
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
    evaluate_parser.add_argument("--output", required=True, type=Path,
                                 help="New evaluation directory; existing directories are refused")
    evaluate_parser.set_defaults(handler=_evaluate_command)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
