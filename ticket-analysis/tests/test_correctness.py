import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from portfolio.analysis import analyze, changes, digest, normalize_ticket, semantic_hash
from portfolio.contracts import identity


def native(event="opaque-event-alpha", market="18", spec="total=2.5", outcome="12", price=2):
    return {"event_id": 0, "native_event_id": event, "market_id": market,
            "specifier": spec, "outcome_id": outcome, "event": "A-B", "market": "Over/Under",
            "pick": "Over", "odds": price}


def ticket(code="CODE", legs=None, **extra):
    legs = [native()] if legs is None else legs
    return {"provider": "sportybet", "code": code, "num_legs": len(legs), "selections": legs, **extra}


class CorrectnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def run_data(self, data, name="sample", policy=None):
        path = self.root / (name + ".json")
        path.write_text(json.dumps({"provider": "sportybet", "qualifying": data}))
        return analyze([path], policy or {})

    def test_opaque_native_id_recovers_integer_zero(self):
        result = self.run_data([ticket("A"), ticket("B")])
        self.assertEqual(result["combined"]["category_B"], ["sportybet:A", "sportybet:B"])
        self.assertEqual(result["coverage"]["identity_modes_classifiable_legs"]["structured_native"], 2)

    def test_native_specifier_and_provider_prevent_false_merges(self):
        result = self.run_data([ticket("A"), ticket("B", [native(spec="total=3.5")]),
                                ticket("C", provider="bet9ja")])
        self.assertEqual(len(result["combined"]["category_A"]), 3)

    def test_labels_do_not_change_native_identity_but_legacy_is_separate(self):
        a = native(event="123")
        b = {**a, "market": "Translated", "pick": "Translated"}
        c = {"event_id": "123", "market": "Over/Under", "pick": "Over", "odds": 2}
        result = self.run_data([ticket("A", [a]), ticket("B", [b]), ticket("C", [c])])
        self.assertEqual(result["combined"]["category_A"], ["sportybet:C"])
        self.assertEqual(result["combined"]["category_B"], ["sportybet:A", "sportybet:B"])

    def test_missing_specifier_not_assumed_empty(self):
        leg = native()
        del leg["specifier"]
        self.assertEqual(identity(leg, "sportybet")[2], "unresolved")
        leg["specifier"] = ""
        self.assertEqual(identity(leg, "sportybet")[2], "structured_native")

    def test_odds_separated_never_promoted_by_agreement_or_claim(self):
        raw = ticket(total_odds=4, site_displayed_odds=3,
                     payout_semantics={"status": "verified", "multiplier": 99}, bet_type="multiple")
        normalized, errors = normalize_ticket(raw, None)
        odds = normalized["odds"]
        self.assertFalse(errors)
        self.assertEqual(odds["site_displayed_odds"], 3)
        self.assertEqual(odds["parsed_leg_product"], 2)
        self.assertEqual(odds["payout_semantics"]["status"], "unknown")
        self.assertTrue(odds["site_product_discrepancy"])
        bet9ja, _ = normalize_ticket(ticket(provider="bet9ja", total_odds=9), None)
        self.assertIsNone(bet9ja["odds"]["site_displayed_odds"])

    def test_profiles_enforce_basis_and_structure(self):
        rows = [ticket("A", total_odds=10, bet_type="system"), ticket("B", total_odds=10, bet_type="multiple")]
        with self.assertRaisesRegex(ValueError, "odds_basis"):
            self.run_data(rows, policy={"profiles": {"p": {"max_total_odds": 5}}})
        policy = {"profiles": {"p": {"odds_basis": "parsed_leg_product", "max_total_odds": 5,
                                      "ticket_types": ["declared_accumulator"]}}}
        self.assertEqual(self.run_data(rows, policy=policy)["combined"]["category_A"], ["sportybet:B"])
        policy["profiles"]["p"]["odds_basis"] = "site_displayed_odds"
        self.assertEqual(self.run_data(rows, policy=policy)["combined"]["candidate_count"], 0)
        policy["profiles"]["p"]["odds_basis"] = "verified_payout"
        self.assertEqual(self.run_data(rows, policy=policy)["combined"]["candidate_count"], 0)

    def test_confirmed_vs_unverified_leg_disappearance(self):
        before = ticket(legs=[native(), native(event="second")], retrieval_completeness="complete",
                        observed_at="2026-09-19T12:00:00+00:00")
        after = ticket(retrieval_completeness="complete", observed_at="2026-09-20T12:00:00+00:00")
        a = self.run_data([before], "before")
        b = self.run_data([after], "after")
        row = changes(a, b)["selection_changes"]["tickets"][0]
        self.assertEqual(row["status"], "confirmed_disappearance_from_complete_response")
        self.assertEqual(len(row["removed"]), 1)
        after["retrieval_completeness"] = "unknown"
        b = self.run_data([after], "after")
        self.assertIn("unverified", changes(a, b)["selection_changes"]["tickets"][0]["status"])

    def test_missing_failed_retrieval_not_confirmed_disappearance(self):
        a = self.run_data([ticket(retrieval_completeness="complete")], "a")
        b = self.run_data([ticket(legs=[], ok=False, retrieval_completeness="incomplete")], "b")
        self.assertEqual(changes(a, b)["selection_changes"]["tickets"][0]["status"], "identity_format_change_or_unresolved")

    def test_format_change_not_removal_and_price_changes_detected(self):
        legacy = {"event_id": "123", "market": "Over/Under", "pick": "Over", "odds": 2}
        a = self.run_data([ticket(legs=[legacy])], "a")
        b = self.run_data([ticket(legs=[native(event="123")])], "b")
        self.assertEqual(changes(a, b)["selection_changes"]["tickets"][0]["status"], "identity_format_change_or_unresolved")
        c = self.run_data([ticket(legs=[native(event="123", price=3)])], "c")
        self.assertEqual(len(changes(b, c)["selection_changes"]["tickets"][0]["changed"]), 1)

    def test_semantic_hash_survives_relocation_but_sensitive_to_selection(self):
        a = self.run_data([ticket()], "source")
        folder = self.root / "relocated"
        folder.mkdir()
        shutil.copyfile(self.root / "source.json", folder / "copy.json")
        b = analyze([folder / "copy.json"], {})
        self.assertEqual(semantic_hash(a), semantic_hash(b))
        self.assertNotEqual(digest(a["sources"]), digest(b["sources"]))
        c = copy.deepcopy(b)
        c["snapshots"][0]["legs"][0]["key"][-1] = "different"
        self.assertNotEqual(semantic_hash(b), semantic_hash(c))

    def test_coverage_reports_zero_id_and_counts(self):
        broken = {"event_id": 0, "market": "1X2", "pick": "Home", "odds": 2}
        report = self.run_data([ticket("A"), ticket("B", [broken])])
        self.assertEqual(report["coverage"]["classifiable_ticket_fraction"], .5)
        self.assertEqual(report["coverage"]["identity_failure_tickets"]["zero_event_id_without_complete_native_identity"], 1)
