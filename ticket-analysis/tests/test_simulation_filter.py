import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SimulationFilterTests(unittest.TestCase):
    def run_provider(self, provider, script):
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT / provider,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_markers_and_real_names(self):
        for provider in ('bet9ja', 'sportybet'):
            with self.subTest(provider=provider):
                self.run_provider(provider, '''
from engine.event_policy import simulation_reason
for value in ['Z.Moreirense - Z.Academico Viseu', 'Real - z.Other', 'Arsenal SRL - Leeds SRL',
              'srl', 'England Simulated Reality League', 'Virtual Football', 'Simulation', 'Z. Moreirense']:
    assert simulation_reason(value), value
for value in ['Zurich - Basel', 'Arsenal - Leeds', 'AZ Alkmaar - PSV', 'Moreirense - Academico Viseu', 'SRLa Team']:
    assert not simulation_reason(value), value
assert simulation_reason('Real A - Real B', 'Premier League SRL')
''')

    def test_scan_and_save_reject_whole_mixed_coupons(self):
        for provider in ('bet9ja', 'sportybet'):
            with self.subTest(provider=provider):
                self.run_provider(provider, '''
import json, tempfile
from pathlib import Path
from unittest.mock import patch
from engine.coupon import Coupon, Selection
from engine.scanner import CouponScanner, ScanResult
from engine.settings import BaseSettings

def leg(key, event, league='League'):
    return Selection(key, int(key), event, league, '1X2', 'Home', 2)
real = Coupon('REAL', True, [leg('1', 'Arsenal - Leeds')])
mixed = Coupon('MIXED', True, [leg('2', 'Real - Other'), leg('3', 'Z.Moreirense - Z.Academico Viseu')])
srl = Coupon('SIM', True, [leg('4', 'Real A - Real B', 'England SRL')])
class Client:
    def decode(self, code):
        return {'SEED': Coupon('SEED', False), 'REAL': real, 'MIXED': mixed, 'SIM': srl}[code]
with tempfile.TemporaryDirectory() as folder:
    settings = BaseSettings(results_dir=Path(folder), min_total_odds=1, request_workers=1,
                            request_delay_ms=0, max_codes_to_try=3, max_qualifying=10)
    scanner = CouponScanner(Client(), settings)
    with patch('engine.scanner.mutate', return_value=iter(['MIXED', 'SIM', 'REAL'])):
        scanned = scanner.scan('SEED')
    assert [c.code for c in scanned.qualifying] == ['REAL']
    assert scanned.excluded_simulations == 2
    paths = scanner.save(ScanResult(seed='TEST', qualifying=[mixed, srl, real]))
    data = json.loads(paths['extract'].read_text())
    assert [c['code'] for c in data['qualifying']] == ['REAL']
    assert data['excluded_simulations'] == 2
    assert 'MIXED' not in paths['codes'].read_text()
    assert 'SIM' not in paths['codes'].read_text()
''')

    def test_bet9ja_filters_before_click(self):
        self.run_provider('bet9ja', '''
from unittest.mock import Mock
from pages.sports_page import SportsPage
from infra.config.settings import Settings
page = Mock()
sports = SportsPage(page, Settings())
sports.list_matches = lambda: [{'eid':'1','home':'Z.Moreirense','away':'Z.Academico Viseu'}]
try:
    sports.pick_random_odd()
except RuntimeError:
    pass
else:
    raise AssertionError('simulation was chosen')
page.locator.assert_not_called()
sports.list_matches = lambda: [{'eid':'1','home':'Z.Moreirense','away':'Other'},
                                {'eid':'2','home':'Arsenal','away':'Leeds'}]
assert sports.pick_random_odd()['eid'] == '2'
page.locator.return_value.click.assert_called_once()
''')

    def test_sportybet_filters_before_click(self):
        self.run_provider('sportybet', '''
from unittest.mock import Mock
from pages.football_page import FootballPage
from infra.config.settings import Settings
from tests.data import Selectors as S

def cell(text, known=True):
    c = Mock()
    row, league = Mock(), Mock()
    row.count.return_value = int(known)
    row.inner_text.return_value = text
    league.count.return_value = 1
    league.inner_text.return_value = 'England Premier League\\nMatches'
    c.locator.side_effect = lambda selector: row if selector == S.MATCH_ROW_ANCESTOR else league
    return c
for candidates in [[cell('Arsenal SRL - Leeds SRL')], [cell('Z.Moreirense - Other')], [cell('2.0',False)]]:
    page = Mock()
    page.locator.return_value.count.return_value = len(candidates)
    page.locator.return_value.nth.side_effect = lambda i: candidates[i]
    assert not FootballPage(page,Settings()).pick_random_outcome()
    for c in candidates: c.click.assert_not_called()
real = cell('Arsenal - Leeds')
page = Mock()
page.locator.return_value.count.return_value = 1
page.locator.return_value.nth.return_value = real
assert FootballPage(page,Settings()).pick_random_outcome()
real.click.assert_called_once()
''')
