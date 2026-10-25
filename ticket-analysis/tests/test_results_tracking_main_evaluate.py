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

    def test_unsupported_leg_with_unparseable_event_display_stays_unresolved(self):
        # F9_STEP4_REREVIEW.md S4R-06 reproduction: a Tennis leg (unsupported
        # sport) whose event_display isn't a "Team - Team" string must not
        # abort the whole evaluation. The reporting path must not rerun
        # matching for it outside the sport/market gate evaluate_selection()
        # already applied.
        evidence = {
            "fixtures": [],
            "tickets": [_ticket_payload([_leg_payload(
                sport="Tennis", event_display="not a team fixture")])],
        }
        report = evaluate_run(evidence)
        leg = report["tickets"][0]["legs"][0]
        self.assertEqual(leg["status"], "unresolved")
        self.assertEqual(leg["reason"], "unsupported_sport")
        self.assertIsNone(leg["match_tier"])
        self.assertIsNone(leg["matched_fixture"])

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

    def test_null_event_display_raises_evaluation_input_error_not_attribute_error(self):
        # F9_STEP4_REREVIEW.md S4R-05 reproduction: a present-but-null
        # required string field must raise a contextual EvaluationInputError
        # at the parser boundary, not an AttributeError deep inside
        # split_event_display()'s .split() call.
        evidence = {"tickets": [_ticket_payload([_leg_payload(event_display=None)])]}
        with self.assertRaises(EvaluationInputError):
            evaluate_run(evidence)

    def test_null_scalar_fields_are_rejected_across_parsers(self):
        for overrides in (
            {"provider": None}, {"league_display": None}, {"sport": None},
            {"market": None}, {"pick": None}, {"leg_id": None},
        ):
            evidence = {"tickets": [_ticket_payload([_leg_payload(**overrides)])]}
            with self.assertRaises(EvaluationInputError):
                evaluate_run(evidence)

    def test_wrong_type_kickoff_raw_is_rejected(self):
        evidence = {"tickets": [_ticket_payload([_leg_payload(
            kickoff={"raw": 20261024, "precision": "date_only"})])]}
        with self.assertRaises(EvaluationInputError):
            evaluate_run(evidence)

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


def _selection_for_leg(leg_payload):
    """The original-extract 'selections' entry that would have produced the
    given evidence leg payload, for building a fake preserved extract whose
    content matches (or, in an S4R-03 test, deliberately does not match) a
    row_id's supplied evidence."""
    return {"event": leg_payload["event_display"], "league": leg_payload["league_display"],
            "market": leg_payload["market"], "pick": leg_payload["pick"],
            "sport": leg_payload["sport"], "kickoff": leg_payload["kickoff"]["raw"]}


def _write_audit_dir(tmp_path: Path, run_id="run-1", rows=(), qualifying=None,
                      provider="sportybet", dir_name="audit") -> Path:
    """A stand-in for a real `audit` command's output directory, including
    the preserved extract that _load_audited_population() now reads to bind
    row_ids to their original ticket content (F9_STEP4_REREVIEW.md S4R-03).
    rows without an explicit "index" are assigned one by position, matching
    qualifying by the same position when qualifying is given; when
    qualifying is omitted, each row gets an empty-selections placeholder
    ticket (sufficient for tests that only care about row-id bookkeeping,
    not content binding)."""
    audit_dir = tmp_path / dir_name
    audit_dir.mkdir()
    extracts_dir = audit_dir / "extracts"
    extracts_dir.mkdir()
    rows = [dict(row) for row in rows]
    for i, row in enumerate(rows):
        row.setdefault("index", i)
    if qualifying is None:
        qualifying = [{"code": row.get("code"), "selections": []} for row in rows]
    extract_bytes = json.dumps(
        {"provider": provider, "odds_basis": "parsed_leg_product", "qualifying": qualifying}
    ).encode("utf-8")
    extract_sha256 = hashlib.sha256(extract_bytes).hexdigest()
    extract_path = extracts_dir / f"{run_id}.extract.json"
    extract_path.write_bytes(extract_bytes)
    run_report = {"run_id": run_id, "rows": rows, "provider": provider,
                  "extract_sha256": extract_sha256,
                  "preserved_extract_path": str(extract_path.relative_to(audit_dir))}
    (audit_dir / f"{run_id}.json").write_text(json.dumps(run_report), encoding="utf-8")
    summary = {"runs": [{"run_id": run_id}]}
    (audit_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    manifest = {"files": {
        str(p.relative_to(audit_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(audit_dir.rglob("*")) if p.is_file()
    }}
    (audit_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return audit_dir


class AuditedPopulationAnchoringTests(unittest.TestCase):
    """F9_STEP4_REVIEW.md S4-03: evaluate's denominator, when --audit-dir
    is given, is the full audited row set -- not whatever the evidence
    file's tickets array happens to contain."""

    def test_row_id_attached_to_unrelated_ticket_content_is_rejected(self):
        # F9_STEP4_REREVIEW.md S4R-03 reproduction: a valid audited Bet9ja
        # row_id, originally retained from an ORIGINAL ticket with an
        # Alpha-Beta away selection, is attached in evidence to an unrelated
        # SportyBet-provider ticket (renamed REPLACEMENT) carrying a wholly
        # different Arsenal-Everton home selection. Membership of the
        # row_id alone must not be accepted -- content must bind too.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            original_selection = {
                "event": "Alpha - Beta", "league": "Some League", "market": "1X2",
                "pick": "Away", "sport": "Football", "kickoff": "2026-10-20",
            }
            audit_dir = _write_audit_dir(
                tmp_path, rows=[{"row_id": "abc:0", "code": "ORIGINAL"}],
                qualifying=[{"code": "ORIGINAL", "selections": [original_selection]}],
                provider="bet9ja")
            audit_rows = _load_audited_population(audit_dir)
            self.assertEqual(audit_rows["abc:0"]["provider"], "bet9ja")

            replacement_leg = _leg_payload(provider="sportybet")  # Arsenal - Everton home
            evidence = {
                "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
                "aliases": _STANDARD_ALIASES,
                "tickets": [dict(_ticket_payload([replacement_leg], provider="sportybet",
                                                  ticket_id="REPLACEMENT"), row_id="abc:0")],
            }
            with self.assertRaises(EvaluationInputError):
                evaluate_run(evidence, audit_rows=audit_rows)

    def test_row_id_attached_to_matching_original_content_is_accepted(self):
        leg = _leg_payload()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audit_dir = _write_audit_dir(
                tmp_path, rows=[{"row_id": "abc:0", "code": "C1"}],
                qualifying=[{"code": "C1", "selections": [_selection_for_leg(leg)]}])
            audit_rows = _load_audited_population(audit_dir)
            evidence = {
                "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
                "aliases": _STANDARD_ALIASES,
                "tickets": [dict(_ticket_payload([leg]), row_id="abc:0")],
            }
            report = evaluate_run(evidence, audit_rows=audit_rows)
            self.assertEqual(report["tickets"][0]["row_id"], "abc:0")

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
            leg = _leg_payload()
            audit_dir = _write_audit_dir(tmp_path, rows=[{"row_id": "abc:0"}],
                                          qualifying=[{"code": "C1", "selections": [_selection_for_leg(leg)]}])
            evidence = {
                "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
                "aliases": _STANDARD_ALIASES,
                "tickets": [dict(_ticket_payload([leg]), row_id="abc:0")],
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

    def test_replay_reproduces_same_n_after_both_source_directories_are_gone(self):
        # F9_STEP4_REREVIEW.md S4R-04 reproduction: a real two-row audit,
        # evidence for only one row, evaluated with --audit-dir (N=2, the
        # second row unresolved). Both the original evidence file and the
        # audit directory are then deleted. Replaying from the archived
        # evaluation output alone must still produce N=2 and the same row
        # decisions -- the audit population snapshot, not just its path,
        # must have been archived with the evaluation.
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            leg = _leg_payload()
            audit_dir = _write_audit_dir(
                tmp_path, rows=[{"row_id": "abc:0"}, {"row_id": "abc:1"}],
                qualifying=[{"code": "C1", "selections": [_selection_for_leg(leg)]},
                            {"code": "C2", "selections": []}])
            evidence = {
                "fixtures": [_fixture_payload(score={"home": 2, "away": 1})],
                "aliases": _STANDARD_ALIASES,
                "tickets": [dict(_ticket_payload([leg]), row_id="abc:0")],
            }
            input_path = tmp_path / "evidence.json"
            input_path.write_text(json.dumps(evidence), encoding="utf-8")
            first_output = tmp_path / "out1"
            exit_code = main(["evaluate", "--input", str(input_path), "--audit-dir", str(audit_dir),
                              "--output", str(first_output)])
            self.assertEqual(exit_code, 0)
            original_report = json.loads((first_output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(original_report["counts"]["total"], 2)
            self.assertTrue((first_output / "audit_population.json").exists())

            shutil.rmtree(audit_dir)
            input_path.unlink()

            second_output = tmp_path / "out2"
            exit_code = main(["evaluate", "--replay", str(first_output), "--output", str(second_output)])
            self.assertEqual(exit_code, 0)
            replayed_report = json.loads((second_output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(replayed_report["counts"], original_report["counts"])
            self.assertEqual(
                {t["row_id"]: t["status"] for t in replayed_report["tickets"]},
                {t["row_id"]: t["status"] for t in original_report["tickets"]})
            missing = next(t for t in replayed_report["tickets"] if t["row_id"] == "abc:1")
            self.assertEqual(missing["reason"], "not_supplied_in_evidence")

    def test_replay_and_input_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "evidence.json"
            input_path.write_text(json.dumps({"fixtures": [], "tickets": []}), encoding="utf-8")
            exit_code = main(["evaluate", "--input", str(input_path), "--replay", str(tmp_path),
                              "--output", str(tmp_path / "out")])
            self.assertEqual(exit_code, 2)


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
