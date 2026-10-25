"""Chromium regressions; all network requests are intercepted, no provider traffic.

Run: python3 -m unittest discover -s tests -p test_browser_ui.py -v
Optional: pip install playwright && python3 -m playwright install chromium
Missing Playwright skips this class; an installed but broken browser fails.
"""
import json
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
        rows = [coupon(f'ABC{i:04}', f'Fixture {i}', 'bet9ja') for i in range(27)]
        for row in rows:
            row['selections'][0]['kickoff'] = '9999-01-02T00:00:00Z'
        rows[-1]['selections'][0]['kickoff'] = '2000-01-01T00:00:00Z'
        self.raw = json.dumps(dict(provider='bet9ja', qualifying=rows,
                                   odds_basis='parsed_leg_product',
                                   min_total_odds=1, max_total_odds=350000)).encode()

    def list_runs(self, provider=None):
        return [self.run] if provider in (None, 'bet9ja') else []

    def get(self, run_id):
        assert run_id == self.run_id
        return self.run

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
        self.page.locator('#s1-history summary').click()
        self.page.locator('#runs').select_option(self.registry.run_id)
        self.page.locator('#runs-load').click()
        expect(self.page.locator('#s2-fields')).to_be_enabled()

    def analyze(self):
        self.page.locator('#s2-run').click()
        expect(self.page.locator('#s3-fields')).to_be_enabled()
        self.page.locator('#s3-run').click()
        expect(self.page.locator('#s3-cards details')).to_have_count(26)

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
                background:getComputedStyle(e.closest('.panel')).backgroundColor})""")
            self.assertGreaterEqual(self.contrast(colors['border'], colors['background']), 3)

    def assert_row_usable(self, row):
        button = row.locator('button')
        geometry = row.evaluate("""el => {
            const r=el.getBoundingClientRect(), b=el.querySelector('button'), q=b.getBoundingClientRect();
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
                    self.assert_row_usable(self.page.locator(f'#{stage}-cards summary').first)
                self.page.locator('#s1-expired').click()
                self.page.locator('#s1-next').click()
                historical = self.page.locator('#s1-cards summary').filter(has_text='outcome not checked')
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
        row.locator('button').click()
        expect(row.locator('input')).to_be_focused()
        self.assertIsNone(row.locator('..').get_attribute('open'))
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 320)

    def test_request_errors_are_exposed_as_alerts_in_each_workflow(self):
        self.errors['/api/runs'] = 'Cannot load runs'
        self.page.locator('#s1-history summary').click()
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
        self.page.locator('#s2-run').click()
        expect(self.page.get_by_role('alert')).to_contain_text('Distinct unavailable')
        del self.errors['/api/distinct']
        self.page.locator('#s2-run').click()
        expect(self.page.locator('#s3-fields')).to_be_enabled()
        expect(self.page.get_by_role('alert')).to_have_count(0)
        self.errors['/api/decorrelate'] = 'Selection unavailable'
        self.page.locator('#s3-run').click()
        expect(self.page.get_by_role('alert')).to_contain_text('Selection unavailable')
        self.assertEqual(self.page.get_by_role('alert').get_attribute('aria-atomic'), 'true')


if __name__ == '__main__':
    unittest.main()
