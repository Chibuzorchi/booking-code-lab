"""HTTP-level tests for the /api/results/... routes, through the real
localhost handler (make_handler) and a real RunRegistry -- no network, no
API-Football credentials. These exercise identifier validation and
traversal resistance that unit-level calls into results_api.py functions
cannot observe, since those never go through api.py's path parsing.

Revisions are seeded directly via publish_revision() against the same
ResultsApiConfig the server uses, rather than through a full submit ->
fetch -> evaluate pipeline: that keeps these tests about route/identifier
safety, independent of matching/evidence-conversion correctness (covered
elsewhere).
"""
import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from portfolio.registry import STATE_SUCCEEDED
from portfolio.results_tracking.results_api import publish_revision
from portfolio.serve import ResultsContext, make_handler
from test_registry import RegistryTestBase


def _report(total=1):
    return {"tickets": [{"ticket_id": "C1", "row_id": "r0", "status": "unresolved"}],
            "counts": {"won": 0, "lost": 0, "void": 0, "pending": 0, "unresolved": total, "total": total},
            "percentages": {}, "rule_versions": {}}


class ResultsRouteTestBase(RegistryTestBase):
    def setUp(self):
        super().setUp()
        self.reg = self.registry()
        self.results_context = ResultsContext(self.reg)
        self.server = ThreadingHTTPServer(
            ('127.0.0.1', 0),
            make_handler(self.workspace, 'bet9ja', self.reg, results_context=self.results_context))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        data = json.dumps(body if body is not None else {}) if method == 'POST' else None
        try:
            conn.request(method, path, data, {'Content-Type': 'application/json'})
            response = conn.getresponse()
            content = response.read()
            return response.status, json.loads(content) if content else None
        finally:
            conn.close()

    def committed_run(self, provider='bet9ja', seed='AbC1234'):
        status, run = self.request('POST', '/api/scan', {'provider': provider, 'seed': seed})
        self.assertEqual(status, 202, run)
        run_id = run['run_id']
        final = self.wait_state(self.reg, run_id, {STATE_SUCCEEDED})
        self.assertEqual(final['state'], STATE_SUCCEEDED)
        return run_id

    def seed_revision(self, run_id, total=1):
        return publish_revision(self.results_context.config, run_id, {"fixtures": []}, _report(total=total),
                                 input_digest=f"digest-{run_id}", config_digest="config-x")

    def wait_for_terminal_job(self, job_id, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, record = self.request('GET', f'/api/results/jobs/{job_id}')
            self.assertEqual(status, 200, record)
            if record['state'] in ('succeeded', 'failed', 'cancelled'):
                return record
            time.sleep(0.05)
        self.fail(f'job {job_id} never reached a terminal state; last: {record}')


class HistoricalResultsRouteTests(ResultsRouteTestBase):
    def test_saved_population_and_individual_check_through_real_transport(self):
        run_id = self.committed_run()
        status, saved = self.request('GET', f'/api/results/{run_id}/codes?view=saved&limit=1')
        self.assertEqual(status, 200, saved)
        self.assertGreater(saved['total'], 0)
        self.assertEqual(saved['rows'][0]['reason'], 'not_checked')
        self.assertIn('selections', saved['rows'][0]['original_ticket'])
        row_id = saved['rows'][0]['row_id']
        status, job = self.request('POST', f'/api/results/{run_id}', {'row_id': row_id})
        self.assertEqual(status, 202, job)
        final = self.wait_for_terminal_job(job['job_id'])
        self.assertEqual(final['state'], 'succeeded', final)
        status, summary = self.request('GET', f'/api/results/{run_id}')
        self.assertEqual(status, 200)
        self.assertEqual(summary['counts']['total'], saved['total'])
        self.assertEqual(summary['checked_count'], 1)
        revision = summary['revision_id']
        status, focused = self.request('GET', f'/api/results/{run_id}/codes?revision={revision}&row_id={row_id}')
        self.assertEqual(status, 200, focused)
        self.assertEqual(focused['total'], saved['total'])
        self.assertEqual(focused['filtered_total'], 1)
        self.assertEqual(focused['rows'][0]['row_id'], row_id)
        status, page = self.request('GET', f'/api/results/{run_id}/codes?revision={revision}&status=won')
        self.assertEqual(status, 200)
        self.assertEqual(page['total'], saved['total'])
        self.assertEqual(page['filtered_total'], 0)

    def test_untrusted_row_selectors_and_snapshot_changes_are_rejected(self):
        run_id = self.committed_run()
        for value, expected in [(False, 400), (None, 400), ('foreign-row', 422)]:
            status, _ = self.request('POST', f'/api/results/{run_id}', {'row_id': value})
            self.assertEqual(status, expected)
        status, _ = self.request('GET', f'/api/results/{run_id}/codes?view=saved&snapshot=changed')
        self.assertEqual(status, 422)


class CrossRunTraversalTests(ResultsRouteTestBase):
    def test_codes_revision_cannot_cross_into_another_run(self):
        run_a = self.committed_run(seed='AbC1234')
        run_b = self.committed_run(seed='AbC1235')
        revision_a = self.seed_revision(run_a)

        status, body = self.request(
            'GET', f'/api/results/{run_b}/codes?revision=../{run_a}/{revision_a}')
        self.assertNotEqual(status, 200, body)
        self.assertEqual(status, 404, body)
        self.assertEqual(body['error']['code'], 'report_not_found')

    def test_codes_rejects_a_malformed_revision_id_outright(self):
        run_a = self.committed_run(seed='AbC1234')
        for bogus in ('../../etc/passwd', '..', 'not-a-real-shape'):
            status, body = self.request(
                'GET', f'/api/results/{run_a}/codes?revision={bogus}')
            self.assertEqual(status, 404, (bogus, body))
            self.assertEqual(body['error']['code'], 'report_not_found', (bogus, body))

    def test_codes_rejects_an_empty_revision_as_a_bad_request(self):
        run_a = self.committed_run(seed='AbC1234')
        status, body = self.request('GET', f'/api/results/{run_a}/codes?revision=')
        self.assertEqual(status, 400, body)

    def test_job_id_with_embedded_slashes_is_an_unknown_route(self):
        status, body = self.request('GET', '/api/results/jobs/../../etc')
        self.assertEqual(status, 404, body)

    def test_single_segment_malformed_job_id_is_not_found(self):
        status, body = self.request('GET', '/api/results/jobs/not-a-real-job-id')
        self.assertEqual(status, 404, body)
        self.assertEqual(body['error']['code'], 'job_not_found')


class SubmitPollPublishTests(ResultsRouteTestBase):
    def test_seeded_revision_is_readable_as_latest_and_by_codes(self):
        run_id = self.committed_run()
        revision_id = self.seed_revision(run_id)

        status, report = self.request('GET', f'/api/results/{run_id}')
        self.assertEqual(status, 200, report)
        self.assertEqual(report['revision_id'], revision_id)

        status, codes = self.request(
            'GET', f'/api/results/{run_id}/codes?revision={revision_id}')
        self.assertEqual(status, 200, codes)
        self.assertEqual(codes['revision_id'], revision_id)

    def test_latest_report_404s_before_any_submission(self):
        run_id = self.committed_run()
        status, body = self.request('GET', f'/api/results/{run_id}')
        self.assertEqual(status, 404, body)
        self.assertEqual(body['error']['code'], 'report_not_found')

    def test_submit_returns_202_and_job_reaches_a_terminal_state(self):
        run_id = self.committed_run()
        status, job = self.request('POST', f'/api/results/{run_id}')
        self.assertEqual(status, 202, job)
        final = self.wait_for_terminal_job(job['job_id'])
        self.assertIn(final['state'], ('succeeded', 'failed'))


if __name__ == '__main__':
    unittest.main()
