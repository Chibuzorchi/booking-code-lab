"""Chromium regressions; all network requests are intercepted, no provider traffic.

Run: python3 -m unittest discover -s tests -p test_browser_ui.py -v
Optional: pip install playwright && python3 -m playwright install chromium
Missing Playwright skips this class; an installed but broken browser fails.
"""
import json
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from portfolio.api import dispatch
from portfolio.pages import app_page
from test_decorrelation import coupon

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None


class FixtureRegistry:
    run_id = 'run-bet9ja-20261024-051623-b93204b5'

    def __init__(self):
        self.run = dict(run_id=self.run_id, provider='bet9ja', seed='ABC1234',
                        state='succeeded', counters={'qualifying': 27})
        self.runs = {self.run_id: self.run}
        rows = [coupon(f'ABC{i:04}', f'Fixture {i}', 'bet9ja') for i in range(27)]
        for row in rows:
            row['selections'][0]['kickoff'] = '9999-01-02T00:00:00Z'
        rows[-1]['selections'][0]['kickoff'] = '2000-01-01T00:00:00Z'
        self.raw = json.dumps(dict(provider='bet9ja', qualifying=rows,
                                   odds_basis='parsed_leg_product',
                                   min_total_odds=1, max_total_odds=350000)).encode()

    def list_runs(self, provider=None):
        return [run for run in self.runs.values() if provider in (None, run['provider'])]

    def get(self, run_id):
        return self.runs[run_id]

    def committed_extract_snapshot(self, run_id):
        self.get(run_id)
        return Path('/fixture/extract.json'), self.raw

    def saved_codes_snapshot(self, run_id):
        return (*self.committed_extract_snapshot(run_id), False)


@unittest.skipUnless(sync_playwright, 'Playwright is required for browser UI tests')
class BrowserControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.registry = FixtureRegistry()
        self.errors, self.requests, self.page_errors = {}, [], []
        self.page.on('pageerror', lambda error: self.page_errors.append(str(error)))
        self.context.route('**/*', self.respond)
        self.page.add_init_script("""window.copied = '';
            Object.defineProperty(navigator, 'clipboard', {configurable:true,
                value:{writeText:async text => {window.copied = text}}});""")
        self.open_page()

    def tearDown(self):
        self.assertEqual(self.page_errors, [])

    def respond(self, route):
        url = urlsplit(route.request.url)
        if url.path == '/app':
            route.fulfill(content_type='text/html', body=app_page())
            return
        self.requests.append((url.path, route.request.post_data_json))
        if url.path in self.errors:
            route.fulfill(status=503, json={'error': {'code': 'fixture_error',
                                                    'message': self.errors[url.path]}})
            return
        status, payload = dispatch(self.registry, route.request.method, url.path,
                                   route.request.post_data_json or {},
                                   {k: v[0] for k, v in parse_qs(url.query).items()})
        route.fulfill(status=status, json=payload)

    def open_page(self):
        self.page.goto('http://127.0.0.1:8009/app')
        expect(self.page.locator('#runs')).to_be_enabled()

    def load_run(self):
        self.navigate('runs')
        self.choose_run(self.registry.run_id)
        self.page.locator('#runs-load').click()
        expect(self.page.locator('#s2-fields')).to_be_enabled()
        expect(self.page.locator('#page-title')).to_have_text('Scan Codes')

    def choose_run(self, run_id):
        self.page.locator(f'input[name="saved-run"][value="{run_id}"]').check()

    def navigate(self, section):
        if not self.page.locator('#app-sidebar').is_visible():
            self.page.locator('#nav-toggle').click()
        self.page.locator('#nav-' + section).click()

    def analyze(self):
        self.navigate('distinct')
        self.page.locator('#s2-run').click()
        expect(self.page.locator('#s3-fields')).to_be_enabled()
        self.navigate('decorrelate')
        self.page.locator('#s3-run').click()
        expect(self.page.locator('#s3-cards details')).to_have_count(26)

    def test_scan_form_sends_populated_blank_and_zero_leg_limits(self):
        for low, high, expected in [('3', '5', {'min_legs': 3, 'max_legs': 5}),
                                    ('', '', {}),
                                    ('0', '0', {'min_legs': 0, 'max_legs': 0})]:
            with self.subTest(low=low, high=high):
                self.open_page()
                self.errors['/api/scan'] = 'Fixture submission stopped'
                self.page.locator('#seed').fill('ABC1234')
                self.page.locator('#min_legs').fill(low)
                self.page.locator('#max_legs').fill(high)
                with self.page.expect_request(lambda r: r.method == 'POST' and r.url.endswith('/api/scan')) as sent:
                    self.page.locator('#s1-run').click()
                body = sent.value.post_data_json
                self.assertEqual({k: body[k] for k in ('min_legs', 'max_legs') if k in body}, expected)
                expect(self.page.locator('#s1-error')).to_contain_text('Fixture submission stopped')

    def test_seed_source_choices_preserve_payload_defaults_and_case(self):
        sent = []
        def submit(route):
            sent.append(route.request.post_data_json)
            route.fulfill(status=503, json={'error': {'message': 'Fixture stop'}})
        self.page.route('**/api/scan', submit)
        self.page.locator('#seed').fill('AbC123x')
        self.page.locator('#fresh_seed').check()
        expect(self.page.locator('#seed')).to_be_disabled()
        expect(self.page.locator('#fresh-hint')).to_be_visible()
        self.page.locator('#s1-run').click()
        expect(self.page.locator('#s1-error')).to_contain_text('Fixture stop')
        self.assertEqual(sent, [{'provider': 'bet9ja', 'fresh_seed': True}])
        expect(self.page.locator('#s1-guidance')).to_contain_text('Check Saved Runs')
        self.page.locator('#seed-typed').check()
        expect(self.page.locator('#seed')).to_be_enabled()
        expect(self.page.locator('#fresh_seed')).not_to_be_checked()
        self.page.locator('#s1-run').click()
        expect(self.page.locator('#s1-error')).to_contain_text('Fixture stop')
        self.assertEqual(sent[-1], {'provider': 'bet9ja', 'seed': 'AbC123x'})

    def test_status_failure_refreshes_read_only_and_resumes_polling(self):
        self.load_run()
        status_path = '/api/scan/' + self.registry.run_id
        self.errors[status_path] = 'Status offline'
        self.page.locator('#s1-refresh').click()
        expect(self.page.locator('#s1-guidance')).to_contain_text('Status updates paused')
        expect(self.page.locator('#s1-error')).to_contain_text('Status offline')
        del self.errors[status_path]
        self.registry.run['state'] = 'running'
        self.page.locator('#s1-refresh').click()
        expect(self.page.locator('#s1-state')).to_contain_text('Scanning codes')
        expect(self.page.locator('#s1-error')).to_be_hidden()
        expect(self.page.locator('#s1-run')).to_be_disabled()
        self.registry.run.update(state='succeeded', stop_reason='try_budget')
        expect(self.page.locator('#s1-state')).to_contain_text('Completed — Attempt limit reached', timeout=7000)
        expect(self.page.locator('#s1-run')).to_be_enabled()
        self.assertFalse(any(path == '/api/scan' for path, _ in self.requests))

    def test_cancellation_pending_survives_provider_switch_without_duplicate_request(self):
        self.load_run()
        self.registry.run['state'] = 'running'
        self.page.locator('#s1-refresh').click()
        expect(self.page.locator('#s1-cancel')).to_be_enabled()
        pending = []
        self.page.route('**/api/scan/*/cancel', lambda route: pending.append(route))
        self.page.locator('#s1-cancel').click()
        expect(self.page.locator('#s1-state')).to_have_text('Cancelling scan… · seed ABC1234')
        expect(self.page.locator('#s1-cancel')).to_be_disabled()
        self.page.locator('#provider').select_option('sportybet')
        self.page.locator('#provider').select_option('bet9ja')
        expect(self.page.locator('#s1-cancel')).to_be_disabled()
        expect(self.page.locator('#s1-run')).to_be_disabled()
        self.assertEqual(len(pending), 1)
        self.registry.run.update(state='cancelled_unresolved')
        pending.pop().fulfill(json=self.registry.run)
        expect(self.page.locator('#context-state')).to_have_text('Cancellation pending')
        self.registry.run.update(state='cancelled')
        expect(self.page.locator('#context-state')).to_have_text('Cancelled', timeout=7000)
        expect(self.page.locator('#s1-run')).to_be_enabled()

    def test_late_status_reply_cannot_undo_confirmed_cancellation(self):
        for stale_error in [False, True]:
            with self.subTest(stale_error=stale_error):
                self.registry.run.update(state='succeeded')
                self.open_page()
                self.load_run()
                self.registry.run['state'] = 'running'
                self.page.locator('#s1-refresh').click()
                expect(self.page.locator('#s1-cancel')).to_be_enabled()
                pending = []
                pattern = '**/api/scan/' + self.registry.run_id
                self.page.route(pattern, lambda route: pending.append(route))
                self.page.locator('#s1-refresh').click()
                expect(self.page.locator('#s1-refresh')).to_be_disabled()
                def cancel(route):
                    self.registry.run['state'] = 'cancelled'
                    route.fulfill(json=self.registry.run)
                self.page.route('**/api/scan/*/cancel', cancel)
                self.page.locator('#s1-cancel').click()
                expect(self.page.locator('#context-state')).to_have_text('Cancelled')
                if stale_error:
                    pending.pop().fulfill(status=503, json={'error': {'message': 'Stale failure'}})
                else:
                    pending.pop().fulfill(json=dict(self.registry.run, state='running'))
                expect(self.page.locator('#s1-refresh')).to_be_enabled()
                expect(self.page.locator('#context-state')).to_have_text('Cancelled')
                expect(self.page.locator('#s1-run')).to_be_enabled()
                expect(self.page.locator('#s1-error')).to_be_hidden()
                self.page.unroute(pattern)
                self.page.unroute('**/api/scan/*/cancel')

    def test_scan_status_keeps_raw_failure_in_diagnostics_and_layout_fits(self):
        self.registry.run.update(state='failed', stop_reason='failure',
            error={'code': 'booking_failed', 'message': 'Provider detail ' + 'x' * 300})
        self.open_page()
        self.navigate('runs')
        self.choose_run(self.registry.run_id)
        self.page.locator('#runs-load').click()
        expect(self.page.locator('#s1-state')).to_contain_text('Failed — Stopped after an error')
        expect(self.page.locator('#s1-diagnostic-text')).not_to_be_visible()
        self.page.locator('#s1-diagnostics summary').click()
        expect(self.page.locator('#s1-diagnostic-text')).to_contain_text('booking_failed')
        expect(self.page.locator('#s2-fields')).to_be_disabled()
        for width in [1440, 768, 390, 320]:
            self.page.set_viewport_size({'width': width, 'height': 1000})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width)
            for selector in ['#s1-run', '#s1-refresh', '.seed-option', '.scan-advanced summary']:
                for box in self.page.locator(selector).all():
                    self.assertGreaterEqual(box.bounding_box()['height'], 44)

    def test_stage1_leg_filter_combines_with_odds_and_limits_copy(self):
        self.load_run()
        expect(self.page.locator('#s1-cards details')).to_have_count(25)
        before = list(self.requests)
        self.page.locator('#s1-min-legs').fill('1')
        self.page.locator('#s1-max-legs').fill('1')
        self.page.locator('#s1-min').fill('1')
        expect(self.page.locator('#s1-count')).to_contain_text('odds from 1 up and 1 to 1 legs')
        self.page.locator('#s1-copy-all').click()
        self.assertEqual(len(self.page.evaluate('window.copied').splitlines()), 26)
        self.page.locator('#s1-min-legs').fill('3')
        expect(self.page.locator('#s1-cards details')).to_have_count(0)
        expect(self.page.locator('#s1-copy-all')).to_be_disabled()
        self.page.locator('#s1-view-reset').click()
        expect(self.page.locator('#s1-min-legs')).to_have_value('')
        expect(self.page.locator('#s1-max-legs')).to_have_value('')
        expect(self.page.locator('#s1-cards details')).to_have_count(25)
        self.assertEqual(self.requests, before)

    def test_empty_controls_are_hidden_and_absent_from_keyboard_navigation(self):
        expect(self.page.locator('#s2-run')).to_be_disabled()
        expect(self.page.locator('#s3-run')).to_be_disabled()
        visible_hidden = self.page.locator('[hidden]').evaluate_all(
            'els => els.filter(e => e.getClientRects().length).map(e => e.id)')
        self.assertEqual(visible_hidden, [])
        for _ in range(22):
            self.page.keyboard.press('Tab')
            self.assertFalse(self.page.evaluate('Boolean(document.activeElement.closest("[hidden]"))'))
        self.assertNotIn('Copy shown codes', self.page.locator('body').aria_snapshot())

    @staticmethod
    def contrast(first, second):
        def luminance(color):
            channels = [float(n) / 255 for n in color.removeprefix('rgb(').removesuffix(')').split(',')]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
            return sum(c * w for c, w in zip(linear, [.2126, .7152, .0722]))
        values = sorted([luminance(first), luminance(second)])
        return (values[1] + .05) / (values[0] + .05)

    def test_locked_explanations_and_control_boundaries_remain_readable(self):
        for selector in ['#s2-lock', '#s3-lock', '#s3 > .notice']:
            for element in self.page.locator(selector).all():
                colors = element.evaluate("""el => {
                    const opaque = []; for(let e=el;e;e=e.parentElement)
                        opaque.push(Number(getComputedStyle(e).opacity));
                    return {opaque, color:getComputedStyle(el).color,
                            background:getComputedStyle(el.closest('.panel')).backgroundColor};
                }""")
                self.assertTrue(all(opacity == 1 for opacity in colors['opaque']))
                self.assertGreaterEqual(self.contrast(colors['color'], colors['background']), 4.5)
        for selector in ['#seed', '#provider', '#s1-run']:
            colors = self.page.locator(selector).evaluate("""e => ({
                border:getComputedStyle(e).borderTopColor,
                background:getComputedStyle(e.closest('.panel, .app-topbar')).backgroundColor})""")
            self.assertGreaterEqual(self.contrast(colors['border'], colors['background']), 3)

    def assert_row_usable(self, row):
        button = row.get_by_role('button', name=re.compile(r'^(Copy|Copied)$'))
        geometry = button.evaluate("""b => {
            const r=b.closest('summary').getBoundingClientRect(), q=b.getBoundingClientRect();
            return {inside:q.left>=r.left && q.right<=r.right && q.bottom<=r.bottom,
                    unclipped:b.scrollWidth<=b.clientWidth+1, width:q.width, height:q.height};
        }""")
        self.assertTrue(geometry['inside'], geometry)
        self.assertTrue(geometry['unclipped'], geometry)
        self.assertGreaterEqual(geometry['width'], 44)
        self.assertGreaterEqual(geometry['height'], 44)
        code = row.locator('strong').inner_text()
        button.click()
        expect(button).to_have_text('Copied')
        self.assertEqual(self.page.evaluate('window.copied'), code)
        self.assertIsNone(row.locator('..').get_attribute('open'))
        row.focus()
        self.page.keyboard.press('Enter')
        expect(row.locator('..')).to_have_attribute('open', '')
        expect(row.locator('..').locator('.detail')).to_be_visible()
        self.assertEqual(row.evaluate('getComputedStyle(document.activeElement).outlineStyle'), 'solid')
        self.page.keyboard.press('Enter')
        expect(row.locator('..')).not_to_have_attribute('open', '')

    def test_ticket_actions_work_for_each_row_shape_and_viewport(self):
        self.load_run()
        expect(self.page.locator('#s1-cards details')).to_have_count(25)
        self.analyze()
        for width in [1440, 768, 390, 320]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 1000})
                for stage in ['s1', 's2', 's3']:
                    self.navigate({'s1': 'scan', 's2': 'distinct', 's3': 'decorrelate'}[stage])
                    self.assert_row_usable(self.page.locator(f'#{stage}-cards summary').first)
                self.navigate('scan')
                self.page.locator('#s1-expired').click()
                self.page.locator('#s1-next').click()
                historical = self.page.locator('#s1-cards summary').filter(has_text='Scheduled kickoff has passed.')
                expect(historical).to_have_count(1)
                self.assert_row_usable(historical)
                self.page.locator('#s1-expired').click()
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
        analysis = [(path, body) for path, body in self.requests if path in ['/api/distinct', '/api/decorrelate']]
        self.assertEqual(analysis, [('/api/distinct', {'run_id': self.registry.run_id}),
                                   ('/api/decorrelate', {'run_id': self.registry.run_id, 'max_exposure': 1})])

    def test_copy_fallback_remains_visible_focused_and_complete_across_pages(self):
        self.load_run()
        expect(self.page.locator('#s1-cards details')).to_have_count(25)
        self.page.evaluate("""navigator.clipboard.writeText = async () => {throw Error('denied')};
                             document.execCommand = () => false;""")
        self.page.set_viewport_size({'width': 320, 'height': 900})
        self.page.locator('#s1-copy-all').click()
        fallback = self.page.locator('#s1-bar textarea')
        expect(fallback).to_be_focused()
        self.assertEqual(len(fallback.input_value().splitlines()), 26)
        self.assertEqual(fallback.evaluate('e => getComputedStyle(e).outlineStyle'), 'solid')
        self.assertGreaterEqual(fallback.evaluate('e => parseFloat(getComputedStyle(e).outlineWidth)'), 3)
        row = self.page.locator('#s1-cards summary').first
        row.get_by_role('button', name='Copy', exact=True).click()
        expect(row.locator('input')).to_be_focused()
        self.assertIsNone(row.locator('..').get_attribute('open'))
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 320)

    def test_request_errors_are_exposed_as_alerts_in_each_workflow(self):
        self.errors['/api/runs'] = 'Cannot load runs'
        self.navigate('runs')
        self.page.locator('#runs-refresh').click()
        expect(self.page.get_by_role('alert')).to_contain_text('Cannot load runs')
        del self.errors['/api/runs']
        self.open_page()
        self.errors['/api/codes'] = 'Cannot list saved codes'
        self.load_run()
        expect(self.page.get_by_role('alert')).to_contain_text('Cannot list saved codes')
        self.assertIn('Cannot list saved codes', self.page.locator('body').aria_snapshot())
        self.errors.clear()
        self.open_page()
        self.load_run()
        self.errors['/api/distinct'] = 'Distinct unavailable'
        self.navigate('distinct')
        self.page.locator('#s2-run').click()
        expect(self.page.get_by_role('alert')).to_contain_text('Distinct unavailable')
        del self.errors['/api/distinct']
        self.page.locator('#s2-run').click()
        expect(self.page.locator('#s3-fields')).to_be_enabled()
        expect(self.page.get_by_role('alert')).to_have_count(0)
        self.errors['/api/decorrelate'] = 'Selection unavailable'
        self.navigate('decorrelate')
        self.page.locator('#s3-run').click()
        expect(self.page.get_by_role('alert')).to_contain_text('Selection unavailable')
        self.assertEqual(self.page.get_by_role('alert').get_attribute('aria-atomic'), 'true')

    def test_sections_preserve_run_filters_analysis_and_scroll_without_requests(self):
        self.load_run()
        self.page.locator('#s1-order').select_option('desc')
        self.page.locator('#s1-next').click()
        self.analyze()
        self.navigate('distinct')
        self.page.locator('#s2-min').fill('3')
        self.page.locator('#s2-next').click()
        requests = list(self.requests)
        self.page.evaluate('window.scrollTo(0, 300)')
        scroll = self.page.evaluate('window.scrollY')
        self.navigate('results')
        expect(self.page.locator('#page-title')).to_have_text('Historical Results')
        expect(self.page.locator('#context-run')).to_contain_text('ABC1234')
        expect(self.page.locator('#nav-results')).to_have_attribute('aria-current', 'page')
        self.navigate('distinct')
        self.assertEqual(self.page.evaluate('window.scrollY'), scroll)
        expect(self.page.locator('#s2-min')).to_have_value('3')
        expect(self.page.locator('#s2-page-info')).to_contain_text('Page 2')
        self.navigate('scan')
        expect(self.page.locator('#s1-order')).to_have_value('desc')
        expect(self.page.locator('#s1-page-info')).to_contain_text('Page 2')
        self.navigate('decorrelate')
        expect(self.page.locator('#s3-cards details')).to_have_count(26)
        self.assertEqual(self.requests, requests, 'Section navigation must not re-run work')
        for _ in range(18):
            self.page.keyboard.press('Tab')
            self.assertFalse(self.page.evaluate('Boolean(document.activeElement.closest("[hidden]"))'))

    def test_url_reload_back_and_forward_restore_run_without_starting_jobs(self):
        self.load_run()
        self.navigate('distinct')
        self.page.locator('#s2-run').click()
        expect(self.page.locator('#s3-fields')).to_be_enabled()
        self.navigate('decorrelate')
        self.page.go_back()
        expect(self.page.locator('#page-title')).to_have_text('Distinct Codes')
        self.page.go_forward()
        expect(self.page.locator('#page-title')).to_have_text('De-correlate')
        self.requests.clear()
        self.page.reload()
        expect(self.page.locator('#context-run')).to_contain_text('ABC1234')
        expect(self.page.locator('#page-title')).to_have_text('De-correlate')
        expect(self.page.locator('#s3-fields')).to_be_disabled()  # no stale persisted analysis
        expect(self.page.locator('#s1-cards details')).to_have_count(25)
        self.assertTrue(any(path == '/api/scan/' + self.registry.run_id for path, _ in self.requests))
        self.assertTrue(all(path in ['/api/runs', '/api/scan/' + self.registry.run_id, '/api/codes']
                            for path, _ in self.requests))

    def test_history_between_runs_clears_analysis_and_restores_correct_context(self):
        second_id = 'run-bet9ja-20261024-061623-b93204b6'
        self.registry.runs[second_id] = dict(self.registry.run, run_id=second_id, seed='XYZ1234')
        self.open_page()
        self.load_run()
        self.analyze()
        self.navigate('runs')
        self.choose_run(second_id)
        self.page.locator('#runs-load').click()
        expect(self.page.locator('#context-run')).to_contain_text('XYZ1234')
        expect(self.page.locator('#s3-fields')).to_be_disabled()
        self.page.go_back()
        expect(self.page.locator('#context-run')).to_contain_text('ABC1234')
        expect(self.page.locator('#page-title')).to_have_text('Saved Runs')
        self.page.go_forward()
        expect(self.page.locator('#context-run')).to_contain_text('XYZ1234')
        expect(self.page.locator('#page-title')).to_have_text('Scan Codes')

    def test_missing_or_mismatched_url_run_is_recoverable_without_stale_content(self):
        run_url = 'http://127.0.0.1:8009/app#distinct?provider=bet9ja&run=' + self.registry.run_id
        for kind in ['missing', 'mismatch']:
            with self.subTest(kind=kind):
                pattern = '**/api/scan/' + self.registry.run_id
                def respond(route):
                    if kind == 'missing':
                        route.fulfill(status=404, json={'error': {'message': 'Run not found', 'code': 'run_not_found'}})
                    else:
                        route.fulfill(json=dict(self.registry.run, provider='sportybet'))
                self.page.route(pattern, respond)
                self.page.goto(run_url)
                expect(self.page.locator('#navigation-error')).to_contain_text('Could not restore this run')
                expect(self.page.locator('#context-run')).to_have_text('No run selected')
                expect(self.page.locator('#s2-fields')).to_be_disabled()
                expect(self.page.locator('#s1-run')).to_be_enabled()
                self.assertNotIn('run=', self.page.url)
                self.page.unroute(pattern, respond)
        self.requests.clear()
        self.page.goto('http://127.0.0.1:8009/app#distinct?provider=bet9ja&run=../../bad')
        expect(self.page.locator('#navigation-error')).to_contain_text('Invalid run link')
        self.assertFalse(any(path.startswith('/api/scan/') for path, _ in self.requests))

    def test_late_url_restore_cannot_overwrite_provider_switch_or_back_navigation(self):
        pending = []
        pattern = '**/api/scan/' + self.registry.run_id
        self.page.route(pattern, lambda route: pending.append(route))
        self.page.goto('http://127.0.0.1:8009/app#distinct?provider=bet9ja&run=' + self.registry.run_id)
        expect(self.page.locator('#context-run')).to_have_text('Loading saved run…')
        expect(self.page.locator('#s1-run')).to_be_disabled()
        self.page.locator('#provider').select_option('sportybet')
        expect(self.page.locator('#context-run')).to_have_text('No run selected')
        pending.pop().fulfill(json=self.registry.run)
        expect(self.page.locator('#provider')).to_have_value('sportybet')
        expect(self.page.locator('#context-run')).to_have_text('No run selected')
        self.assertIn('provider=sportybet', self.page.url)
        self.assertFalse(any(path == '/api/codes' for path, _ in self.requests))
        self.page.go_back()
        expect(self.page.locator('#context-run')).to_have_text('Loading saved run…')
        self.page.go_forward()
        expect(self.page.locator('#provider')).to_have_value('sportybet')
        pending.pop().fulfill(json=self.registry.run)
        expect(self.page.locator('#context-run')).to_have_text('No run selected')

    def test_submission_and_cancellation_survive_section_navigation(self):
        pending = []
        self.page.route('**/api/scan', lambda route: pending.append(route))
        self.page.locator('#fresh_seed').check()
        self.page.locator('#s1-run').click()
        expect(self.page.locator('#context-state')).to_have_text('Submitting scan…')
        self.navigate('distinct')
        self.registry.run['state'] = 'running'
        pending.pop().fulfill(status=202, json=self.registry.run)
        expect(self.page.locator('#context-state')).to_have_text('Running')
        expect(self.page.locator('#page-title')).to_have_text('Distinct Codes')
        expect(self.page.locator('#s2-fields')).to_be_disabled()
        def cancel(route):
            # Cancellation updates registry status too; subsequent polls must
            # observe the same transition as the cancellation response.
            self.registry.run['state'] = 'cancelled_unresolved'
            route.fulfill(json=self.registry.run)
        self.page.route('**/api/scan/*/cancel', cancel)
        self.navigate('scan')
        self.page.locator('#s1-cancel').click()
        expect(self.page.locator('#context-state')).to_have_text('Cancellation pending')
        self.registry.run['state'] = 'cancelled'
        self.navigate('runs')
        expect(self.page.locator('#context-state')).to_have_text('Cancelled', timeout=7000)
        expect(self.page.locator('#s1-run')).to_be_enabled()

    def test_historical_filters_evidence_and_jobs_survive_section_navigation(self):
        run_id = self.registry.run_id
        summary = dict(revision_id='fixture-revision', created_at='2026-10-25T00:00:00Z',
                       population_complete=True, checked_count=27,
                       counts=dict(total=27, won=0, lost=0, void=0, pending=0, unresolved=27))
        rows = [dict(row_id=f'row-{i}', ticket_id=f'ABC{i:04}', checked=True,
                     status='unresolved', reason='source_unavailable',
                     original_ticket=ticket)
                for i, ticket in enumerate(json.loads(self.registry.raw)['qualifying'])]
        result_requests, pending_jobs = [], []

        def respond_results(route):
            url = urlsplit(route.request.url)
            query = parse_qs(url.query)
            result_requests.append((route.request.method, url.path, query,
                                    route.request.post_data_json))
            if '/jobs/' in url.path:
                pending_jobs.append(route)
            elif route.request.method == 'POST':
                route.fulfill(status=202, json={'job_id': 'fixture-job', 'state': 'running'})
            elif url.path.endswith('/codes'):
                offset = int(query['offset'][0])
                route.fulfill(json=dict(total=27, filtered_total=27, population_complete=True,
                                        rows=rows[offset:offset + 25]))
            else:
                route.fulfill(json=summary)

        self.page.route('**/api/results/**', respond_results)
        self.load_run()
        self.navigate('results')
        self.page.locator('#results-open').click()
        expect(self.page.locator('#results-cards article')).to_have_count(25)
        expect(self.page.locator('#results-summary')).to_contain_text('0 lost')
        expect(self.page.locator('#results-summary')).to_contain_text('27 unresolved')
        self.page.locator('#results-filter').select_option('unresolved')
        expect(self.page.locator('#results-page')).to_contain_text('Page 1 of 2')
        self.page.locator('#results-next').click()
        expect(self.page.locator('#results-cards article')).to_have_count(2)
        self.page.locator('#results-cards summary').first.click()
        before = len(result_requests)
        self.navigate('scan')
        self.navigate('results')
        self.assertEqual(len(result_requests), before)
        expect(self.page.locator('#results-filter')).to_have_value('unresolved')
        expect(self.page.locator('#results-page')).to_contain_text('Page 2 of 2')
        expect(self.page.locator('#results-cards details').first).to_have_attribute('open', '')

        # The existing saved-code shortcut should reveal Historical Results and
        # check the full retained population, including the expired ticket.
        self.navigate('scan')
        self.page.locator('#s1-check-results').click()
        expect(self.page.locator('#page-title')).to_have_text('Historical Results')
        expect(self.page.locator('#results-cancel')).to_be_enabled()
        self.navigate('distinct')
        self.page.wait_for_function('document.querySelector("#results-job").textContent.includes("Checking")')
        self.assertEqual(len(pending_jobs), 1)
        pending_jobs.pop().fulfill(json={'job_id': 'fixture-job', 'state': 'succeeded'})
        expect(self.page.locator('#results-job')).to_have_text('Result check succeeded.')
        expect(self.page.locator('#page-title')).to_have_text('Distinct Codes')
        self.navigate('results')
        expect(self.page.locator('#results-page')).to_contain_text('27 saved in run')
        expect(self.page.locator('#results-summary')).to_contain_text('27 unresolved')
        expect(self.page.locator('#results-cancel')).to_be_disabled()
        posts = [(path, body) for method, path, _, body in result_requests if method == 'POST']
        self.assertEqual(posts, [('/api/results/' + run_id, {'refresh': False, 'allow_partial': False})])

    def test_run_table_search_status_counts_and_paging_are_local(self):
        for i in range(30):
            run_id = f'run-bet9ja-20261024-051623-{i:08x}'
            self.registry.runs[run_id] = dict(self.registry.run, run_id=run_id,
                started_at=f'2026-10-24T05:{i:02}:00Z', seed=f'SEED{i:03}',
                state='succeeded' if i % 2 else 'cancelled', extract={'sha256': 'fixture'},
                counters={'qualifying': 900 + i})
        self.open_page()
        self.navigate('runs')
        expect(self.page.locator('#runs-rows tr')).to_have_count(25)
        expect(self.page.locator('#runs-load')).to_be_disabled()
        before = list(self.requests)
        self.page.locator('#runs-next').click()
        expect(self.page.locator('#runs-rows tr')).to_have_count(6)
        self.page.locator('#runs-status').select_option('cancelled')
        expect(self.page.locator('#runs-rows tr')).to_have_count(15)
        # Partial scan counters never masquerade as saved totals.
        self.assertTrue(all(row == '—' for row in self.page.locator('#runs-rows td:nth-child(5)').all_text_contents()))
        self.page.locator('#runs-status').select_option('succeeded')
        self.page.locator('#runs-search').fill('SEED029')
        expect(self.page.locator('#runs-rows tr')).to_have_count(1)
        expect(self.page.locator('#runs-rows td:nth-child(5)')).to_have_text('929')
        self.page.locator('#runs-search').fill('no matches')
        expect(self.page.locator('#runs-note')).to_contain_text('No runs match')
        self.page.locator('#runs-reset').click()
        expect(self.page.locator('#runs-search')).to_be_focused()
        expect(self.page.locator('#runs-rows tr')).to_have_count(25)
        self.assertEqual(self.requests, before)
        self.page.set_viewport_size({'width': 320, 'height': 900})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 320)

    def test_change_run_returns_to_requesting_workflow_with_keyboard_selection(self):
        self.navigate('distinct')
        self.page.locator('#change-run').click()
        radio = self.page.locator('input[name=saved-run]').first
        radio.focus()
        self.page.keyboard.press('Space')
        expect(self.page.locator('#runs-load')).to_be_enabled()
        self.page.locator('#runs-load').click()
        expect(self.page.locator('#page-title')).to_have_text('Distinct Codes')
        expect(self.page.locator('#s2-fields')).to_be_enabled()
        self.navigate('runs')
        expect(self.page.locator('#runs-rows .current-label')).to_be_visible()
        self.page.locator('#runs-rows summary').click()
        expect(self.page.locator('#runs-rows code')).to_have_text(self.registry.run_id)

    def test_run_load_failure_cannot_start_results_for_previous_selection(self):
        self.load_run()
        self.navigate('runs')
        self.errors['/api/scan/' + self.registry.run_id] = 'Run unavailable'
        self.requests.clear()
        self.page.locator('#runs-check-results').click()
        expect(self.page.locator('#s1-error')).to_have_text('Run unavailable (HTTP 503, fixture_error)')
        expect(self.page.locator('#page-title')).to_have_text('Saved Runs')
        expect(self.page.locator('#runs-check-results')).to_be_enabled()
        self.assertFalse(any(path.startswith('/api/results') for path, _ in self.requests))

    def test_empty_history_and_refresh_failure_have_recovery_states(self):
        self.registry.runs.clear()
        self.open_page()
        self.navigate('runs')
        expect(self.page.locator('#runs-note')).to_contain_text('No saved runs')
        expect(self.page.locator('#runs-load')).to_be_disabled()
        expect(self.page.locator('#runs-check-results')).to_be_disabled()
        self.errors['/api/runs'] = 'History unavailable'
        self.page.locator('#runs-refresh').click()
        expect(self.page.locator('#runs-note')).to_contain_text('Refresh list to retry')
        del self.errors['/api/runs']
        self.registry.runs[self.registry.run_id] = self.registry.run
        self.page.locator('#runs-refresh').click()
        expect(self.page.locator('#runs-rows tr')).to_have_count(1)
        expect(self.page.locator('#s1-error')).to_be_hidden()

    def test_mobile_navigation_disclosure_and_focus_remain_usable(self):
        for width in [768, 390, 320]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open_page()
                expect(self.page.locator('#app-sidebar')).to_be_hidden()
                expect(self.page.locator('#nav-toggle')).to_have_attribute('aria-expanded', 'false')
                self.page.locator('#nav-toggle').focus()
                self.page.keyboard.press('Enter')
                expect(self.page.locator('#app-sidebar')).to_be_visible()
                self.page.locator('#nav-runs').focus()
                self.page.keyboard.press('Enter')
                expect(self.page.locator('#app-sidebar')).to_be_hidden()
                expect(self.page.locator('#page-title')).to_be_focused()
                expect(self.page.locator('#saved-runs')).to_be_visible()
                expect(self.page.locator('#s1')).to_be_hidden()
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)


if __name__ == '__main__':
    unittest.main()
