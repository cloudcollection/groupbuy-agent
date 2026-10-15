"""Key-free preflight and real loopback diagnostics; synthetic data only."""
import gzip
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch
from groupbuy.agent import run_agent
from groupbuy.agent_runtime import AgentSettings
from groupbuy.capture import CaptureReceiver, capture_info, probe_capture
from groupbuy.common import GateError, atomic_json
from groupbuy.config import load_config
from groupbuy.diagnostics import doctor
from tests.support import config
from tests.test_capture import free_port, report


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        c = config(self.root)
        c['mode'] = 'live'
        c['tasks'] = c['tasks'][:1]
        c['capture'] = {'enabled': True, 'port': free_port()}
        c['tasks'][0]['input'].update(kind='reqable', path='runtime/capture/DEMO-001')
        c['tasks'][0]['ui_profile'] = {'calibration_required': False,
                                     'steps': [{'action': 'type_search', 'text': '搜索商家'}]}
        atomic_json(self.root / 'config.local.json', c)
        self.c = load_config(self.root / 'config.local.json', self.root)

    def post(self, value, *, wrong=False):
        url = capture_info(self.c)['report_url'] + ('-wrong' if wrong else '')
        data = gzip.compress(json.dumps(value).encode())
        req = urllib.request.Request(url, data=data, headers={'Content-Encoding': 'gzip'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=3) as response:
                return response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            return status

    def start_probe(self):
        ready = threading.Event()
        results, statuses = [], []
        def observe(status):
            statuses.append(status)
            if status['listening']:
                ready.set()
        thread = threading.Thread(target=lambda: results.append(probe_capture(self.c, 2, on_status=observe)))
        thread.start()
        self.addCleanup(lambda: thread.join(timeout=5))
        self.assertTrue(ready.wait(3))
        return thread, results, statuses

    def test_probe_receives_gzip_without_saving_payload_or_progress(self):
        with patch('groupbuy.agent_runtime.post_json') as model:
            thread, results, statuses = self.start_probe()
            self.assertEqual(self.post({'log': {'entries': [report()]}}), 200)
            thread.join(timeout=5)
            model.assert_not_called()
        result = results[0]
        self.assertEqual(result['status'], 'CAPTURE_CONNECTED')
        self.assertFalse(result['collection_verified'])
        self.assertFalse(result['capture']['listening'])
        self.assertFalse(result['capture']['armed'])
        self.assertEqual(result['capture']['posts_received'], 1)
        self.assertEqual(result['capture']['valid_reports'], 1)
        self.assertEqual(result['capture']['accepted_entries'], 0)
        self.assertFalse(self.c['_progress'].exists())
        self.assertFalse(list(self.root.rglob('input.har')))
        self.assertFalse(list(self.root.rglob('session.json')))
        for path in self.root.rglob('*.json'):
            self.assertNotIn('fictional-private-marker', path.read_text(encoding='utf-8'))
        self.assertNotIn('/report/', json.dumps(statuses))

    def test_wrong_address_is_distinct_from_no_traffic(self):
        thread, results, _ = self.start_probe()
        self.assertEqual(self.post(report(), wrong=True), 403)
        thread.join(timeout=5)
        self.assertEqual(results[0]['code'], 'report_not_authorized')
        self.assertEqual(results[0]['capture']['rejected_reports'], 1)
        quiet = probe_capture(self.c, .1)
        self.assertEqual(quiet['code'], 'capture_no_reports')
        self.assertEqual(quiet['capture']['posts_received'], 0)

    def test_unarmed_receiver_rejects_malformed_envelopes(self):
        with CaptureReceiver(self.c) as receiver:
            self.assertEqual(self.post({'log': {'entries': [123]}}), 400)
            self.assertEqual(receiver.status()['valid_reports'], 0)
            self.assertEqual(receiver.status()['last_error'], 'invalid_report')

    def test_probe_cancellation_and_duration_limits(self):
        cancel = threading.Event()
        cancel.set()
        result = probe_capture(self.c, 5, cancel_event=cancel)
        self.assertEqual(result['status'], 'INTERRUPTED')
        self.assertFalse(result['capture']['listening'])
        for invalid in (True, 0, 301, float('nan'), float('inf')):
            with self.assertRaisesRegex(GateError, 'invalid_probe_duration'):
                probe_capture(self.c, invalid)

    def test_doctor_does_not_read_keys_or_write_task_state(self):
        marker = 'fictional-memory-marker'
        self.c['tasks'][0]['ui_profile']['calibration_required'] = True
        with patch.dict(os.environ, {'GB_TEMP_DIR': '', 'OPENAI_API_KEY': marker}), \
                patch('groupbuy.diagnostics.importlib.util.find_spec', return_value=None):
            result = doctor(self.c)
        self.assertEqual(result['status'], 'ATTENTION_REQUIRED')
        self.assertIn('win32gui', result['missing_ui_dependencies'])
        self.assertIn('ui_calibration_required', [x['code'] for x in result['checks']])
        self.assertFalse(self.c['_progress'].exists())
        self.assertNotIn(marker, json.dumps(result))
        self.assertNotIn(self.c['tasks'][0]['search_term'], json.dumps(result, ensure_ascii=False))

    def test_missing_ui_is_detected_before_first_model_request(self):
        transport = Mock(backend='FICTIONAL_TEST')
        with patch('groupbuy.agent_tools.importlib.util.find_spec', return_value=None):
            result = run_agent(self.c, AgentSettings(model='fictional-model'), allow_ui=True, transport=transport)
        transport.create.assert_not_called()
        self.assertEqual(result['status'], 'UI_UNAVAILABLE')
        self.assertEqual(result['rounds'], 0)
        self.assertEqual(result['next_action'], 'install_ui_dependencies')
