"""F9 step 4: the `evaluate` CLI subcommand -- deterministic offline replay
over a JSON evidence file. No network, no run directories; everything here
is pure in-memory evidence written to a temp file.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from portfolio.results_tracking.__main__ import (
    EvaluationInputError, _load_audited_population, evaluate_run, main,
)


def _fixture_payload(home="Arsenal", away="Everton", state="finished_regulation",
                      score=None, source_fixture_id="1"):
    return {
        "provenance": {"source": "api_football", "source_fixture_id": source_fixture_id,
                       "fetched_at": "2026-10-24T12:00:00Z", "content_sha256": "a" * 64},
        "competition": "Premier League", "season": "2026",
        "home_team": home, "away_team": away,
        "kickoff": {"raw": "2026-10-24", "precision": "date_only"},
        "state": state,
        "regulation_score": score,
    }


def _alias(namespace, display_name, canonical):
    return {"namespace": namespace, "display_name": display_name, "canonical": canonical}


_STANDARD_ALIASES = [
    _alias("sportybet_competition", "Premier League", "EPL"),
    _alias("api_football_competition", "Premier League", "EPL"),
    _alias("sportybet_team", "Arsenal", "Arsenal"),
    _alias("api_football_team", "Arsenal", "Arsenal"),
    _alias("sportybet_team", "Everton", "Everton"),
    _alias("api_football_team", "Everton", "Everton"),
]


def _leg_payload(**overrides):
    defaults = dict(leg_id="leg1", provider="sportybet", event_display="Arsenal - Everton",
                     league_display="Premier League", sport="Football", market="1X2", pick="Home",
                     specifier="", kickoff={"raw": "2026-10-24", "precision": "date_only"})
    defaults.update(overrides)
    return defaults


def _ticket_payload(legs, provider="sportybet", raw_bet_type="0", ticket_id="t1"):
    return {"ticket_id": ticket_id, "provider": provider, "raw_bet_type": raw_bet_type, "legs": legs}


class EvaluateRunPureTests(unittest.TestCase):
    """evaluate_run() takes no filesystem args, so these cover the pipeline
    without touching disk."""

    def test_single_won_leg_ticket_is_unresolved_unverified_structure(self):
        # provider_types.verify_bet_type is always "unverified" today, so an
        # otherwise-all-won ticket must still report UNRESOLVED, not WON.
        evidence = {
            "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
            "aliases": _STANDARD_ALIASES,
            "tickets": [_ticket_payload([_leg_payload()])],
        }
        report = evaluate_run(evidence)
        self.assertEqual(report["tickets"][0]["status"], "unresolved")
        self.assertEqual(report["tickets"][0]["reason"], "unverified_ticket_structure")
        self.assertTrue(report["tickets"][0]["proxy_all_legs_won"])
        self.assertEqual(report["tickets"][0]["legs"][0]["status"], "won")
        self.assertEqual(report["counts"]["unresolved"], 1)
        self.assertEqual(report["counts"]["total"], 1)

    def test_unmatched_leg_is_unresolved_fixture_not_matched(self):
        evidence = {"fixtures": [], "tickets": [_ticket_payload([_leg_payload()])]}
        report = evaluate_run(evidence)
        leg = report["tickets"][0]["legs"][0]
        self.assertEqual(leg["status"], "unresolved")
        self.assertEqual(leg["reason"], "fixture_not_matched")

    def test_pending_fixture_leg_is_pending(self):
        evidence = {
            "fixtures": [_fixture_payload(state="scheduled", score=None)],
            "aliases": _STANDARD_ALIASES,
            "tickets": [_ticket_payload([_leg_payload()])],
        }
        report = evaluate_run(evidence)
        self.assertEqual(report["tickets"][0]["legs"][0]["status"], "pending")

    def test_deterministic_repeat_run_gives_identical_report(self):
        evidence = {
            "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
            "aliases": _STANDARD_ALIASES,
            "tickets": [_ticket_payload([_leg_payload()])],
        }
        first = evaluate_run(evidence)
        second = evaluate_run(evidence)
        self.assertEqual(first, second)

    def test_percentages_computed_over_all_tickets(self):
        won_fixture = _fixture_payload(score={"home": 2, "away": 1}, source_fixture_id="1")
        evidence = {
            "fixtures": [won_fixture],
            "aliases": _STANDARD_ALIASES,
            "tickets": [
                _ticket_payload([_leg_payload(leg_id="l1")], ticket_id="t1"),
                _ticket_payload([], ticket_id="t2"),
            ],
        }
        report = evaluate_run(evidence)
        self.assertEqual(report["counts"]["total"], 2)
        self.assertEqual(report["counts"]["unresolved"], 2)
        self.assertEqual(report["percentages"]["confirmed_win_rate"]["value"], 0.0)

    def test_missing_required_field_raises_evaluation_input_error(self):
        evidence = {"fixtures": [], "tickets": [{"ticket_id": "t1", "provider": "sportybet", "legs": []}]}
        with self.assertRaises(EvaluationInputError):
            evaluate_run(evidence)

    def test_malformed_fixture_state_raises_value_error(self):
        evidence = {
            "fixtures": [_fixture_payload(state="not_a_real_state")],
            "tickets": [_ticket_payload([_leg_payload()])],
        }
        with self.assertRaises(ValueError):
            evaluate_run(evidence)

    def test_root_list_raises_evaluation_input_error_not_attribute_error(self):
        # F9_STEP4_REVIEW.md S4-05: a JSON root of [] must not leak
        # AttributeError from evidence.get().
        with self.assertRaises(EvaluationInputError):
            evaluate_run([])

    def test_short_score_object_raises_evaluation_input_error_not_key_error(self):
        fixture = _fixture_payload(score={"away": 1})  # missing "home"
        evidence = {"fixtures": [fixture], "tickets": []}
        with self.assertRaises(EvaluationInputError):
            evaluate_run(evidence)

    def test_non_list_tickets_raises_evaluation_input_error(self):
        evidence = {"fixtures": [], "tickets": "not-a-list"}
        with self.assertRaises(EvaluationInputError):
            evaluate_run(evidence)

    def test_duplicate_leg_id_within_ticket_raises(self):
        evidence = {
            "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
            "aliases": _STANDARD_ALIASES,
            "tickets": [_ticket_payload([_leg_payload(leg_id="l1"), _leg_payload(leg_id="l1")])],
        }
        with self.assertRaises(EvaluationInputError):
            evaluate_run(evidence)

    def test_report_carries_rule_versions(self):
        evidence = {"fixtures": [], "tickets": []}
        report = evaluate_run(evidence)
        self.assertIn("regulation_1x2", report["rule_versions"])

    def test_leg_report_retains_matched_fixture_identity(self):
        # F9_STEP4_REVIEW.md S4-04: a leg's report must name which fixture
        # it actually matched, for independent replay.
        evidence = {
            "fixtures": [_fixture_payload(score={"home": 2, "away": 1}, source_fixture_id="42")],
            "aliases": _STANDARD_ALIASES,
            "tickets": [_ticket_payload([_leg_payload()])],
        }
        report = evaluate_run(evidence)
        leg = report["tickets"][0]["legs"][0]
        self.assertEqual(leg["matched_fixture"], {"source": "api_football", "source_fixture_id": "42"})
        self.assertEqual(leg["match_tier"], "exact_unique")


def _write_audit_dir(tmp_path: Path, run_id="run-1", rows=()) -> Path:
    """A minimal stand-in for a real `audit` command's output directory --
    only the files _load_audited_population() reads (manifest.json,
    summary.json, <run_id>.json)."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    run_report = {"run_id": run_id, "rows": list(rows)}
    (audit_dir / f"{run_id}.json").write_text(json.dumps(run_report), encoding="utf-8")
    summary = {"runs": [{"run_id": run_id}]}
    (audit_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    manifest = {"files": {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(audit_dir.iterdir())
    }}
    (audit_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return audit_dir


class AuditedPopulationAnchoringTests(unittest.TestCase):
    """F9_STEP4_REVIEW.md S4-03: evaluate's denominator, when --audit-dir
    is given, is the full audited row set -- not whatever the evidence
    file's tickets array happens to contain."""

    def test_load_audited_population_reads_row_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = _write_audit_dir(Path(tmp), rows=[
                {"row_id": "abc:0", "code": "C1", "num_legs": 1, "num_selections": 1},
            ])
            rows = _load_audited_population(audit_dir)
            self.assertEqual(rows["abc:0"]["code"], "C1")

    def test_load_audited_population_rejects_tampered_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = _write_audit_dir(Path(tmp), rows=[{"row_id": "abc:0"}])
            (audit_dir / "run-1.json").write_text(
                json.dumps({"run_id": "run-1", "rows": [{"row_id": "tampered"}]}), encoding="utf-8")
            with self.assertRaises(EvaluationInputError):
                _load_audited_population(audit_dir)

    def test_unknown_row_id_in_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = _write_audit_dir(Path(tmp), rows=[{"row_id": "abc:0"}])
            audit_rows = _load_audited_population(audit_dir)
            evidence = {"fixtures": [], "tickets": [
                dict(_ticket_payload([]), row_id="not-in-audit")]}
            with self.assertRaises(EvaluationInputError):
                evaluate_run(evidence, audit_rows=audit_rows)

    def test_duplicate_row_id_across_tickets_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = _write_audit_dir(Path(tmp), rows=[{"row_id": "abc:0"}])
            audit_rows = _load_audited_population(audit_dir)
            evidence = {"fixtures": [], "tickets": [
                dict(_ticket_payload([], ticket_id="t1"), row_id="abc:0"),
                dict(_ticket_payload([], ticket_id="t2"), row_id="abc:0"),
            ]}
            with self.assertRaises(EvaluationInputError):
                evaluate_run(evidence, audit_rows=audit_rows)

    def test_audited_row_missing_from_evidence_becomes_explicit_unresolved_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = _write_audit_dir(Path(tmp), rows=[
                {"row_id": "abc:0"}, {"row_id": "abc:1"},
            ])
            audit_rows = _load_audited_population(audit_dir)
            evidence = {"fixtures": [], "tickets": [
                dict(_ticket_payload([], ticket_id="t1"), row_id="abc:0")]}
            report = evaluate_run(evidence, audit_rows=audit_rows)
            self.assertEqual(report["counts"]["total"], 2)
            missing = next(t for t in report["tickets"] if t["row_id"] == "abc:1")
            self.assertEqual(missing["status"], "unresolved")
            self.assertEqual(missing["reason"], "not_supplied_in_evidence")

    def test_evaluate_command_with_audit_dir_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audit_dir = _write_audit_dir(tmp_path, rows=[{"row_id": "abc:0"}])
            evidence = {
                "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
                "aliases": _STANDARD_ALIASES,
                "tickets": [dict(_ticket_payload([_leg_payload()]), row_id="abc:0")],
            }
            input_path = tmp_path / "evidence.json"
            input_path.write_text(json.dumps(evidence), encoding="utf-8")
            output_path = tmp_path / "out"
            exit_code = main(["evaluate", "--input", str(input_path), "--audit-dir", str(audit_dir),
                              "--output", str(output_path)])
            self.assertEqual(exit_code, 0)
            report = json.loads((output_path / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["tickets"][0]["row_id"], "abc:0")
            self.assertIn("population_source", report)
            preserved_evidence = json.loads((output_path / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(preserved_evidence, evidence)
            manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("evidence.json", manifest["files"])


class EvaluateCommandCliTests(unittest.TestCase):
    """End-to-end through main(["evaluate", ...]), with real temp files --
    the actual offline-replay path, not just the pure function."""

    def test_evaluate_writes_report_and_manifest(self):
        evidence = {
            "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
            "aliases": _STANDARD_ALIASES,
            "tickets": [_ticket_payload([_leg_payload()])],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "evidence.json"
            input_path.write_text(json.dumps(evidence), encoding="utf-8")
            output_path = tmp_path / "out"
            exit_code = main(["evaluate", "--input", str(input_path), "--output", str(output_path)])
            self.assertEqual(exit_code, 0)
            report = json.loads((output_path / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["tickets"][0]["status"], "unresolved")
            manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("report.json", manifest["files"])
            self.assertIn("evidence.json", manifest["files"])
            preserved_evidence = json.loads((output_path / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(preserved_evidence, evidence)

    def test_evaluate_refuses_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "evidence.json"
            input_path.write_text(json.dumps({"fixtures": [], "tickets": []}), encoding="utf-8")
            output_path = tmp_path / "out"
            output_path.mkdir()
            exit_code = main(["evaluate", "--input", str(input_path), "--output", str(output_path)])
            self.assertEqual(exit_code, 2)

    def test_evaluate_rejects_malformed_json_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "evidence.json"
            input_path.write_text("{not valid json", encoding="utf-8")
            output_path = tmp_path / "out"
            exit_code = main(["evaluate", "--input", str(input_path), "--output", str(output_path)])
            self.assertEqual(exit_code, 2)
            self.assertFalse(output_path.exists())

    def test_evaluate_rejects_missing_input_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            exit_code = main(["evaluate", "--input", str(tmp_path / "missing.json"),
                              "--output", str(tmp_path / "out")])
            self.assertEqual(exit_code, 2)


class AuditSubcommandStillWorksTests(unittest.TestCase):
    """The audit subcommand must remain reachable after the argparse
    subparser conversion -- same flags, just behind `audit` now."""

    def test_audit_missing_run_id_is_argparse_error(self):
        with self.assertRaises(SystemExit):
            main(["audit", "--output", "/nonexistent-output-dir"])

    def test_no_subcommand_is_argparse_error(self):
        with self.assertRaises(SystemExit):
            main([])


if __name__ == "__main__":
    unittest.main()
