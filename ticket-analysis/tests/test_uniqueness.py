"""Phase 1 leg-level uniqueness on the SHARED eligible pool.

Two layers of tests:
  * pure distinctness logic on normalized tickets (identity == contracts.identity);
  * INGESTION-PARITY integration tests that go through analyze_extract (Stage 2) and
    build_pool / decorrelate_extract (Stage 3) from a real extract file, so the odds
    band, provider-ownership, and duplicate/conflict handling are exercised, not a
    hand-built _norm_legs helper.
"""
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portfolio.analysis import canonical, normalize_ticket
from portfolio.contracts import identity
from portfolio.decorrelation import ExtractError, build_pool, decorrelate_extract
from portfolio.uniqueness import _bare_code, analyze, analyze_extract, leg_key_set
from portfolio.distinct_page import render_html


# ---------- helpers ----------

def sel(ev, market="1X2", pick="1", odds=2.0):
    return {"native_event_id": ev, "market_id": market, "outcome_id": pick, "specifier": "",
            "event": f"Game {ev}", "league": "L", "market": market, "pick": pick, "odds": odds}


def coupon(code, sels, provider="bet9ja"):
    return {"provider": provider, "code": code, "num_legs": len(sels), "selections": sels}


def key(s, provider="bet9ja"):
    return canonical(identity(s, provider)[0])


def norm(coupons, provider="bet9ja"):
    """Normalized tickets for the pure-logic tests (mirrors build_pool's normalize step)."""
    out = []
    for raw in coupons:
        ticket, issues = normalize_ticket(raw, raw.get("provider") or provider)
        assert ticket is not None and not issues, issues
        ticket["raw"] = raw
        out.append(ticket)
    return out


def write_extract(tmp, qualifying, provider="bet9ja"):
    d = Path(tmp) / "extracts"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "scan.json"
    path.write_text(json.dumps({"provider": provider, "qualifying": qualifying}))
    return str(path)


# ---------- pure distinctness logic ----------

class DistinctnessLogicTests(unittest.TestCase):
    def test_leg_identity_is_contracts_identity(self):
        pool = norm([coupon("A", [sel("1"), sel("2", pick="2")])])
        self.assertEqual(leg_key_set(pool[0]), {key(sel("1")), key(sel("2", pick="2"))})

    def test_same_game_different_option_is_distinct(self):
        r = analyze(norm([coupon("A", [sel("ENG", pick="1"), sel("X")]),
                          coupon("B", [sel("ENG", pick="2"), sel("Y")])]))
        self.assertEqual(r["distinct_codes"], 2)
        self.assertEqual(r["overlapping_codes"], 0)

    def test_shared_game_and_option_overlaps_both(self):
        r = analyze(norm([coupon("A", [sel("ENG", pick="1"), sel("X")]),
                          coupon("B", [sel("ENG", pick="1"), sel("Y")])]))
        self.assertEqual(r["distinct_codes"], 0)
        self.assertEqual(r["overlapping_codes"], 2)
        self.assertEqual(r["shared_legs"], 1)

    def test_distinct_survives_alongside_overlap(self):
        r = analyze(norm([coupon("A", [sel("1"), sel("2")]),
                          coupon("B", [sel("2"), sel("3")]),
                          coupon("C", [sel("9"), sel("8")])]))
        self.assertEqual({_bare_code(c) for c in r["distinct"]}, {"C"})


# ---------- ingestion parity: Stage 2 == Stage 3 on ONE builder ----------

class IngestionParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def eligible_codes_stage2(self, path, provider, **policy):
        r = analyze_extract(path, provider, **policy)
        return {_bare_code(t) for t in r["distinct"] + r["overlapping"]}, r

    def eligible_codes_stage3(self, path, provider, **policy):
        pool = build_pool(path, provider, **policy)
        return {_bare_code(t) for t in pool["tickets"]}, pool

    def test_odds_band_applied_in_stage2(self):
        # Two OVERLAPPING codes: P priced 4 (in band), Q priced 100 (out of band).
        path = write_extract(self.tmp.name, [
            coupon("P", [sel("1", odds=2.0), sel("2", odds=2.0)]),      # product 4
            coupon("Q", [sel("2", odds=10.0), sel("9", odds=10.0)]),    # product 100, shares leg 2
        ])
        policy = dict(odds_basis="parsed_leg_product", min_odds=2, max_odds=10)
        s2, r = self.eligible_codes_stage2(path, "bet9ja", **policy)
        s3, _ = self.eligible_codes_stage3(path, "bet9ja", **policy)
        self.assertEqual(s2, s3, "Stage 2 and Stage 3 must build the same banded pool")
        self.assertEqual(s2, {"P"})                 # Q excluded by band, not treated as overlap
        self.assertEqual(r["distinct_codes"], 1)    # P alone is distinct within the eligible pool

    def test_foreign_provider_excluded_in_stage2(self):
        # sportybet run extract that also contains a bet9ja coupon.
        path = write_extract(self.tmp.name, [
            coupon("SPORTY1", [sel("1"), sel("2")], provider="sportybet"),
            coupon("FOREIGN", [sel("3"), sel("4")], provider="bet9ja"),
        ], provider="sportybet")
        policy = dict(odds_basis="parsed_leg_product", min_odds=1)
        s2, _ = self.eligible_codes_stage2(path, "sportybet", **policy)
        s3, _ = self.eligible_codes_stage3(path, "sportybet", **policy)
        self.assertEqual(s2, s3)
        self.assertEqual(s2, {"SPORTY1"})           # bet9ja coupon owned by neither stage

    def test_conflicting_duplicate_raises_in_both_stages(self):
        # Same code observed twice with DIFFERENT content.
        path = write_extract(self.tmp.name, [
            coupon("DUP", [sel("1"), sel("2")]),
            coupon("DUP", [sel("1"), sel("3")]),    # conflicting observation
        ])
        policy = dict(odds_basis="parsed_leg_product", min_odds=1)
        with self.assertRaises(ExtractError):
            analyze_extract(path, "bet9ja", **policy)
        with self.assertRaises(ExtractError):
            build_pool(path, "bet9ja", **policy)

    def test_equality_criterion_distinct_is_subset_of_selected(self):
        # A/B overlap on game 2; C disjoint. All in band.
        path = write_extract(self.tmp.name, [
            coupon("A", [sel("1"), sel("2")]),
            coupon("B", [sel("2"), sel("3")]),
            coupon("C", [sel("8"), sel("9")]),
        ])
        policy = dict(odds_basis="parsed_leg_product", min_odds=1)
        _, r = self.eligible_codes_stage2(path, "bet9ja", **policy)
        distinct = {_bare_code(t) for t in r["distinct"]}
        self.assertEqual(distinct, {"C"})           # Stage 2: distinct against the FULL pool

        result = decorrelate_extract(path, "bet9ja", {"max_exposure": 1}, **policy)
        selected = set(result["selection"]["selected_codes"])
        # Strict inclusion holds for this fixture, with no target limit.
        self.assertTrue(distinct < selected, f"{distinct} !< {selected}")
        self.assertEqual(len(selected), 2)          # C + one of {A, B}
        self.assertIn("C", selected)

    def test_all_distinct_can_equal_selected_but_target_can_omit_distinct(self):
        path = write_extract(self.tmp.name, [coupon("A", [sel("1")]),
                                             coupon("B", [sel("2")])])
        policy = dict(odds_basis="parsed_leg_product", min_odds=1)
        _, report = self.eligible_codes_stage2(path, "bet9ja", **policy)
        distinct = {_bare_code(t) for t in report["distinct"]}
        full = decorrelate_extract(path, "bet9ja", {}, **policy)
        self.assertEqual(distinct, set(full["selection"]["selected_codes"]))
        limited = decorrelate_extract(path, "bet9ja", {"target": 1}, **policy)
        self.assertEqual(len(limited["selection"]["selected_codes"]), 1)
        self.assertFalse(distinct <= set(limited["selection"]["selected_codes"]))

    def test_page_displays_and_sorts_by_chosen_basis_without_fallback(self):
        a = coupon("A", [sel("1", odds=4)])
        b = coupon("B", [sel("2", odds=3)])
        a["site_displayed_odds"], b["site_displayed_odds"] = 9, 11
        path = write_extract(self.tmp.name, [a, b])
        report = analyze_extract(path, "bet9ja", odds_basis="site_displayed_odds",
                                 min_odds=8.5, max_odds=12)
        page = render_html("bet9ja", report)
        self.assertIn("Site displayed odds: 8.5–12", page)
        self.assertIn('<small>Site displayed odds</small><span class="n">9.00</span>', page)
        self.assertIn('<small>Site displayed odds</small><span class="n">11.00</span>', page)
        self.assertLess(page.index('data-code="A"'), page.index('data-code="B"'))
        report["distinct"][0]["odds"]["site_displayed_odds"] = None
        page = render_html("bet9ja", report)
        self.assertIn('<small>Site displayed odds</small><span class="n">—</span>', page)
        self.assertLess(page.index('data-code="B"'), page.index('data-code="A"'))

    def test_both_clis_require_odds_basis_before_loading_extract(self):
        from portfolio import distinct_page, uniqueness
        for module in (distinct_page, uniqueness):
            with self.subTest(module=module.__name__), patch("sys.argv", [
                    module.__name__, "--extract", "missing.json", "--provider", "bet9ja",
                    "--min-odds", "2"]), patch("sys.stderr", new_callable=io.StringIO) as stderr:
                with self.assertRaises(SystemExit) as exc:
                    module.main()
                self.assertEqual(exc.exception.code, 2)
                self.assertIn("required: --odds-basis", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
