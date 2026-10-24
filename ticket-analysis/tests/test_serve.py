"""HTTP and real registry/worker integration without provider traffic."""
import http.client
import io
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from portfolio.serve import make_handler, main
from test_registry import RegistryTestBase
from test_decorrelation import coupon


class APITests(RegistryTestBase):
    def setUp(self):
        super().setUp()
        self.reg = self.registry(cancel_grace_s=0.1)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.workspace, 'bet9ja', self.reg))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        data = raw if raw is not None else json.dumps(body or {}) if method == 'POST' else None
        try:
            conn.request(method, path, data, {'Content-Type': 'application/json', **(headers or {})})
            response = conn.getresponse()
            content = response.read()
            return response.status, json.loads(content) if content else None
        finally:
            conn.close()

    def raw(self, method, path, headers=None):
        """Unparsed reply, for the HTML routes."""
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            conn.request(method, path, None, headers or {})
            response = conn.getresponse()
            return response.status, response.getheader('Content-Type'), response.read()
        finally:
            conn.close()

    def start(self, provider='sportybet'):
        status, run = self.request('POST', '/api/scan', {'provider': provider, 'seed': 'ABC123' if provider == 'sportybet' else 'AbC1234'})
        self.assertEqual(status, 202, run)
        return run['run_id']

    def test_real_scan_poll_history_analysis_both_providers(self):
        for provider in ('sportybet', 'bet9ja'):
            run_id = self.start(provider)
            self.wait_state(self.reg, run_id, {'succeeded'})
            status, run = self.request('GET', '/api/scan/' + run_id)
            self.assertEqual(status, 200)
            self.assertEqual(run['state'], 'succeeded')
            self.assertIsNotNone(run['counters']['tried'])
            self.assertIsNotNone(run['stop_reason'])
            status, history = self.request('GET', '/api/runs?provider=' + provider)
            self.assertEqual([r['run_id'] for r in history['runs']], [run_id])
            for route in ('distinct', 'decorrelate'):
                status, report = self.request('POST', '/api/' + route, {'run_id': run_id})
                self.assertEqual(status, 200, report)
                self.assertEqual(report['provider'], provider)
                self.assertEqual(report['pool_size'], 0)  # fixture coupons below committed band
                self.assertEqual(report['min_odds'], 5000)
            self.assertEqual(self.request('HEAD', '/api/scan/' + run_id), (200, None))

    def test_cancel_busy_and_unfinished_analysis(self):
        with patch.dict('os.environ', {'FIXTURE_SLEEP_SECONDS': '2'}):
            run_id = self.start()
            self.assertEqual(self.request('POST', '/api/scan', {'provider': 'sportybet', 'seed': 'ABC123'})[0], 409)
            self.assertEqual(self.request('POST', '/api/distinct', {'run_id': run_id})[0], 409)
            self.assertEqual(self.request('POST', '/api/scan/' + run_id + '/cancel')[0], 200)
            self.wait_state(self.reg, run_id, {'cancelled'})
            self.assertEqual(self.request('POST', '/api/scan/' + run_id + '/cancel')[0], 200)

    def test_scan_validation_before_launch(self):
        base = {'provider': 'sportybet', 'seed': 'ABC123'}
        cases = [{'want_count': True}, {'try_budget': 0}, {'try_budget': 1.5}, {'min_odds': -1}, {'min_odds': 0.5},
                 {'max_odds': 2, 'min_odds': 3}, {'provider': []}, {'seed': '--help'},
                 {'env_extra': {}}, {'extract_path': '/tmp/anything'}, {'fresh_seed': 'yes'},
                 {'fresh_seed': 1}, {'fresh_seed': True}]  # a seed alongside fresh_seed
        with patch.object(self.reg, 'start_scan') as start:
            for change in cases:
                self.assertEqual(self.request('POST', '/api/scan', {**base, **change})[0], 400, change)
            start.assert_not_called()

    def test_scan_defaults_and_translation(self):
        with patch.object(self.reg, 'start_scan', return_value={'run_id': 'fake'}) as start:
            self.assertEqual(self.request('POST', '/api/scan', {'provider': 'sportybet', 'seed': 'abc123', 'min_odds': 0})[0], 202)
            start.assert_called_once_with(provider='sportybet', seed='ABC123', fresh_seed=False,
                                          min_odds=1, max_odds=350000, max_qualifying=0,
                                          max_codes=25000, depth=3)

    def test_fresh_seed_scan_books_its_own_code(self):
        with patch.object(self.reg, 'start_scan', return_value={'run_id': 'fake'}) as start:
            status, _ = self.request('POST', '/api/scan', {'provider': 'bet9ja', 'fresh_seed': True})
            self.assertEqual(status, 202)
            start.assert_called_once_with(provider='bet9ja', seed=None, fresh_seed=True,
                                          min_odds=1, max_odds=350000, max_qualifying=0,
                                          max_codes=25000, depth=3)
        # An empty seed field alongside the checkbox is the console's own shape.
        with patch.object(self.reg, 'start_scan', return_value={'run_id': 'fake'}) as start:
            self.assertEqual(self.request('POST', '/api/scan', {'provider': 'bet9ja', 'seed': '', 'fresh_seed': True})[0], 202)
            self.assertIsNone(start.call_args.kwargs['seed'])

    def test_fresh_seed_run_end_to_end(self):
        os.environ['FIXTURE_BOOKED_SEED'] = 'BK9JA07'
        status, run = self.request('POST', '/api/scan', {'provider': 'bet9ja', 'fresh_seed': True})
        self.assertEqual(status, 202, run)
        self.assertIsNone(run['seed'])  # still booking
        self.wait_state(self.reg, run['run_id'], {'succeeded'})
        status, final = self.request('GET', '/api/scan/' + run['run_id'])
        self.assertEqual(status, 200)
        self.assertEqual(final['seed'], 'BK9JA07')
        self.assertIs(final['request']['fresh_seed'], True)
        status, report = self.request('POST', '/api/distinct', {'run_id': run['run_id']})
        self.assertEqual(status, 200, report)

    def test_fresh_seed_unavailable_is_reported_as_501(self):
        (self.workspace / 'bet9ja' / 'scripts' / 'book_and_scan.py').unlink()
        status, body = self.request('POST', '/api/scan', {'provider': 'bet9ja', 'fresh_seed': True})
        self.assertEqual(status, 501, body)
        self.assertEqual(body['error']['code'], 'fresh_seed_unavailable')
        # The same provider still scans from a typed seed.
        self.assertEqual(self.request('POST', '/api/scan', {'provider': 'bet9ja', 'seed': 'AbC1234'})[0], 202)

    def test_invalid_json_and_framing(self):
        for raw in ('[]', 'null', 'false', '{', '{"x":NaN}', '{"x":1,"x":2}'):
            self.assertEqual(self.request('POST', '/api/scan', raw=raw)[0], 400, raw)
        self.assertEqual(self.request('POST', '/api/scan', raw='x' * 65537)[0], 413)
        self.assertEqual(self.request('POST', '/api/scan', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/scan', headers={'Transfer-Encoding': 'chunked'})[0], 400)

    def test_local_access_host_and_origin(self):
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}, {'Origin': 'null'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Host': '127.0.0.1:1'}):
            self.assertEqual(self.request('GET', '/api/runs', headers=headers)[0], 403, headers)
        local = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.assertEqual(self.request('GET', '/api/runs', headers={'Origin': local})[0], 200)
        with patch('portfolio.serve.is_loopback', return_value=False):
            self.assertEqual(self.request('POST', '/api/scan')[0], 403)

    def test_routing_and_parameter_errors(self):
        for path in ('/api/nope', '/api/runs/extra'):
            self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('GET', '/api/runs?provider=bad')[0], 400)
        self.assertEqual(self.request('GET', '/api/runs?provider=bet9ja&provider=sportybet')[0], 400)
        with patch.object(self.reg, 'get') as get:
            for value in (0, -1, True, 1.5, 'bad'):
                self.assertEqual(self.request('POST', '/api/decorrelate', {'run_id': 'irrelevant', 'max_exposure': value})[0], 400)
            get.assert_not_called()

    def test_full_pool_exclusions_and_snapshot_no_reopen(self):
        path = self.workspace / 'extract.json'
        rows = [coupon('A', 'shared'), coupon('B', 'shared'), coupon('C', 'solo'), coupon('FOREIGN', 'other', 'bet9ja')]
        snapshot = json.dumps({'provider': 'sportybet', 'odds_basis': 'parsed_leg_product',
                               'min_total_odds': 2, 'max_total_odds': 10, 'qualifying': rows}).encode()
        path.write_text('changed after verification')
        with patch.object(self.reg, 'get', return_value={'provider': 'sportybet'}), \
             patch.object(self.reg, 'committed_extract_snapshot', return_value=(path, snapshot)):
            status, distinct = self.request('POST', '/api/distinct', {'run_id': 'test'})
            self.assertEqual(status, 200, distinct)
            self.assertEqual(distinct['distinct_codes'], 1)
            for cap, count in ((1, 2), (2, 3)):
                status, report = self.request('POST', '/api/decorrelate', {'run_id': 'test', 'max_exposure': cap})
                self.assertEqual(status, 200, report)
                self.assertEqual(report['pool_size'], 3)
                self.assertEqual(report['selection']['selected_count'], count)
                distinct_codes = {ticket['raw']['code'] for ticket in distinct['distinct']}
                self.assertLessEqual(distinct_codes, set(report['selection']['selected_codes']))
                self.assertEqual(report['excluded'][0]['reason'], 'provider_mismatch')

    def test_codes_lists_the_whole_pool_the_analyses_read(self):
        path = self.workspace / 'extract.json'
        rows = [coupon('A', 'shared'), coupon('B', 'shared'), coupon('C', 'solo'), coupon('FOREIGN', 'other', 'bet9ja')]
        snapshot = json.dumps({'provider': 'sportybet', 'odds_basis': 'parsed_leg_product',
                               'min_total_odds': 2, 'max_total_odds': 10, 'qualifying': rows}).encode()
        with patch.object(self.reg, 'get', return_value={'provider': 'sportybet', 'state': 'succeeded'}), \
             patch.object(self.reg, 'committed_extract_snapshot', return_value=(path, snapshot)):
            status, listing = self.request('POST', '/api/codes', {'run_id': 'test'})
            self.assertEqual(status, 200, listing)
            status, distinct = self.request('POST', '/api/distinct', {'run_id': 'test'})
            self.assertEqual(status, 200, distinct)
            # Overlapping codes are listed too; only ineligible rows are left out.
            self.assertEqual(['A', 'B', 'C'], sorted(ticket['raw']['code'] for ticket in listing['codes']))
            self.assertEqual(listing['pool_size'], 3)
            self.assertEqual(listing['pool_size'], distinct['pool_size'])
            self.assertEqual(listing['excluded'][0]['reason'], 'provider_mismatch')
            self.assertTrue(all(ticket['legs'] for ticket in listing['codes']))
            self.assertNotIn('tickets', listing)
            self.assertEqual(listing['run_id'], 'test')
            self.assertEqual(listing['odds_label'], 'recorded, not verified payout')
            # It takes a run id and nothing else, like stage 2.
            for body in ({}, {'run_id': ''}, {'run_id': 'test', 'max_exposure': 1}):
                self.assertEqual(self.request('POST', '/api/codes', body)[0], 400)
        self.assertEqual(self.request('GET', '/api/codes')[0], 405)

    def test_history_reveals_started_codes_without_changing_analysis(self):
        from pathlib import Path
        from portfolio.api import dispatch
        from unittest.mock import Mock

        for provider in ('sportybet', 'bet9ja'):
            with self.subTest(provider=provider):
                upcoming = coupon('UPCOMING', 'future', provider=provider)
                started = coupon('STARTED', 'past', provider=provider)
                broken = coupon('BROKEN', 'broken', provider=provider)
                upcoming['selections'][0]['kickoff'] = '2100-01-01T00:00:00Z'
                started['selections'][0]['kickoff'] = '2000-01-01T00:00:00Z'
                broken['selections'][0]['kickoff'] = '2000-01-01T00:00:00Z'
                broken['num_legs'] = 99
                saved = {'provider': provider, 'odds_basis': 'parsed_leg_product',
                         'min_total_odds': 1, 'max_total_odds': 0,
                         'qualifying': [upcoming, started, broken]}
                snapshot = json.dumps(saved).encode()
                reg = Mock()
                reg.get.return_value = {'provider': provider, 'state': 'succeeded'}
                reg.saved_codes_snapshot.return_value = (Path('snapshot.json'), snapshot, False)
                reg.committed_extract_snapshot.return_value = (Path('snapshot.json'), snapshot)
                _, listing = dispatch(reg, 'POST', '/api/codes', {'run_id': 'history'}, {})
                self.assertEqual(listing['saved_count'], 3)
                self.assertEqual(listing['pool_size'], 1)
                self.assertEqual(listing['expired_count'], 1)
                self.assertEqual([t['raw']['code'] for t in listing['codes']], ['UPCOMING'])
                self.assertEqual([t['raw']['code'] for t in listing['expired_codes']], ['STARTED'])
                self.assertIn('outcome not checked', listing['expired_codes'][0]['history_note'])
                self.assertEqual(len(listing['excluded']), 2)
                _, analysis = dispatch(reg, 'POST', '/api/distinct', {'run_id': 'history'}, {})
                self.assertEqual(analysis['pool_size'], 1)
                self.assertEqual([t['raw']['code'] for t in analysis['distinct']], ['UPCOMING'])
                saved['qualifying'] = [started]
                reg.saved_codes_snapshot.return_value = (Path('snapshot.json'), json.dumps(saved).encode(), True)
                _, listing = dispatch(reg, 'POST', '/api/codes', {'run_id': 'history'}, {})
                self.assertEqual(listing['pool_size'], 0)
                self.assertEqual(listing['expired_count'], 1)
                self.assertTrue(listing['partial'])

    def test_codes_lists_what_an_unfinished_run_saved(self):
        run_dir = self.workspace / 'stopped-run'
        run_dir.mkdir()
        saved = {'provider': 'sportybet', 'odds_basis': 'parsed_leg_product', 'partial': True,
                 'min_total_odds': 2, 'max_total_odds': 10, 'stop_reason': None,
                 'qualifying': [coupon('A', 'shared'), coupon('B', 'shared')]}
        for state in ('running', 'failed', 'cancelled'):
            with self.subTest(state=state), \
                 patch.object(self.reg, 'get', return_value={'provider': 'sportybet', 'state': state,
                                                             'extract': None}), \
                 patch.object(self.reg, '_resolve_run_dir', return_value=run_dir):
                # Nothing saved yet is a conflict, not an empty list.
                (run_dir / 'checkpoint.json').unlink(missing_ok=True)
                self.assertEqual(self.request('POST', '/api/codes', {'run_id': 'test'})[0], 409)
                (run_dir / 'checkpoint.json').write_text(json.dumps(saved))
                status, listing = self.request('POST', '/api/codes', {'run_id': 'test'})
                self.assertEqual(status, 200, listing)
                self.assertTrue(listing['partial'])
                self.assertEqual(listing['state'], state)
                self.assertEqual(['A', 'B'], sorted(ticket['raw']['code'] for ticket in listing['codes']))
                # A checkpoint is never an extract: the analyses still refuse the run.
                for route in ('/api/distinct', '/api/decorrelate'):
                    self.assertEqual(self.request('POST', route, {'run_id': 'test'})[0], 409)
                # One written for another provider, or cut short, is refused.
                (run_dir / 'checkpoint.json').write_text(json.dumps({**saved, 'provider': 'bet9ja'}))
                self.assertEqual(self.request('POST', '/api/codes', {'run_id': 'test'})[0], 409)
                (run_dir / 'checkpoint.json').write_text('{"provider": "sportybet", "qualif')
                self.assertEqual(self.request('POST', '/api/codes', {'run_id': 'test'})[0], 409)

    def test_all_get_and_head_routes_guarded_before_side_effects(self):
        (self.workspace / 'private.txt').write_text('private codes')
        with patch('portfolio.serve.render') as render:
            for method in ('GET', 'HEAD'):
                for path in ('/', '/app', '/__version', '/private.txt', '/scripts/'):
                    for headers in ({'Host': 'attacker.test'},
                                    {'Origin': 'http://attacker.test'},
                                    {'Sec-Fetch-Site': 'cross-site'}):
                        self.assertEqual(self.request(method, path, headers=headers)[0], 403)
            render.assert_not_called()
        with patch('portfolio.serve.is_loopback', return_value=False):
            self.assertEqual(self.request('GET', '/')[0], 403)

    def test_normalized_origin_and_invalid_authorities(self):
        port = self.server.server_address[1]
        headers = {'Host': f'LOCALHOST:{port}', 'Origin': f'http://localhost:{port}'}
        self.assertEqual(self.request('GET', '/api/runs', headers=headers)[0], 200)
        for origin in (f'https://localhost:{port}', f'http://localhost:{port}/path',
                       'http://localhost:bad', 'http://[', f'http://user@localhost:{port}'):
            self.assertEqual(self.request('GET', '/api/runs', headers={**headers, 'Origin': origin})[0], 403)
        for host in ('localhost:bad', '['):
            self.assertEqual(self.request('GET', '/', headers={'Host': host})[0], 403)

    def test_unexpected_exception_and_missing_scanner_are_sanitized(self):
        log = io.StringIO()
        with patch('sys.stderr', log), \
             patch.object(self.reg, 'list_runs', side_effect=TypeError('private details')):
            status, body = self.request('GET', '/api/runs')
            self.assertEqual(status, 500)
            self.assertEqual(body['error']['code'], 'server_error')
            self.assertNotIn('private details', str(body))
        # The wire stays generic, but an unexpected failure is a bug: the cause
        # must still reach the console, since log_message is silenced.
        self.assertIn('TypeError: private details', log.getvalue())
        (self.workspace / 'bet9ja/scripts/scan_code.py').unlink()
        log = io.StringIO()
        with patch('sys.stderr', log):
            status, body = self.request('POST', '/api/scan', {'provider': 'bet9ja', 'seed': 'ABC1234'})
        self.assertEqual(status, 500)
        self.assertNotIn(str(self.workspace), str(body))
        self.assertEqual(self.reg.list_runs(), [])
        self.assertEqual(log.getvalue(), '')  # a known condition, not a bug: no traceback

    def test_malformed_snapshot_and_single_parse(self):
        import portfolio.api as api
        original_loads = json.loads
        path = self.workspace / 'extract.json'
        good = json.dumps({'provider': 'sportybet', 'odds_basis': 'parsed_leg_product',
                           'min_total_odds': 1, 'qualifying': []}).encode()
        with patch.object(self.reg, 'get', return_value={'provider': 'sportybet', 'state': 'succeeded'}):
            for route in ('codes', 'distinct', 'decorrelate'):
                for snapshot in (b'not json', b'\xff'):
                    with patch.object(self.reg, 'committed_extract_snapshot', return_value=(path, snapshot)):
                        status, body = self.request('POST', '/api/' + route, {'run_id': 'test'})
                        self.assertEqual(status, 422)
                        self.assertEqual(body['error']['message'], 'Cannot parse committed extract')
                with patch.object(self.reg, 'committed_extract_snapshot', return_value=(path, good)), \
                     patch('portfolio.api.json.loads', wraps=original_loads) as loads:
                    status, _ = api.dispatch(self.reg, 'POST', '/api/' + route, {'run_id': 'test'}, {})
                    self.assertEqual(status, 200)
                    loads.assert_called_once_with(good)

    def test_method_errors_are_json(self):
        for method, path in (('GET', '/api/scan/test/cancel'), ('POST', '/api/scan/test'),
                             ('PUT', '/api/scan'), ('DELETE', '/api/runs'), ('OPTIONS', '/api/distinct')):
            status, body = self.request(method, path)
            self.assertEqual(status, 405)
            self.assertEqual(body['error']['code'], 'method_not_allowed')
        status, body = self.request('CUSTOM', '/api/scan')
        self.assertEqual(status, 501)
        self.assertEqual(body['error']['code'], 'http_error')

    def test_public_binding_refused_before_registry_start(self):
        for host in ('0.0.0.0', '192.0.2.1', '::'):
            with patch('sys.argv', ['serve', '--provider', 'bet9ja', '--host', host]), \
                 patch('portfolio.serve.RunRegistry') as registry, \
                 patch('sys.stderr'):
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                registry.assert_not_called()

    def test_legacy_report_still_served(self):
        def render(results, provider):
            (results / 'index.html').write_text('<body>Report</body>')
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            with patch('portfolio.serve.render', side_effect=render):
                conn.request('GET', '/')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b'__version', response.read())
        finally:
            conn.close()

    def test_console_page_serves_the_one_copy_helper(self):
        status, kind, body = self.raw('GET', '/app')
        self.assertEqual(status, 200)
        self.assertEqual(kind, 'text/html; charset=utf-8')
        page = body.decode('utf-8')
        self.assertEqual(page.count('const Copy = (() =>'), 1)
        self.assertNotIn('__COPY_JS__', page)
        for route in ('/api/scan', '/api/distinct', '/api/decorrelate'):
            self.assertIn(route, page)
        self.assertEqual(self.raw('GET', '/app/')[0], 200)
        self.assertEqual(self.raw('HEAD', '/app'), (200, kind, b''))
        # the console is a static shell: serving it never rebuilds the legacy report
        with patch('portfolio.serve.render') as render:
            self.assertEqual(self.raw('GET', '/app')[0], 200)
            render.assert_not_called()

    def test_integrity_and_analysis_failures_are_not_success(self):
        run_id = self.start()
        self.wait_state(self.reg, run_id, {'succeeded'})
        path = self.reg.committed_extract(run_id)
        path.write_text('{}')
        self.assertEqual(self.request('POST', '/api/distinct', {'run_id': run_id})[0], 409)
        bad = json.dumps({'provider': 'sportybet', 'qualifying': []}).encode()
        with patch.object(self.reg, 'committed_extract_snapshot', return_value=(path, bad)):
            self.assertEqual(self.request('POST', '/api/distinct', {'run_id': run_id})[0], 422)


if __name__ == '__main__':
    unittest.main()
