"""One copy helper, inlined everywhere, with a fallback that actually runs."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import portfolio
from portfolio.distinct_page import render_html
from portfolio.pages import PLACEHOLDER, app_page, copy_js, inline_copy_js
from portfolio.reporting import render

PORTFOLIO = Path(portfolio.__file__).resolve().parent
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
PROBE = FIXTURES / 'copy_probe.js'
CONSOLE_PROBE = FIXTURES / 'console_probe.js'
NODE = shutil.which('node')
MARKER = 'const Copy = (() =>'          # the helper's own first line


def pages(html):
    """Every <script> body in a rendered page, so duplication cannot hide."""
    return re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)


class HelperSourceTests(unittest.TestCase):
    def test_copy_js_is_the_only_clipboard_implementation(self):
        owners = {'navigator.clipboard': [], 'execCommand': []}
        for source in sorted(PORTFOLIO.glob('*.py')) + sorted(PORTFOLIO.glob('*.html')) + sorted(PORTFOLIO.glob('*.js')):
            text = source.read_text(encoding='utf-8')
            for api in owners:
                if api in text:
                    owners[api].append(source.name)
        self.assertEqual(owners, {'navigator.clipboard': ['copy.js'], 'execCommand': ['copy.js']},
                         'clipboard code belongs in copy.js only')

    def test_helper_covers_all_three_paths_and_cannot_break_out_of_a_script(self):
        source = copy_js()
        for path in ('navigator.clipboard', "document.execCommand('copy')", 'field.select()'):
            self.assertIn(path, source)
        self.assertNotIn('</script', source.lower())

    def test_every_generated_page_inlines_the_one_helper_exactly_once(self):
        distinct = render_html('bet9ja', {'odds_basis': 'parsed_leg_product', 'distinct': [], 'coupons': 0,
                                          'overlapping_codes': 0, 'min_odds': 5000, 'max_odds': 350000})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'extracts').mkdir()
            (root / 'extracts' / 'scan_20260101-120000.json').write_text(json.dumps({
                'provider': 'bet9ja', 'qualifying': [{'code': 'ABC1234', 'selections': [
                    {'event_id': 1, 'event': 'A - B', 'market': '1X2', 'pick': 'Home', 'odds': 2}]}]}))
            render(root, 'bet9ja')
            report = (root / 'index.html').read_text(encoding='utf-8')
        for name, html in (('report', report), ('distinct', distinct), ('console', app_page())):
            with self.subTest(page=name):
                self.assertEqual(html.count(MARKER), 1)
                self.assertNotIn(PLACEHOLDER, html)
                # the helper lives in a <script> of its own, and only there
                bodies = [s for s in pages(html) if MARKER in s]
                self.assertEqual(len(bodies), 1)
                self.assertEqual(bodies[0].strip(), copy_js().strip())
                self.assertIn('Copy.button(', html)

    def test_a_page_without_the_placeholder_is_refused(self):
        with self.assertRaises(ValueError):
            inline_copy_js('<html><script>Copy.button(b, x)</script></html>')


@unittest.skipUnless(NODE, 'node is required to execute the copy helper')
class HelperBehaviourTests(unittest.TestCase):
    """Runs copy.js against a DOM stub: the fallback is exercised, not assumed."""

    @classmethod
    def setUpClass(cls):
        done = subprocess.run([NODE, str(PROBE)], capture_output=True, text=True, timeout=60)
        if done.returncode:
            raise AssertionError(f'copy probe failed: {done.stderr}')
        cls.report = json.loads(done.stdout)

    def test_clipboard_path_copies_and_restores_the_button(self):
        case = self.report['clipboard']
        self.assertTrue(case['returned'])
        self.assertEqual(case['wrote'], ['5SH7HBJ'])
        self.assertEqual(case['label'], 'Copied')
        self.assertIsNone(case['field'])                      # no fallback when it worked
        self.assertEqual(case['label_after_flash'], 'Copy')   # original label never lost
        self.assertEqual(case['classes_after_flash'], [])

    def test_denied_clipboard_falls_through_to_exec_command_and_leaves_no_host(self):
        case = self.report['exec_command']
        self.assertTrue(case['returned'])
        self.assertEqual(case['label'], 'Copied')
        self.assertEqual(case['body_children'], ['BUTTON'])   # the off-screen host is cleaned up

    def test_per_code_copy_offers_selectable_text_when_both_paths_fail(self):
        case = self.report['fallback']
        self.assertFalse(case['returned'])
        field = case['field']
        self.assertIsNotNone(field, 'a failed copy must still expose the code')
        self.assertEqual(field['value'], '5SH7HBJ')
        self.assertEqual(field['tag'], 'INPUT')
        self.assertTrue(field['read_only'])
        self.assertTrue(field['focused'])
        self.assertTrue(field['selected'])
        self.assertEqual(field['aria_label'], 'Booking code to copy')
        # the field can sit inside a <summary>: its own clicks/keys must not toggle the card
        self.assertEqual(field['stops'], ['click', 'keydown'])
        self.assertTrue(case['shortcut_named'])
        self.assertEqual(case['fields_after_second_click'], 1)

    def test_empty_and_text_only_calls_never_claim_success(self):
        self.assertFalse(self.report['empty']['returned'])
        self.assertEqual(self.report['empty']['label'], 'Nothing to copy')
        self.assertIsNone(self.report['empty']['field'])
        self.assertFalse(self.report['text_only']['returned'])
        self.assertEqual(self.report['text_only']['body_children'], ['BUTTON'])


@unittest.skipUnless(NODE, 'node is required to parse the page scripts')
class PageScriptTests(unittest.TestCase):
    def test_every_inlined_script_parses(self):
        distinct = render_html('sportybet', {'odds_basis': 'parsed_leg_product', 'distinct': [], 'coupons': 0,
                                             'overlapping_codes': 0, 'min_odds': 1, 'max_odds': None})
        template = (PORTFOLIO / 'reporting.html').read_text(encoding='utf-8')
        report = inline_copy_js(template).replace('__CATALOG__', '{"runs":[],"provider":"bet9ja"}')
        with tempfile.TemporaryDirectory() as directory:
            for name, html in (('report', report), ('distinct', distinct), ('console', app_page())):
                for index, script in enumerate(pages(html)):
                    if not script.strip() or script.strip().startswith('{'):
                        continue                                  # the JSON catalog element
                    path = Path(directory) / f'{name}-{index}.js'
                    path.write_text(script, encoding='utf-8')
                    done = subprocess.run([NODE, '--check', str(path)], capture_output=True, text=True)
                    self.assertEqual(done.returncode, 0, f'{name} script {index}: {done.stderr}')

    def test_console_cannot_send_an_exposure_below_one(self):
        html = app_page()
        self.assertIn('id="max_exposure" type="number" min="1" step="1" value="1" required', html)
        self.assertIn('Max exposure must be a whole number of at least 1', html)
        self.assertIn("if (Number($('max_exposure').value) < 1) $('max_exposure').value = '1';", html)

    def test_console_labels_odds_and_names_the_three_stages(self):
        html = app_page()
        self.assertIn('recorded, not verified payout', html)
        self.assertIn('report.odds_label', html)
        for route in ('/api/scan', '/api/runs', '/api/distinct', '/api/decorrelate'):
            self.assertIn(route, html)
        self.assertIn('full eligible pool', html)


@unittest.skipUnless(NODE, 'node is required to execute the console script')
class ConsoleStateTests(unittest.TestCase):
    """Runs the console script itself through the transitions that break state.

    Each scenario resolves a request by hand *after* the user has moved on, which is
    the only way to see a late reply painting over the current run. A syntax check or
    a source grep cannot observe any of this.
    """

    @classmethod
    def setUpClass(cls):
        done = subprocess.run([NODE, str(CONSOLE_PROBE)], capture_output=True, text=True, timeout=120)
        if done.returncode:
            raise AssertionError(f'console probe failed: {done.stderr}')
        cls.seen = json.loads(done.stdout)

    def test_a_late_stage_2_reply_cannot_become_another_run_s_analysis(self):
        after = self.seen['stale_stage2']
        self.assertIn('RUN_B', after['state_line'])
        self.assertEqual(0, after['s2_cards'])      # run A's distinct codes are discarded
        self.assertTrue(after['s3_locked'])         # so stage 3 stays shut

    def test_a_late_poll_cannot_replace_the_run_on_screen(self):
        after = self.seen['stale_poll']
        self.assertIn('RUN_B', after['state_line'])
        self.assertNotIn('RUN_A', after['state_line'])
        self.assertFalse(after['polling'])          # B is terminal: A's poller is gone

    def test_a_late_selection_reply_is_not_rendered(self):
        after = self.seen['stale_stage3']
        self.assertIn('RUN_B', after['state_line'])
        self.assertEqual(0, after['s3_cards'])
        self.assertTrue(after['s3_facts_hidden'])

    def test_a_late_cancellation_reply_cannot_resurrect_that_run(self):
        after = self.seen['stale_cancel']
        self.assertIn('RUN_B', after['state_line'])
        self.assertNotIn('cancelled', after['state_line'])

    def test_only_one_poll_is_ever_in_flight(self):
        self.assertEqual(1, self.seen['no_overlapping_polls']['in_flight'])

    def test_switching_provider_clears_then_restores_that_provider_s_state(self):
        seen = self.seen['provider_switch']
        self.assertEqual((1, 1), (seen['before']['s2_cards'], seen['before']['s3_cards']))
        self.assertEqual('/api/runs?provider=sportybet', seen['history_provider'])
        away = seen['away']
        self.assertEqual('No run yet.', away['state_line'])
        self.assertEqual((0, 0), (away['s2_cards'], away['s3_cards']))
        self.assertTrue(away['s2_locked'] and away['s3_locked'])
        back = seen['back']                         # the run and both analyses come back
        self.assertIn('RUN_A', back['state_line'])
        self.assertEqual((1, 1), (back['s2_cards'], back['s3_cards']))
        self.assertFalse(back['s2_locked'] or back['s3_locked'])

    def test_a_run_owned_for_one_provider_does_not_block_the_other(self):
        seen = self.seen['provider_switch_running']
        self.assertTrue(seen['held']['run_disabled'])        # bet9ja is busy...
        self.assertTrue(seen['held']['polling'])
        self.assertFalse(seen['away']['run_disabled'])       # ...sportybet is not
        self.assertFalse(seen['away']['polling'])            # and A's poller stopped

    def test_unresolved_cancellation_is_not_treated_as_an_end_state(self):
        # registry: cancelled_unresolved -> cancelled once the worker is gone, and the
        # provider stays owned until then, so a second scan would be refused with 409.
        seen = self.seen['unresolved_cancel']
        held = seen['unresolved']
        self.assertTrue(held['polling'])
        self.assertTrue(held['run_disabled'])
        self.assertTrue(seen['kept_asking'])        # it polled again, unprompted
        settled = seen['settled']
        self.assertIn('is cancelled.', settled['state_line'])
        self.assertFalse(settled['polling'])
        self.assertFalse(settled['run_disabled'])

    def test_a_new_run_that_arrives_succeeded_inherits_no_analysis(self):
        seen = self.seen['immediate_success']
        self.assertEqual(1, seen['before']['s2_cards'])
        after = seen['after']
        self.assertIn('RUN_B', after['state_line'])
        self.assertEqual(0, after['s2_cards'])
        self.assertTrue(after['s3_locked'])

    def test_history_is_cleared_and_loaded_run_provider_is_verified(self):
        case = self.seen['history_switch_guard']
        self.assertTrue(case['locked'])
        self.assertTrue(case['cleared'])
        self.assertTrue(case['rejected'])
        self.assertEqual('No run yet.', case['state_line'])

    def test_stale_analysis_cleanup_cannot_unlock_another_request(self):
        for stage, case in self.seen['analysis_request_ownership'].items():
            with self.subTest(stage=stage):
                self.assertTrue(case['stillBusy'])
                self.assertEqual(0, case['duplicates'])
                self.assertTrue(case['released'])

    def test_submission_survives_switching_away_and_returning_before_reply(self):
        for timing in ('away', 'returned'):
            with self.subTest(timing=timing):
                case = self.seen['submission_switches'][timing]
                self.assertTrue(case['otherEnabled'] and case['pendingLocked'] and case['noOtherPaint'])
                self.assertIn('RUN_NEW', case['state_line'])
                self.assertTrue(case['run_disabled'] and case['polling'])

    def test_submission_failure_is_restored_to_its_own_provider(self):
        case = self.seen['submission_switches']['failed']
        self.assertTrue(case['noOtherPaint'])
        self.assertIn('Launch refused', case['error'])
        self.assertFalse(case['run_disabled'] or case['polling'])


if __name__ == '__main__':
    unittest.main()
