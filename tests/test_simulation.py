"""The distributable simulation must exercise HTTP and preserve live workspaces."""
import json
import io
import os
import tempfile
import threading
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout

from groupbuy.simulation import run_simulation


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_full_http_sse_capture_export_faults_and_recovery(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        pointer, progress = runtime / 'ui-state.local.json', runtime / 'existing-progress.json'
        pointer.write_text('{"untouched":true}', encoding='utf-8')
        progress.write_text('{"status":"PENDING"}', encoding='utf-8')
        marker = 'fictional-operator-memory-only'
        with patch.dict(os.environ, {'GROUPBUY_API_KEY': marker, 'OPENAI_API_KEY': marker,
                                    'GROUPBUY_MODEL_PROXY_URL': 'http://127.0.0.1:1',
                                    'HTTPS_PROXY': 'http://127.0.0.1:1'}), patch(
                'groupbuy.windows_ui.WindowsDriver', side_effect=AssertionError('desktop forbidden')):
            report = run_simulation(self.root)
        self.assertEqual(report['status'], 'PASS', report['scenarios'])
        self.assertEqual(report['cases_passed'], 12)
        self.assertFalse(report['live_collection_verified'])
        self.assertEqual(report['external_api_requests'], 0)
        self.assertEqual(report['desktop_actions'], 0)
        cases = {c['scenario']: c for c in report['scenarios']}
        normal = cases['batch_and_resume']
        self.assertEqual((normal['completed_tasks'], normal['shops'], normal['products'], normal['http_posts']),
                         (6, 12, 12, 18))
        self.assertGreater(normal['model_http_requests'], 10)
        self.assertEqual(cases['partial_model_response']['tool_calls'], 0)
        self.assertTrue(cases['missing_page']['explicit_retry_recovered'])
        self.assertTrue(all(c['false_completion'] is False for name, c in cases.items() if name != 'batch_and_resume'))
        self.assertEqual(pointer.read_text(encoding='utf-8'), '{"untouched":true}')
        self.assertEqual(progress.read_text(encoding='utf-8'), '{"status":"PENDING"}')
        self.assertEqual(json.loads(Path(report['report_path']).read_text(encoding='utf-8'))['status'], 'PASS')
        self.assertTrue(Path(report['summary_path']).is_file())
        for file in self.root.rglob('*'):
            if file.is_file() and file.suffix in {'.json', '.har', '.csv', '.md'}:
                self.assertNotIn(marker, file.read_text(encoding='utf-8-sig'))

    def test_cancel_before_start_has_no_cases_or_network(self):
        cancel = threading.Event()
        cancel.set()
        with patch('groupbuy.simulation.ModelServer') as server:
            report = run_simulation(self.root, cancel_event=cancel)
        server.assert_not_called()
        self.assertEqual(report['status'], 'INTERRUPTED')
        self.assertEqual(report['cases_passed'], 0)
        self.assertEqual(report['scenarios'], [])

    def test_broken_pipeline_is_not_reported_as_pass(self):
        with patch('groupbuy.simulation.execute', return_value=(
                {'status': 'COMPLETE'}, SimpleNamespace(posts=[]))):
            report = run_simulation(self.root)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['cases_passed'], 0)
        self.assertTrue(all(not c['passed'] for c in report['scenarios']))

    def test_cli_summary_is_compact_and_failure_exit_is_nonzero(self):
        from groupbuy.__main__ import main
        for status, expected in [('PASS', 0), ('FAIL', 2), ('INTERRUPTED', 2)]:
            result = {'status': status, 'backend': 'SIMULATED_RESPONSES_SSE', 'synthetic': True,
                      'live_collection_verified': False, 'cases_passed': 0, 'cases_total': 12,
                      'external_api_requests': 0, 'desktop_actions': 0,
                      'report_path': 'runtime/fictional/report.json',
                      'summary_path': 'runtime/fictional/report.md',
                      'scenarios': [{'scenario': 'fictional', 'passed': False}]}
            output = io.StringIO()
            with patch.object(sys, 'argv', ['groupbuy', 'simulate']), patch(
                    'groupbuy.simulation.run_simulation', return_value=result), redirect_stdout(output):
                self.assertEqual(main(), expected)
            summary = json.loads(output.getvalue())
            self.assertEqual(summary['status'], status)
            self.assertNotIn('scenarios', summary)


if __name__ == '__main__':
    unittest.main()
