"""Official report protocol against a real loopback server; all data is fictional."""
import gzip
import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from groupbuy.capture import CaptureReceiver, capture_source
from groupbuy.common import GateError, atomic_json, load_json, utcnow
from groupbuy.config import load_config
from groupbuy.agent_runtime import AgentSettings
from groupbuy.agent_tools import CollectionTools
from groupbuy.runner import Runner
from tests.support import config, entry, evidence, FakeDriver, frame


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def report(start=0, nxt=0, end=True, number=1):
    e = entry(start, nxt, end, number)
    return {'startedDateTime': utcnow(), 'request': {'method': 'POST',
            'url': 'https://example.dianping.com/search',
            'headers': [{'name': 'Authorization', 'value': 'fictional-private-marker'}],
            'postData': {'text': json.dumps({**e['request'], 'token': 'fictional-private-marker'})}},
            'response': {'status': 200, 'headers': [],
                         'content': {'text': json.dumps(e['response'], ensure_ascii=False)}}}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        c = config(self.root)
        c['mode'] = 'live'
        c['tasks'] = c['tasks'][:2]
        c['capture'] = {'enabled': True, 'port': free_port(), 'settle_seconds': 0, 'timeout_seconds': 0}
        for t in c['tasks']:
            t['input'].update(kind='reqable', path='runtime/capture/' + t['task_id'])
            t['ui_profile'] = {'steps': [{'action': 'type_search', 'text': '搜索商家'}]}
        atomic_json(self.root / 'config.local.json', c)
        self.c = load_config(self.root / 'config.local.json', self.root)
        self.task = self.c['tasks'][0]
        self.receiver = CaptureReceiver(self.c)

    def prepare(self):
        meta = self.receiver.arm(self.task)
        return {'capture_session': meta, 'ui_evidence': 'proof.json'}

    def proof(self, record):
        e = evidence(self.task)
        e.update(capture_start=record['capture_session']['started_at'], capture_end=utcnow())
        atomic_json(self.c['_output'] / record['ui_evidence'], e)

    def post(self, value, encoding='', route=None, origin=None):
        data = json.dumps(value).encode()
        if encoding == 'gzip':
            data = gzip.compress(data)
        headers = {'Content-Type': 'application/json', 'Content-Encoding': encoding}
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(f"http://127.0.0.1:{self.c['_capture']['port']}" +
                                     (route or self.receiver.route), data=data, headers=headers)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_http_gzip_projection_seal_and_parse(self):
        record = self.prepare()
        raw = report()
        raw['response']['content']['text'] = raw['response']['content']['text'].replace(
            '虚构双人套餐', '虚构双人套餐 联系138' + '00138000')
        with self.receiver:
            self.assertEqual(self.post({'log': {'entries': [raw]}}, 'gzip')[0], 200)
            self.proof(record)
            record['auto_har'] = self.receiver.seal(self.task, record)
        path = capture_source(self.c, self.task, record)
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('fictional-private-marker', text)
        self.assertNotIn('138' + '00138000', text)
        self.assertEqual(load_json(path)['log']['entries'][0]['request']['method'], 'POST')
        runner = Runner(self.c)
        state = runner.load_state()
        state['tasks'][self.task['task_id']].update(record)
        runner.save(state)
        self.assertEqual(runner.run(offline=True, task_ids=[self.task['task_id']])['processed'][0]['status'], 'COMPLETE')

    def test_target_domain_and_keyword_isolation(self):
        self.prepare()
        for url in ('https://api.openai.com/search', 'https://chatgpt.com/search',
                    'https://evil-dianping.com/search', 'http://example.dianping.com/search'):
            value = report()
            value['request']['url'] = url
            self.assertEqual(self.receiver.accept(value), 0)
        value = report()
        value['request']['postData']['text'] = '{"keyword":"其他广场"}'
        self.assertEqual(self.receiver.accept(value), 0)
        self.assertEqual(self.receiver.active['entries'], [])

    def test_old_reports_ignored_and_future_clock_rejected(self):
        self.prepare()
        old = report()
        old['startedDateTime'] = '2000-01-01T00:00:00+00:00'
        self.assertEqual(self.receiver.accept(old), 0)
        future = report()
        future['startedDateTime'] = '2100-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(GateError, 'capture_clock_mismatch'):
            self.receiver.accept(future)

    def test_compressed_and_expanded_report_size_bounded(self):
        self.prepare()
        with self.receiver:
            with patch('groupbuy.capture.MAX_REPORT_BYTES', 100):
                self.assertEqual(self.post(report())[1]['code'], 'report_too_large')
            huge = {'padding': 'X' * 10000}
            with patch('groupbuy.capture.MAX_REPORT_BYTES', 200):
                self.assertEqual(self.post(huge, 'gzip')[1]['code'], 'report_too_large')

    def test_route_origin_compression_and_malformed_rejected(self):
        self.prepare()
        with self.receiver:
            self.assertEqual(self.post(report(), route='/wrong')[0], 403)
            self.assertEqual(self.post(report(), origin='https://example.invalid')[0], 403)
            self.assertEqual(self.post(report(), encoding='br')[1]['code'], 'unsupported_report_encoding')
            self.assertEqual(self.post([])[1]['code'], 'invalid_report')
            self.assertEqual(self.post(report())[0], 200)

    def test_missing_page_and_latest_incomplete_round_never_sealed(self):
        record = self.prepare()
        self.receiver.accept(report(0, 1, False))
        self.proof(record)
        with self.assertRaisesRegex(GateError, 'capture_incomplete_or_not_reporting'):
            self.receiver.seal(self.task, record)
        self.receiver.accept(report(1, 0, True, 2))
        self.receiver.accept(report(0, 3, False, 3))
        self.proof(record)
        with self.assertRaisesRegex(GateError, 'capture_incomplete_or_not_reporting'):
            self.receiver.seal(self.task, record)

    def test_journal_resume_and_snapshot_tamper(self):
        record = self.prepare()
        self.receiver.accept(report(0, 1, False))
        restored = CaptureReceiver(self.c)
        restored.arm(self.task, record['capture_session'])
        restored.accept(report(1, 0, True, 2))
        self.proof(record)
        record['auto_har'] = restored.seal(self.task, record)
        path = capture_source(self.c, self.task, record)
        path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(GateError, 'capture_integrity_failure'):
            capture_source(self.c, self.task, record)

    def test_session_and_proof_binding(self):
        record = self.prepare()
        wrong = deepcopy(record['capture_session'])
        wrong['task_hash'] = '0' * 64
        with self.assertRaisesRegex(GateError, 'capture_session_mismatch'):
            self.receiver.arm(self.task, wrong)
        self.receiver.accept(report())
        self.proof(record)
        record['auto_har'] = self.receiver.seal(self.task, record)
        atomic_json(self.c['_output'] / 'proof.json', {})
        with self.assertRaisesRegex(GateError, 'capture_integrity_failure'):
            capture_source(self.c, self.task, record)

    def test_capture_error_persists_after_restart(self):
        record = self.prepare()
        bad = report()
        bad['response']['status'] = 403
        with self.assertRaisesRegex(GateError, 'capture_http_failure'):
            self.receiver.accept(bad)
        self.proof(record)
        restored = CaptureReceiver(self.c)
        with self.assertRaisesRegex(GateError, 'capture_http_failure'):
            restored.seal(self.task, record)

    def test_ui_http_seal_export_two_tasks_and_shared_progress(self):
        receiver = self.receiver
        test = self

        class ReportingDriver(FakeDriver):
            def type_search(self, text):
                super().type_search(text)
                test.assertEqual(test.post(report())[0], 200)

        driver = ReportingDriver([frame(True)])
        runner = Runner(self.c)
        tools = CollectionTools(runner, AgentSettings(model='fictional-model'), allow_ui=True,
                                driver_factory=lambda: driver, capture=receiver)
        with receiver:
            tools.execute('check_environment', {})
            for ref in ('task-001', 'task-002'):
                self.assertTrue(tools.execute('collect_ui', {'task_ref': ref, 'resume': False})['ok'])
                self.assertEqual(tools.snapshot()['tasks'][int(ref[-1])-1]['stage'], 'EXPORT_HAR')
                self.assertTrue(tools.execute('export_har', {'task_ref': ref})['ok'])
                self.assertTrue(tools.execute('process_task', {'task_ref': ref})['ok'])
        self.assertEqual(tools.snapshot()['status'], 'BATCH_COMPLETE')
        self.assertEqual(Runner(self.c).load_state(), runner.load_state())

    def test_manual_retry_starts_new_search(self):
        runner = Runner(self.c)
        state = runner.load_state()
        state['tasks'][self.task['task_id']].update(status='STOPPED', error='capture_incomplete_or_not_reporting',
                                                   capture_session={}, auto_har={}, ui_evidence='proof.json')
        runner.save(state)
        runner.retry(self.task['task_id'])
        row = runner.load_state()['tasks'][self.task['task_id']]
        self.assertEqual(row['status'], 'PENDING')
        self.assertFalse({'ui_evidence', 'auto_har', 'capture_session'} & row.keys())

    def test_duplicate_page_projection_retains_product_link(self):
        record = self.prepare()
        first = report(0, 1, False)
        self.receiver.accept(first)
        self.receiver.accept(deepcopy(first))
        self.receiver.accept(report(1, 0, True, 2))
        self.proof(record)
        record['auto_har'] = self.receiver.seal(self.task, record)
        runner = Runner(self.c)
        state = runner.load_state()
        state['tasks'][self.task['task_id']].update(record)
        runner.save(state)
        result = runner.run(offline=True, task_ids=[self.task['task_id']])
        self.assertEqual(result['processed'][0]['status'], 'COMPLETE')
        folder = runner.output / runner.load_state()['tasks'][self.task['task_id']]['artifact_dir']
        parsed = load_json(folder / 'result.json')
        self.assertEqual(parsed['quality']['duplicate_pages'], 1)
        self.assertEqual(len(parsed['products']), 2)
