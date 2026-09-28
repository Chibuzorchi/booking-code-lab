"""F4 odds-basis contract: Stage-1 harvesting and Stage-2/3 eligibility must
use the SAME basis (parsed_leg_product) and the SAME inclusive band.

Provider qualification runs in real subprocesses (offline, fake clients over
the real mutate/scan/save paths); the ACTUAL scan result is saved by the real
save(), its policy translated by harvest_odds_band(), and the resulting
extract feeds the real build_pool / analyze_extract paths. No provider
traffic.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from portfolio.analysis import normalize_ticket
from portfolio.decorrelation import ExtractError, build_pool, harvest_odds_band
from portfolio.reporting import scan_summary
from portfolio.uniqueness import analyze_extract

ROOT = Path(__file__).resolve().parents[2]

PROVIDER_SCAN = r'''
import json, logging, sys, tempfile
from pathlib import Path
from engine.settings import BaseSettings
from engine.scanner import CouponScanner
from engine.coupon import Coupon, Selection

PROVIDER = sys.argv[1]

quiet = logging.getLogger("f4-harness")
quiet.addHandler(logging.NullHandler())
quiet.setLevel(logging.CRITICAL)
quiet.success = lambda *a, **k: None

_seq = 0


def mk(code, leg_odds, site=None):
    global _seq
    _seq += 1
    return Coupon(code=code, ok=True,
                  selections=[Selection(key=f"{code}-k{i}",
                                        event_id=_seq * 10 + i + 1,
                                        event_name=f"G{_seq}-{i}", league="L",
                                        market="1X2", sign="H", odds=odds)
                              for i, odds in enumerate(leg_odds)],
                  site_total_odds=site, provider=PROVIDER)


# Candidate suffixes after the seed "CASE00A" (charset order B..J).
CAPPED = [
    ("B", mk("B1", [10.0, 10.0], site=20000.0)),    # site in band, product out
    ("C", mk("C1", [80.0, 80.0], site=100.0)),      # product in band, site out
    ("D", mk("D1", [50.0, 100.0])),                 # exact lower boundary
    ("E", mk("E1", [1000.0, 350.0])),               # exact upper boundary
    ("F", mk("F1", [1000.0, 400.0])),               # above the cap
    ("G", mk("G1", [2.0, 0.0])),                    # missing leg price
    ("H", mk("H1", [2.0, float("inf")])),           # non-finite leg price
    ("I", mk("I1", [1e308, 1e308])),                # overflow
]
UNCAPPED = ("J", mk("J1", [2000.0, 2000.0]))        # only qualifies without a cap


class Fake:
    def __init__(self, coupons):
        self.coupons = coupons

    def decode(self, code):
        return self.coupons.get(
            code, Coupon(code=code, ok=False, error="no", provider=PROVIDER))


def run_scan(coupons, min_odds, max_odds, tmp):
    settings = BaseSettings(provider=PROVIDER, results_dir=Path(tmp),
                            min_total_odds=min_odds, max_total_odds=max_odds,
                            max_codes_to_try=10, max_qualifying=0,
                            charset="ABCDEFGHIJ", max_mutation_depth=1,
                            request_delay_ms=0, request_workers=1, top_n=0)
    scanner = CouponScanner(Fake(coupons), settings, logger=quiet)
    result = scanner.scan("CASE00A")
    paths = scanner.save(result)
    return result, paths


out = {"provider": PROVIDER, "as_dicts": {}}
for suffix, coupon in CAPPED + [UNCAPPED]:
    out["as_dicts"][suffix] = coupon.as_dict()

with tempfile.TemporaryDirectory() as tmp:
    capped_result, capped_paths = run_scan(
        {f"CASE00{s}": c for s, c in CAPPED}, 5000.0, 350000.0, tmp)
    out["capped"] = {
        "extract": json.loads(capped_paths["extract"].read_text()),
        "codes": capped_paths["codes"].read_text().splitlines(),
        "qualifying_codes": [c.code for c in capped_result.qualifying],
        "unpriced": capped_result.unpriced,
        "found_ok": capped_result.found_ok,
        "tried": capped_result.tried,
    }

with tempfile.TemporaryDirectory() as tmp:
    by_suffix = dict(CAPPED)
    uncapped_result, uncapped_paths = run_scan(
        {"CASE00F": by_suffix["F"], "CASE00J": UNCAPPED[1]}, 5000.0, 0.0, tmp)
    out["uncapped"] = {
        "extract": json.loads(uncapped_paths["extract"].read_text()),
        "codes": uncapped_paths["codes"].read_text().splitlines(),
        "qualifying_codes": [c.code for c in uncapped_result.qualifying],
        "unpriced": uncapped_result.unpriced,
        "found_ok": uncapped_result.found_ok,
        "tried": uncapped_result.tried,
    }

print(json.dumps(out))
'''

CONTEXT_SCAN = r'''
import json, logging, sys, tempfile
from pathlib import Path
from engine.settings import BaseSettings
from engine.scanner import CouponScanner
from engine.coupon import Coupon
from infra.parser import parse_share

quiet = logging.getLogger("f4-context")
quiet.addHandler(logging.NullHandler())
quiet.setLevel(logging.CRITICAL)
quiet.success = lambda *a, **k: None

payload = {"bizCode": 10000, "data": {"shareCode": "CTX0", "betType": "multiple",
    "ticket": {"selections": [{"eventId": "sr:match:1", "marketId": "18",
                               "outcomeId": "12", "specifier": "total=2.5"},
                              {"eventId": "sr:match:2", "marketId": "18",
                               "outcomeId": "12", "specifier": "total=2.5"}],
               "displayTotalOdds": float("inf"),
               "bets": [{"selectedSystems": [6], "stake": 1.0}]},
    "outcomes": [{"eventId": "sr:match:1", "estimateStartTime": 4102444800000,
                  "markets": [{"id": "18", "specifier": "total=2.5", "desc": "Over/Under",
                               "outcomes": [{"id": "12", "odds": "80.0", "desc": "Over 2.5"}]}]},
                 {"eventId": "sr:match:2", "estimateStartTime": 4102444800000,
                  "markets": [{"id": "18", "specifier": "total=2.5", "desc": "Over/Under",
                               "outcomes": [{"id": "12", "odds": "80.0", "desc": "Over 2.5"}]}]}]}}
coupon = parse_share("CTX0", payload)
assert coupon.ok
assert coupon.parsed_leg_product == 6400.0
assert not __import__("math").isfinite(coupon.site_total_odds)


class Fake:
    def decode(self, code):
        if code == "CASE00B":
            return coupon
        return Coupon(code=code, ok=False, error="no", provider="sportybet")


with tempfile.TemporaryDirectory() as tmp:
    settings = BaseSettings(provider="sportybet", results_dir=Path(tmp),
                            min_total_odds=5000.0, max_total_odds=350000.0,
                            max_codes_to_try=2, max_qualifying=0,
                            charset="B", max_mutation_depth=1,
                            request_delay_ms=0, request_workers=1, top_n=0)
    scanner = CouponScanner(Fake(), settings, logger=quiet)
    result = scanner.scan("CASE00A")
    paths = scanner.save(result)
    data = json.loads(paths["extract"].read_text())  # strict: Infinity would raise
    print(json.dumps({
        "extract": data,
        "qualifying": [c.code for c in result.qualifying],
        "context_display_total": data["qualifying"][0]["odds_context"]["displayTotalOdds"],
        "site_displayed_odds": data["qualifying"][0]["site_displayed_odds"],
        "parsed_leg_product": data["qualifying"][0]["parsed_leg_product"],
    }))
'''

CASE_CODES = {
    "site_in_band_product_out": "B1",
    "product_in_site_out": "C1",
    "lower_boundary": "D1",
    "upper_boundary": "E1",
    "above_cap": "F1",
    "missing_price": "G1",
    "nonfinite_price": "H1",
    "overflow": "I1",
    "uncapped": "J1",
}


class OddsBasisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for provider in ("bet9ja", "sportybet"):
            run = subprocess.run([sys.executable, "-c", PROVIDER_SCAN, provider],
                                 cwd=ROOT / provider, capture_output=True,
                                 text=True, timeout=120)
            assert run.returncode == 0, run.stderr
            cls.results[provider] = json.loads(run.stdout)

    def write(self, data):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "extract.json"
        path.write_text(json.dumps(data))
        return path

    def test_stage1_qualification_uses_parsed_leg_product(self):
        for provider in ("bet9ja", "sportybet"):
            with self.subTest(provider=provider):
                capped = self.results[provider]["capped"]
                self.assertEqual(set(capped["qualifying_codes"]),
                                 {"C1", "D1", "E1"})
                self.assertEqual(capped["unpriced"], 3)
                self.assertEqual(capped["found_ok"], 8)
                self.assertEqual(capped["tried"], 9)
                uncapped = self.results[provider]["uncapped"]
                self.assertEqual(set(uncapped["qualifying_codes"]), {"F1", "J1"})
                self.assertEqual(uncapped["unpriced"], 0)

    def test_real_scan_save_analysis_continuity(self):
        for provider in ("bet9ja", "sportybet"):
            with self.subTest(provider=provider):
                capped = self.results[provider]["capped"]
                extract = capped["extract"]
                self.assertEqual(extract["odds_basis"], "parsed_leg_product")
                self.assertEqual(extract["unpriced_excluded"], 3)
                self.assertEqual(extract["tried"], 9)
                self.assertEqual({c["code"] for c in extract["qualifying"]},
                                 {"C1", "D1", "E1"})
                policy = harvest_odds_band(extract)
                self.assertEqual(policy, {"odds_basis": "parsed_leg_product",
                                          "min_odds": 5000.0, "max_odds": 350000.0})
                path = self.write(extract)
                pool = build_pool(path, provider, **policy)
                self.assertEqual({t["id"].split(":")[1] for t in pool["tickets"]},
                                 {"C1", "D1", "E1"})
                stage2 = analyze_extract(path, provider, **policy)
                self.assertEqual(stage2["pool_size"], pool["pool_size"])
                self.assertEqual(stage2["pool_size"], 3)
                # Uncapped run: the above-cap coupon qualifies under no ceiling.
                uncapped = self.results[provider]["uncapped"]
                uncapped_policy = harvest_odds_band(uncapped["extract"])
                self.assertIsNone(uncapped_policy["max_odds"])
                path2 = self.write(uncapped["extract"])
                pool2 = build_pool(path2, provider, **uncapped_policy)
                self.assertEqual({t["id"].split(":")[1] for t in pool2["tickets"]},
                                 {"F1", "J1"})
                stage2b = analyze_extract(path2, provider, **uncapped_policy)
                self.assertEqual(stage2b["pool_size"], pool2["pool_size"])

    def test_cross_stage_banding_rules(self):
        for provider in ("bet9ja", "sportybet"):
            with self.subTest(provider=provider):
                full = {"provider": provider,
                        "odds_basis": "parsed_leg_product",
                        "qualifying": [self.results[provider]["as_dicts"][s]
                                       for s in "BCDEFGHIJ"]}
                path = self.write(full)
                pool = build_pool(path, provider, odds_basis="parsed_leg_product",
                                  min_odds=5000, max_odds=350000)
                in_pool = {t["id"].split(":")[1] for t in pool["tickets"]}
                self.assertEqual(in_pool, {"C1", "D1", "E1"})
                by_code = {}
                for row in pool["excluded"]:
                    code = full["qualifying"][row["index"]]["code"]
                    by_code[code] = row["reason"]
                for code in ("B1", "F1", "J1"):
                    self.assertEqual(by_code.get(code), "outside_band", code)
                for code in ("G1", "H1", "I1"):
                    self.assertEqual(by_code.get(code), "missing_odds", code)
                stage2 = analyze_extract(path, provider,
                                         odds_basis="parsed_leg_product",
                                         min_odds=5000, max_odds=350000)
                self.assertEqual(stage2["pool_size"], pool["pool_size"])
                # No ceiling: the above-cap coupon joins the pool.
                uncapped = build_pool(path, provider,
                                      odds_basis="parsed_leg_product",
                                      min_odds=5000, max_odds=None)
                self.assertEqual({t["id"].split(":")[1] for t in uncapped["tickets"]},
                                 {"C1", "D1", "E1", "F1", "J1"})

    def test_harvest_odds_band_translation(self):
        extract = self.results["bet9ja"]["capped"]["extract"]
        band = harvest_odds_band(extract)
        self.assertEqual(band["odds_basis"], "parsed_leg_product")
        self.assertEqual(band["min_odds"], 5000.0)
        self.assertEqual(band["max_odds"], 350000.0)
        for uncapped in (dict(extract, max_total_odds=0),
                         dict(extract, max_total_odds=-1),
                         {k: v for k, v in extract.items() if k != "max_total_odds"},
                         dict(extract, max_total_odds=None),
                         dict(extract, max_total_odds="")):
            self.assertIsNone(harvest_odds_band(uncapped)["max_odds"])
        for bad in ("garbage", True, [], float("nan"), float("inf"), [350000]):
            with self.subTest(max_total_odds=bad):
                with self.assertRaises(ExtractError):
                    harvest_odds_band(dict(extract, max_total_odds=bad))
        with self.assertRaises(ExtractError):
            harvest_odds_band({k: v for k, v in extract.items() if k != "odds_basis"})
        with self.assertRaises(ExtractError):
            harvest_odds_band(dict(extract, max_total_odds=100))
        with self.assertRaises(ExtractError):
            harvest_odds_band(dict(extract, min_total_odds=None))
        with self.assertRaises(ExtractError):
            harvest_odds_band(dict(extract, min_total_odds="garbage"))

    def test_site_odds_preserved_distinct_from_product(self):
        raw = self.results["sportybet"]["as_dicts"]["C"]
        self.assertEqual(raw["site_displayed_odds"], 100.0)
        self.assertEqual(raw["parsed_leg_product"], 6400.0)
        ticket, issues = normalize_ticket(raw, "sportybet")
        self.assertEqual(issues, [])
        self.assertEqual(ticket["odds"]["parsed_leg_product"], 6400.0)
        self.assertEqual(ticket["odds"]["site_displayed_odds"], 100.0)
        self.assertNotEqual(ticket["odds"]["parsed_leg_product"],
                            ticket["odds"]["site_displayed_odds"])

    def test_codes_file_uses_leg_product(self):
        for provider in ("bet9ja", "sportybet"):
            with self.subTest(provider=provider):
                capped = self.results[provider]["capped"]
                by_code = {c["code"]: c["parsed_leg_product"]
                           for c in capped["extract"]["qualifying"]}
                self.assertEqual(len(capped["codes"]), 3)
                for line in capped["codes"]:
                    code, _, value = line.split(" │ ")
                    self.assertAlmostEqual(float(value.replace(",", "")),
                                           by_code[code], places=2)

    def test_report_labels_both_quantities(self):
        run = {"time": "now", "seed": "S", "saved_count": 1, "tried": 1,
               "valid": 1, "id": "x", "rejected": 0,
               "filters": {"min_total_odds": 5000, "max_total_odds": 350000,
                           "min_legs": 0, "max_legs": 0, "top_n_display": 0},
               "tickets": [{"code": "C1", "legs": [{}], "site": 100.0,
                            "product": 6400.0, "legacy": None, "issues": []}]}
        out = scan_summary(run, "bet9ja")
        self.assertIn("Calculated odds = product of the recorded leg prices, "
                      "not verified payout odds.", out)
        self.assertIn("Site odds       = captured provider-displayed odds.", out)
        self.assertIn("100.00", out)
        self.assertIn("6,400.00", out)


class ParserOddsBoundaryTests(unittest.TestCase):
    def run_provider(self, provider, script):
        run = subprocess.run([sys.executable, "-c", script], cwd=ROOT / provider,
                             text=True, capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_bet9ja_malformed_leg_price_is_unpriced_not_invented(self):
        self.run_provider("bet9ja", '''
import math
from infra.parser import parse_coupon
p = {"R": "OK", "D": {"O": {
    "k1": {"E_ID": "1", "E_NAME": "A - B", "GN": "L", "M_NAME": "1X2", "SGN": "1", "V": "abc"},
    "k2": {"E_ID": "2", "E_NAME": "C - D", "GN": "L", "M_NAME": "1X2", "SGN": "1", "V": 2}}}}
c = parse_coupon("CODE", p)
assert c.ok
assert c.parsed_leg_product is None
assert c.site_total_odds is None
''')

    def test_sportybet_malformed_leg_price_is_unpriced_not_invented(self):
        self.run_provider("sportybet", '''
from infra.parser import parse_share
payload = {"bizCode": 10000, "data": {"shareCode": "ABC", "betType": "multiple",
    "ticket": {"selections": [{"eventId": "sr:match:1", "marketId": "18",
                               "outcomeId": "12", "specifier": "total=2.5"}],
               "displayTotalOdds": "9.00"},
    "outcomes": [{"eventId": "sr:match:1", "estimateStartTime": 4102444800000,
                  "markets": [{"id": "18", "specifier": "total=2.5",
                               "desc": "Over/Under",
                               "outcomes": [{"id": "12", "odds": "not-a-number",
                                             "desc": "Over 2.5"}]}]}]}}
c = parse_share("ABC", payload)
assert c.ok
assert c.parsed_leg_product is None
assert c.site_total_odds == 9.0
''')

    def test_nonfinite_odds_context_does_not_corrupt_extract(self):
        run = subprocess.run([sys.executable, "-c", CONTEXT_SCAN],
                             cwd=ROOT / "sportybet", text=True,
                             capture_output=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["qualifying"], ["CTX0"])
        self.assertIsNone(result["context_display_total"])
        self.assertIsNone(result["site_displayed_odds"])
        self.assertEqual(result["parsed_leg_product"], 6400.0)
        path = self.write(result["extract"])
        pool = build_pool(path, "sportybet", odds_basis="parsed_leg_product",
                          min_odds=5000, max_odds=350000)
        self.assertEqual({t["id"] for t in pool["tickets"]}, {"sportybet:CTX0"})
        ticket = pool["tickets"][0]
        self.assertEqual(ticket["odds"]["parsed_leg_product"], 6400.0)
        self.assertIsNone(ticket["odds"]["site_displayed_odds"])

    def write(self, data):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "extract.json"
        path.write_text(json.dumps(data))
        return path


if __name__ == "__main__":
    unittest.main()
