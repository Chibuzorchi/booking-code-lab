"""Isolate provider imports until their generic package names are migrated."""
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPORTY_FIXTURE = '''
import copy
from infra.parser import parse_share
event = {"eventId": "sr:match:123", "homeTeamName": "A", "awayTeamName": "B",
         "estimateStartTime": 2000000000000,
         "markets": [
             {"id": "18", "specifier": "total=2.5", "desc": "Over/Under",
              "outcomes": [{"id": "12", "desc": "Over 2.5", "odds": "1.5"}]},
             {"id": "18", "specifier": "total=3.5", "desc": "Over/Under",
              "outcomes": [{"id": "12", "desc": "Over 3.5", "odds": "2.5"}]}]}
refs = [{"eventId": "sr:match:123", "marketId": "18", "outcomeId": "12", "specifier": "total=2.5"},
        {"eventId": "sr:match:123", "marketId": "18", "outcomeId": "12", "specifier": "total=3.5"}]
payload = {"bizCode": 10000, "data": {"shareCode": "ABC", "betType": "system",
           "ticket": {"selections": refs, "displayTotalOdds": "3.75",
                      "bets": [{"selectedSystems": [1, 2]}]}, "outcomes": [event]}}
'''


class ProviderIntegrityTests(unittest.TestCase):
    def run_provider(self, provider, script):
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT / provider,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sporty_preserves_multiple_refs_and_system_metadata(self):
        self.run_provider("sportybet", SPORTY_FIXTURE + '''
c = parse_share("ABC", payload)
assert c.ok and c.num_legs == 2
out = c.as_dict()
assert out["bet_type"] == "system" and out["selected_systems"] == [[1, 2]]
assert out["selections"][0]["native_event_id"] == "sr:match:123"
assert out["selections"][0]["specifier"] == "total=2.5"
assert out["selections"][0]["kickoff"].endswith("+00:00")
assert out["selections"][0]["selection_key"] != out["selections"][1]["selection_key"]
''')

    def test_provider_derived_setka_fixture_preserves_opaque_id(self):
        self.run_provider("sportybet", '''
import json
from pathlib import Path
from infra.parser import parse_share
payload = json.loads(Path("../ticket-analysis/tests/fixtures/sportybet_setka_native.json").read_text())
c = parse_share("FIXTURE", payload)
assert c.ok and c.retrieval_completeness == "complete"
leg = c.as_dict()["selections"][0]
assert leg["event_id"] == 0
assert leg["native_event_id"] == "sr:match:B1kJVsX-2R76hb1ZDH2kz4w"
assert leg["market_id"] == "237" and leg["specifier"] == "hcp=1.5"
assert c.as_dict()["site_displayed_odds"] is None
assert c.as_dict()["payout_semantics"]["status"] == "unknown"
''')

    def test_bet9ja_disappearing_leg_does_not_create_count_mismatch(self):
        self.run_provider("bet9ja", '''
from infra.parser import parse_coupon
p = {"R": "OK", "D": {"O": {
    "first": {"E_ID": "1", "M_NAME": "1X2", "SGN": "1", "V": 2},
    "second": {"E_ID": "2", "M_NAME": "1X2", "SGN": "2", "V": 3}}}}
before = parse_coupon("CODE", p).as_dict()
del p["D"]["O"]["second"]
after = parse_coupon("CODE", p).as_dict()
assert before["num_legs"] == len(before["selections"]) == 2
assert after["num_legs"] == len(after["selections"]) == 1
assert after["retrieval_completeness"] == "unknown"
assert after["site_displayed_odds"] is None
''')

    def test_sporty_ambiguous_and_missing_refs_do_not_fall_back(self):
        self.run_provider("sportybet", SPORTY_FIXTURE + '''
del refs[0]["specifier"]
c = parse_share("ABC", payload)
assert not c.ok and "2 outcomes" in c.error
refs[0]["marketId"] = "missing"
c = parse_share("ABC", payload)
assert not c.ok and "0 outcomes" in c.error
payload["data"]["ticket"]["selections"] = []
assert not parse_share("ABC", payload).ok
''')

    def test_sporty_duplicate_refs_rejected(self):
        self.run_provider("sportybet", SPORTY_FIXTURE + '''
refs.append(copy.deepcopy(refs[0]))
assert not parse_share("ABC", payload).ok
''')

    def test_bet9ja_preserves_native_key_and_type(self):
        self.run_provider("bet9ja", '''
from infra.parser import parse_coupon
c = parse_coupon("aBc", {"R": "OK", "D": {"BTYPE": 3, "O": {
    "native-selection": {"E_ID": "123", "E_NAME": "A - B", "GN": "Test",
                         "M_NAME": "1X2", "SGN": "1", "V": 2.5}}}})
out = c.as_dict()
assert out["code"] == "aBc" and out["bet_type"] == "3"
assert out["selections"][0]["selection_key"] == "native-selection"
assert out["selections"][0]["native_event_id"] == "123"
''')

    def test_archives_append_before_filtering_and_keep_payload(self):
        script = '''
import json, tempfile
from pathlib import Path
from engine.archive import archive_response
with tempfile.TemporaryDirectory() as directory:
    payload = {"response": [1, 2], "nested": {"keep": True}}
    t1, p1 = archive_response(directory, "test", "../../untrusted", payload)
    t2, p2 = archive_response(directory, "test", "../../untrusted", payload)
    assert p1 != p2 and Path(p1).parent == Path(directory).resolve() / "raw"
    assert json.loads(Path(p1).read_text())["payload"] == payload
    assert json.loads(Path(p1).read_text())["payload_sha256"] == json.loads(Path(p2).read_text())["payload_sha256"]
'''
        for provider in ("bet9ja", "sportybet"):
            with self.subTest(provider=provider):
                self.run_provider(provider, script)

    def test_export_keeps_all_qualifying_and_records_effective_bounds(self):
        script = '''
import json, tempfile
from pathlib import Path
from engine.scanner import CouponScanner, ScanResult
from engine.settings import BaseSettings
from engine.coupon import Coupon, Selection
with tempfile.TemporaryDirectory() as directory:
    settings = BaseSettings(results_dir=Path(directory), top_n=1, min_total_odds=10, max_total_odds=100)
    tickets = [Coupon(code=str(i), ok=True, selections=[Selection(str(i), i, "A-B", "L", "1X2", "Home", 3)]) for i in (1, 2)]
    scanner = CouponScanner(None, settings)
    result = ScanResult(seed="TEST", qualifying=tickets, effective_min_odds=2)
    first = scanner.save(result)
    second = scanner.save(result)
    assert first["extract"] != second["extract"]
    assert first["report"].is_file()
    assert (Path(directory) / "START_HERE.txt").is_file()
    assert len(list((Path(directory) / "summaries").glob("*.txt"))) == 2
    data = json.loads(first["extract"].read_text())
    assert len(data["qualifying"]) == 2
    assert data["min_total_odds"] == 2 and data["max_total_odds"] == 100
    assert len(first["codes"].read_text().splitlines()) == 1
    from unittest.mock import patch
    with patch("engine.scanner.subprocess.run", side_effect=OSError("report unavailable")):
        fallback = scanner.save(result)
    assert fallback["extract"].is_file() and fallback["codes"].is_file()
    assert "report" not in fallback
'''
        for provider in ("bet9ja", "sportybet"):
            with self.subTest(provider=provider):
                self.run_provider(provider, script)
