import json
from pathlib import Path
import tempfile
import unittest

from datetime import datetime, timedelta, timezone

from portfolio.reporting import archive_stale, load_catalog, render


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'extracts').mkdir()

    def write(self, name, payload):
        path = self.root / 'extracts' / name
        path.write_text(json.dumps(payload))
        return path

    def ticket(self, **extra):
        return {'code': 'ABC', 'selections': [
            {'event_id': 1, 'event': 'A - B', 'market': '1X2', 'pick': 'Home', 'odds': 2},
            {'event_id': 2, 'event': 'C - D', 'market': '1X2', 'pick': 'Home', 'odds': 3}], **extra}

    def test_odds_are_distinct_and_legacy_not_promoted(self):
        self.write('scan_old_20260101-120000.json', {'provider': 'sportybet', 'qualifying': [
            self.ticket(total_odds=99), self.ticket(code='DEF', site_displayed_odds=8)]})
        run = load_catalog(self.root, 'sportybet')['runs'][0]
        legacy, modern = run['tickets']
        self.assertEqual(legacy['product'], 6)
        self.assertIsNone(legacy['site'])
        self.assertEqual(legacy['legacy'], 99)
        self.assertEqual(modern['site'], 8)
        self.assertEqual(modern['product'], 6)
        self.assertEqual(run['time_source'], 'filename')
        self.assertIn('timezone unknown', run['time'])

    def test_bad_input_visible_and_runs_sorted_by_capture(self):
        self.write('z.json', {'saved_at': '2026-01-01T12:00:00+00:00', 'qualifying': [self.ticket()]})
        self.write('a.json', {'saved_at': '2026-01-02T12:00:00+00:00', 'qualifying': [None, self.ticket(provider='sportybet')]})
        (self.root / 'extracts' / 'broken.json').write_text('{oops')
        data = load_catalog(self.root, 'bet9ja')
        self.assertEqual(data['runs'][0]['id'], 'a.json')
        self.assertEqual(data['runs'][0]['rejected'], 2)
        self.assertEqual(len(data['errors']), 1)

    def test_script_injection_escaped_and_originals_untouched(self):
        attack = '</script><script>alert(1)</script>'
        source = self.write('input.json', {'seed': attack, 'qualifying': [self.ticket(code=attack)]})
        before = source.read_bytes()
        report = render(self.root, 'bet9ja').read_text()
        self.assertNotIn(attack, report)
        self.assertIn('\\u003c/script>', report)
        self.assertEqual(before, source.read_bytes())
        self.assertTrue((self.root / 'START_HERE.txt').exists())
        summary = (self.root / 'summaries' / 'input.txt').read_text()
        self.assertIn('Calculated odds', summary)
        self.assertIn('6.00', summary)
        self.assertEqual(summary, (self.root / 'latest.txt').read_text())

    def test_empty_directory_has_valid_report(self):
        render(self.root, 'bet9ja')
        self.assertEqual(load_catalog(self.root, 'bet9ja')['runs'], [])
        self.assertIn('No readable scans', (self.root / 'START_HERE.txt').read_text())

    def _leg(self, kickoff):
        return {'event_id': 1, 'event': 'A - B', 'market': '1X2', 'pick': 'Home', 'odds': 2, 'kickoff': kickoff}

    def test_expired_tickets_hidden_but_live_kept(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        self.write('scan_mix_20260101-120000.json', {'provider': 'sportybet', 'qualifying': [
            {'code': 'DEAD', 'selections': [self._leg(past), self._leg(future)]},
            {'code': 'LIVE', 'selections': [self._leg(future), self._leg(future)]}]})
        run = load_catalog(self.root, 'sportybet')['runs'][0]
        codes = [t['code'] for t in run['tickets']]
        self.assertEqual(codes, ['LIVE'])            # DEAD hidden (a leg already kicked off)
        self.assertEqual(run['expired'], 1)

    def test_fully_expired_scan_dropped(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.write('scan_dead_20260101-120000.json', {'provider': 'sportybet', 'qualifying': [
            {'code': 'X1', 'selections': [self._leg(past)]}]})
        self.assertEqual(load_catalog(self.root, 'sportybet')['runs'], [])

    def test_unknown_kickoff_is_kept(self):
        self.write('scan_unk_20260101-120000.json', {'provider': 'sportybet', 'qualifying': [self.ticket()]})
        run = load_catalog(self.root, 'sportybet')['runs'][0]
        self.assertEqual([t['code'] for t in run['tickets']], ['ABC'])

    def test_archive_stale_moves_only_dead_scans(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        self.write('scan_dead_20260101-120000.json', {'provider': 'sportybet', 'qualifying': [
            {'code': 'D', 'selections': [self._leg(past)]}]})
        self.write('scan_live_20260102-120000.json', {'provider': 'sportybet', 'qualifying': [
            {'code': 'L', 'selections': [self._leg(future)]}]})
        moved = archive_stale(self.root, 'sportybet')
        self.assertEqual(moved, ['scan_dead_20260101-120000.json'])
        self.assertFalse((self.root / 'extracts' / 'scan_dead_20260101-120000.json').exists())
        self.assertTrue((self.root / 'archive' / 'extracts' / 'scan_dead_20260101-120000.json').exists())
        self.assertTrue((self.root / 'extracts' / 'scan_live_20260102-120000.json').exists())
