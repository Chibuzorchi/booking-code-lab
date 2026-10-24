import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ScanCheckpointTests(unittest.TestCase):
    """A scan that dies part-way must leave what it had found on disk."""

    def run_provider(self, provider, script):
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT / provider,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hits_survive_a_scan_that_fails_part_way(self):
        for provider in ('bet9ja', 'sportybet'):
            with self.subTest(provider=provider):
                self.run_provider(provider, '''
import json, tempfile
from pathlib import Path
from unittest.mock import patch
from engine.coupon import Coupon, Selection
from engine.scanner import CouponScanner
from engine.settings import BaseSettings

def hit(code, key):
    return Coupon(code, True, [Selection(key, int(key), 'Arsenal - Leeds ' + key, 'League', '1X2', 'Home', 2)])
class Client:
    def decode(self, code):
        if code == 'BOOM':
            raise RuntimeError('provider went away')
        return {'SEED': Coupon('SEED', False), 'ONE': hit('ONE', '1'), 'TWO': hit('TWO', '2')}[code]
with tempfile.TemporaryDirectory() as folder:
    settings = BaseSettings(results_dir=Path(folder), min_total_odds=1, request_workers=1,
                            request_delay_ms=0, max_codes_to_try=10, max_qualifying=0)
    settings.provider = 'PROVIDER'
    scanner = CouponScanner(Client(), settings)
    with patch('engine.scanner.mutate', return_value=iter(['ONE', 'TWO', 'BOOM', 'NEVER'])):
        try:
            scanner.scan('SEED')
        except RuntimeError:
            pass
        else:
            raise AssertionError('the scan should have failed')
    saved = json.loads((Path(folder) / 'checkpoint.json').read_text())
    assert [c['code'] for c in saved['qualifying']] == ['ONE', 'TWO'], saved['qualifying']
    assert saved['partial'] is True and saved['stop_reason'] is None
    assert saved['odds_basis'] == 'parsed_leg_product' and saved['min_total_odds'] == 1
    # Never where the registry looks for the run's one committed extract.
    assert not (Path(folder) / 'extracts').exists()

    # A scan that completes leaves the checkpoint agreeing with its extract.
    with tempfile.TemporaryDirectory() as done:
        settings.results_dir = Path(done)
        with patch('engine.scanner.mutate', return_value=iter(['ONE', 'TWO'])):
            result = scanner.scan('SEED')
        extract = json.loads(scanner.save(result)['extract'].read_text())
        saved = json.loads((Path(done) / 'checkpoint.json').read_text())
        assert [c['code'] for c in saved['qualifying']] == [c['code'] for c in extract['qualifying']]
        assert 'partial' not in extract
''')


if __name__ == '__main__':
    unittest.main()
