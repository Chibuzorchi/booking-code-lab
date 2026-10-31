"""One copy helper, inlined everywhere, with a fallback that actually runs."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from html.parser import HTMLParser

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


class ConsoleAssetTests(unittest.TestCase):
    def test_console_inlines_each_source_once_in_execution_order(self):
        html = app_page()
        css = (PORTFOLIO / 'app.css').read_text(encoding='utf-8')
        js = (PORTFOLIO / 'app.js').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'<style>(.*?)</style>', html, re.S), [css])
        self.assertEqual(pages(html), [copy_js(), js])
        for marker in ('__APP_CSS__', '__APP_JS__', PLACEHOLDER):
            self.assertNotIn(marker, html)
        external_assets = []
        class Assets(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if (tag == 'script' and 'src' in attrs) or (tag == 'link' and attrs.get('rel') == 'stylesheet'):
                    external_assets.append(attrs)
        Assets().feed(html)
        self.assertEqual(external_assets, [])

    def test_console_refuses_missing_or_duplicate_asset_slots(self):
        from portfolio.pages import _asset
        for marker in ('__APP_CSS__', '__APP_JS__'):
            for replacement in ('', marker + marker):
                with self.subTest(marker=marker, replacement=replacement):
                    def asset(name):
                        text = _asset(name)
                        return text.replace(marker, replacement) if name == 'app.html' else text
                    with patch('portfolio.pages._asset', side_effect=asset):
                        with self.assertRaisesRegex(ValueError, 'exactly one'):
                            app_page()

    def test_console_assets_cannot_close_their_inline_elements(self):
        from portfolio.pages import _asset
        for name, closing in (('app.css', '</STYLE>'), ('app.js', '</SCRIPT>')):
            with self.subTest(asset=name):
                with patch('portfolio.pages._asset', side_effect=lambda asset: closing if asset == name else _asset(asset)):
                    with self.assertRaisesRegex(ValueError, 'cannot be inlined'):
                        app_page()


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

    def test_console_offers_a_fresh_code_instead_of_a_typed_seed(self):
        html = app_page()
        self.assertIn('id="fresh_seed" type="checkbox"', html)
        self.assertIn("$('seed').disabled = fresh;", html)
        self.assertIn("if ($('fresh_seed').checked) body.fresh_seed = true;", html)
        self.assertIn('never stakes money', html)

    def test_console_offers_an_order_and_band_over_the_distinct_list(self):
        html = app_page()
        for control in ('id="s2-order"', 'id="s2-min"', 'id="s2-max"', 'id="s2-view-reset"'):
            self.assertIn(control, html)
        self.assertIn('Lowest odds first', html)
        self.assertIn('Highest odds first', html)
        # It is a view: no band ever reaches the API, and stage 3 says so on the page.
        self.assertIn("not on whatever stage 2's order/band controls are showing", html)
        self.assertNotIn("min_odds: readPrefs", html)
        self.assertIn('Copy shown codes', html)

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

    def test_historical_checks_keep_population_and_revision_across_filtering_and_navigation(self):
        seen = self.seen['historical_results']
        self.assertEqual(seen['before']['cards'], 25)
        self.assertIn('26 saved codes', seen['before']['summary'])
        self.assertEqual(seen['rowSelector'], 'digest:0')
        self.assertTrue(seen['focusedRow'])
        self.assertIn('1 checked', seen['checked']['summary'])
        self.assertIn('(1/26)', seen['checked']['rates'])
        self.assertTrue(seen['pinned'])
        self.assertEqual(seen['finalPage'], 1)
        self.assertTrue(seen['filtering'])
        self.assertTrue(seen['unchanged'])
        self.assertEqual(seen['staleError'], '')
        self.assertTrue(seen['hidden'])
        self.assertIn('RUN_B', seen['run'])

    def test_saved_code_cards_offer_individual_and_full_run_checks(self):
        seen = self.seen['direct_saved_code_actions']
        self.assertEqual(seen['selected'], seen['expected'])
        self.assertNotIn('row_id', seen['allBody'])
        self.assertTrue(seen['copyPresent'])
        self.assertIn('saved codes', seen['label'])

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

    def test_fresh_seed_box_replaces_the_seed_field_and_reports_booking(self):
        seen = self.seen['fresh_seed']
        # Exactly one of the two controls supplies the seed.
        self.assertFalse(seen['before']['seed_disabled'])
        self.assertTrue(seen['ticked']['seed_disabled'])
        self.assertEqual('booked for you when the run starts', seen['ticked']['hint'])
        self.assertFalse(seen['unticked']['seed_disabled'])
        self.assertEqual('7 characters, case kept', seen['unticked']['hint'])
        # The request says fresh_seed and carries no seed at all.
        self.assertEqual({'provider': 'bet9ja', 'fresh_seed': True}, seen['body'])
        # A null seed on a live run reads as "still booking", never as "no seed".
        self.assertIn('booking a fresh code in the browser', seen['while_booking']['state_line'])
        self.assertEqual('booking…', seen['while_booking']['seed_row'])
        self.assertIn('seed BK9JA07', seen['booked']['state_line'])
        self.assertEqual('BK9JA07', seen['booked']['seed_row'])
        self.assertTrue(seen['booked']['polling'])       # booked, still scanning
        # A finished run that never got one says so, and shows the reason.
        self.assertEqual('never booked', seen['finished']['seed_row'])
        self.assertIn('fresh-seed booking failed: no code', seen['finished']['state_line'])
        self.assertFalse(seen['finished']['polling'])

    def test_stage2_list_can_be_ordered_and_banded_without_re_analysing(self):
        seen = self.seen['distinct_view']
        # Default: lowest odds first, everything listed, unpriced last.
        self.assertEqual(['LOW111', 'MID222', 'HIGH33', 'NOPRICE'], seen['initial']['s2_shown'])
        self.assertIn('Listed lowest odds first', seen['initial']['s2_note'])
        # Highest first reverses the priced codes and leaves unpriced at the end.
        self.assertEqual(['HIGH33', 'MID222', 'LOW111', 'NOPRICE'], seen['highest']['s2_shown'])
        self.assertIn('Listed highest odds first', seen['highest']['s2_note'])
        # A band narrows the list; an unpriced code cannot match one, and it says so.
        self.assertEqual(['MID222'], seen['banded']['s2_shown'])
        self.assertIn('Showing 1 of 4 with odds 10 to 1,000', seen['banded']['s2_note'])
        self.assertIn('1 unpriced code(s) cannot match a band', seen['banded']['s2_note'])
        # Copying takes exactly what is on screen, never the whole analysis.
        for case in ('initial', 'highest', 'banded'):
            with self.subTest(case=case):
                self.assertEqual(seen[case]['s2_shown'], seen[case]['copied'].split('\n'))
        # A band nothing reaches is empty and uncopyable, not silently ignored.
        self.assertEqual([], seen['empty']['s2_shown'])
        self.assertTrue(seen['empty']['copy_disabled'])
        self.assertIn('"From odds" is above "To odds"', seen['inverted']['s2_note'])
        self.assertEqual(['LOW111', 'MID222', 'HIGH33', 'NOPRICE'], seen['reset']['s2_shown'])

    def test_stage2_list_view_changes_nothing_the_analyses_read(self):
        seen = self.seen['distinct_view']
        # Stage 3 ran while stage 2 showed 2 of 4, and still checked all 4.
        self.assertEqual(['LOW111', 'MID222'], seen['with_stage3']['s2_shown'])
        self.assertIn('all 4 fully-distinct code(s)', seen['with_stage3']['subset'])
        # The request carries no band: the API is never asked to re-slice the pool.
        self.assertEqual({'run_id': 'RUN_A', 'max_exposure': 1}, seen['with_stage3']['request_body'])

    def test_stage2_band_belongs_to_its_provider_and_not_to_the_next_run(self):
        seen = self.seen['distinct_view']['kept']
        self.assertEqual('', seen['away']['max'])              # the other provider is clean
        self.assertEqual(0, seen['away']['s2_cards'])
        self.assertEqual('1000', seen['back']['max'])          # coming back is where you left
        self.assertEqual(['LOW111', 'MID222'], seen['back']['s2_shown'])
        self.assertEqual('', self.seen['distinct_view']['new_run']['max'])  # a new run starts unfiltered

    def test_succeeded_run_lists_every_code_before_stage2_is_asked(self):
        seen = self.seen['all_codes']
        # While the scan runs, the codes saved so far are listed and said to be partial.
        self.assertEqual(['LOW111', 'MID222'], seen['scanning']['shown'])
        self.assertIn('2 code(s) saved so far', seen['scanning']['note'])
        self.assertIn('still running', seen['scanning']['note'])
        # A poll that brings no new qualifying code does not ask again.
        self.assertEqual(0, seen['unchanged']['asks'])
        # The status that shows it succeeded asks once more, by run id alone.
        self.assertEqual(1, seen['asked']['asks'])
        self.assertEqual({'run_id': 'RUN_A'}, seen['asked']['body'])
        # Every code is listed, overlapping ones included, with stage 2 untouched.
        listed = seen['listed']
        self.assertFalse(listed['hidden'])
        self.assertEqual(['LOW111', 'TWIN44', 'TWIN55', 'MID222', 'HIGH33', 'NOPRICE'], listed['shown'])
        self.assertIn("6 upcoming code(s) available for analysis; 6 saved code(s) in this run, overlapping ones included", listed['note'])
        self.assertEqual(listed['shown'], listed['copied'].split('\n'))
        self.assertEqual(0, listed['s2_cards'])
        self.assertFalse(listed['s2_locked'])

    def test_stage2_list_can_be_banded_by_number_of_legs(self):
        seen = self.seen['legs_view']
        self.assertEqual(['LOW111', 'MID222', 'NOPRICE'], seen['at_most_5']['shown'])
        self.assertIn('Showing 3 of 4 with up to 5 legs', seen['at_most_5']['note'])
        self.assertEqual(seen['at_most_5']['shown'], seen['at_most_5']['copied'].split('\n'))
        self.assertEqual(['MID222', 'NOPRICE'], seen['four_to_five']['shown'])
        self.assertIn('4 to 5 legs', seen['four_to_five']['note'])
        self.assertEqual(['MID222'], seen['with_odds']['shown'])
        self.assertIn('odds from 100 up and 4 to 5 legs', seen['with_odds']['note'])
        self.assertEqual([], seen['inverted']['shown'])
        self.assertIn('"Min legs" is above "Max legs"', seen['inverted']['note'])
        self.assertEqual(4, len(seen['reset']['shown']))
        self.assertEqual(('', ''), (seen['reset']['min'], seen['reset']['max']))

    def test_all_codes_list_has_its_own_order_and_band(self):
        seen = self.seen['all_codes']
        self.assertEqual(['HIGH33', 'MID222', 'TWIN55', 'TWIN44', 'LOW111', 'NOPRICE'], seen['highest']['shown'])
        self.assertEqual(['LOW111', 'TWIN44', 'TWIN55', 'MID222'], seen['banded']['shown'])
        self.assertIn('Showing 4 of 6 with odds up to 1,000', seen['banded']['note'])
        self.assertEqual(seen['banded']['shown'], seen['banded']['copied'].split('\n'))
        # Banding this list does not band stage 2's.
        self.assertEqual(['LOW111', 'MID222', 'HIGH33', 'NOPRICE'], seen['beside_stage2']['s2'])
        self.assertEqual('', seen['beside_stage2']['s2_max'])
        self.assertEqual(seen['banded']['shown'], seen['beside_stage2']['s1'])
        self.assertEqual(6, len(seen['reset']['shown']))

    def test_expired_history_can_be_revealed_copied_and_hidden(self):
        seen = self.seen['expired_history']
        self.assertEqual(seen['hidden']['cards'], 0)
        self.assertTrue(seen['hidden']['available'])
        self.assertIn('26 saved code(s) hidden', seen['hidden']['note'])
        self.assertEqual(seen['revealed']['cards'], 25)
        self.assertEqual(seen['revealed']['page'], 'Page 1 of 2 (25 per page)')
        self.assertIn('Scheduled kickoff has passed.', seen['revealed']['label'])
        self.assertEqual(seen['revealed']['pressed'], 'true')
        self.assertEqual(seen['copied'], 26)
        self.assertEqual(seen['finalPage'], 1)
        self.assertEqual(seen['rehidden'], 0)
        self.assertEqual(seen['restored'], 'true')
        self.assertTrue(seen['reset'])
        self.assertFalse(any('/api/distinct' in r or '/api/decorrelate' in r for r in seen['requests']))

    def test_all_codes_list_follows_its_provider_and_its_run(self):
        seen = self.seen['all_codes']
        # The other provider shows none of it; coming back repaints without refetching.
        self.assertTrue(seen['away']['hidden'])
        self.assertEqual([], seen['away']['shown'])
        self.assertEqual(['LOW111', 'TWIN44', 'TWIN55', 'MID222'], seen['back']['shown'])
        self.assertEqual('1000', seen['back']['max'])
        self.assertEqual(0, seen['back']['asks'])
        # Loading another run drops the old list at once and asks for the new one.
        self.assertTrue(seen['replaced']['hidden'])
        self.assertEqual([], seen['replaced']['shown'])
        self.assertEqual(1, seen['replaced']['asks'])
        # An empty pool is said, not shown as a blank.
        self.assertIn('found no codes in its odds band', seen['none']['note'])
        self.assertIn('1 extract row(s) were excluded', seen['none']['note'])
        self.assertTrue(seen['none']['copy_disabled'])
        self.assertTrue(seen['none']['controls_hidden'])
        # A run that failed keeps the codes it had saved, and says it stopped early.
        self.assertEqual(['LOW111', 'MID222'], seen['failed_run']['shown'])
        self.assertIn('stopped early (failed)', seen['failed_run']['note'])
        self.assertIn('2 code(s) were saved before it stopped', seen['failed_run']['note'])
        # One with nothing saved shows nothing and raises no error.
        self.assertTrue(seen['nothing_saved']['hidden'])
        self.assertEqual('', seen['nothing_saved']['error'])
        # A succeeded run owes its list, so a refusal there is reported.
        self.assertIn('Cannot parse committed extract', seen['refused']['error'])
        self.assertEqual([], seen['refused']['shown'])

    def test_submission_failure_is_restored_to_its_own_provider(self):
        case = self.seen['submission_switches']['failed']
        self.assertTrue(case['noOtherPaint'])
        self.assertIn('Launch refused', case['error'])
        self.assertFalse(case['run_disabled'] or case['polling'])


if __name__ == '__main__':
    unittest.main()
