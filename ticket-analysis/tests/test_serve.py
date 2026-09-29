"""HTTP and real registry/worker integration without provider traffic."""
import http.client
import io
import json
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
                 {'env_extra': {}}, {'extract_path': '/tmp/anything'}, {'fresh_seed': 'yes'}]
        with patch.object(self.reg, 'start_scan') as start:
            for change in cases:
                self.assertEqual(self.request('POST', '/api/scan', {**base, **change})[0], 400, change)
            self.assertEqual(self.request('POST', '/api/scan', {**base, 'fresh_seed': True})[0], 501)
            start.assert_not_called()

    def test_scan_defaults_and_translation(self):
        with patch.object(self.reg, 'start_scan', return_value={'run_id': 'fake'}) as start:
            self.assertEqual(self.request('POST', '/api/scan', {'provider': 'sportybet', 'seed': 'abc123', 'min_odds': 0})[0], 202)
            start.assert_called_once_with(provider='sportybet', seed='ABC123', min_odds=1,
                                          max_odds=350000, max_qualifying=0, max_codes=25000, depth=3)

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
        with patch.object(self.reg, 'get', return_value={'provider': 'sportybet'}):
            for route in ('distinct', 'decorrelate'):
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
