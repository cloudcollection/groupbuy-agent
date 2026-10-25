"""Loopback UI protocol, shared progress, memory-only key and proxy isolation."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from groupbuy.agent_runtime import OpenAITransport
from groupbuy.common import GateError, atomic_json, load_json
from groupbuy.config import load_config
from groupbuy.dashboard import Dashboard, dashboard_server
from tests.support import config
from tests.test_capture import free_port


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / 'examples', self.root / 'examples')
        atomic_json(self.root / 'config.local.json', config(self.root))
        self.c = load_config(self.root / 'config.local.json', self.root)
        self.ui = Dashboard(self.c)

    def save(self):
        return self.ui.save_tasks({'tasks': [{'city': '示例市', 'search_term': '虚构广场'}],
                                   'ui_profile': {'calibration_required': False}})

    def test_connection_checks_are_independent_and_not_saved_as_credentials(self):
        self.assertEqual(self.ui.state()['connection_checks'], {'environment': None, 'capture': None, 'model': None})
        self.ui.check_environment()
        self.assertEqual(self.ui.state()['connection_checks']['environment']['status'], 'passed')
        self.ui.checks['capture'] = {'status': 'passed', 'code': None}
        self.ui.checks['model'] = {'status': 'passed', 'provider': 'openai', 'model': 'fictional-model', 'code': None}
        self.save()
        checks = self.ui.state()['connection_checks']
        self.assertIsNone(checks['environment'])
        self.assertIsNone(checks['capture'])
        self.assertEqual(checks['model']['status'], 'passed')
        self.assertIsNone(Dashboard(self.c).state()['connection_checks']['model'])

    def test_probe_records_failure_without_erasing_model_check(self):
        self.save()
        self.ui.checks['model'] = {'status': 'passed', 'model': 'fictional-model'}
        with patch('groupbuy.capture.probe_capture', return_value={
                'status': 'ATTENTION_REQUIRED', 'code': 'capture_no_reports'}):
            self.ui.start_capture_probe({'seconds': .1})
            self.ui.worker.join(timeout=3)
        checks = self.ui.state()['connection_checks']
        self.assertEqual(checks['capture'], {'status': 'failed', 'code': 'capture_no_reports'})
        self.assertEqual(checks['model']['status'], 'passed')

    def test_offline_landing_page_has_editable_live_recipe_defaults(self):
        profile = self.ui.state()['form_defaults']['ui_profile']
        self.assertTrue(profile['calibration_required'])
        self.assertTrue(any(step['action'] == 'click' for step in profile['steps']))
        self.ui.save_tasks({'tasks': [{'city': '示例市', 'search_term': '虚构广场'}],
                            'ui_profile': {'filter_steps': [s['text'] for s in profile['steps'] if s['action'] == 'click']}})
        self.assertEqual(self.ui.state()['tasks'][0]['status'], 'PENDING')

    def test_new_live_tasks_require_calibration_unless_explicitly_acknowledged(self):
        self.ui.save_tasks({'tasks': [{'city': '示例市', 'search_term': '虚构广场'}]})
        self.assertTrue(self.ui.state()['calibration_required'])
        with patch('groupbuy.dashboard.make_transport') as model:
            with self.assertRaisesRegex(GateError, 'ui_calibration_required'):
                self.ui.start({'model': 'fictional-model', 'api_key': 'fictional-key'})
            model.assert_not_called()

    def test_capture_probe_needs_no_key_and_leaves_task_pending(self):
        self.save()
        self.ui.config['_capture']['port'] = free_port()
        with patch('groupbuy.dashboard.make_transport') as model:
            self.ui.start_capture_probe({'seconds': .1})
            self.ui.worker.join(timeout=5)
            model.assert_not_called()
        self.assertEqual(self.ui.result['code'], 'capture_no_reports')
        self.assertEqual(self.ui.state()['tasks'][0]['status'], 'PENDING')
        self.assertFalse(self.ui.capture_status['listening'])

    def test_search_input_mode_is_saved_and_validated(self):
        body = {'tasks': [{'city': '示例市', 'search_term': '虚构广场'}],
                'ui_profile': {'search_input_mode': 'outlined', 'search_label': '搜索'}}
        self.ui.save_tasks(body)
        self.assertEqual(self.ui.config['tasks'][0]['ui_profile']['search_input_mode'], 'outlined')
        self.assertEqual(self.ui.config['tasks'][0]['ui_profile']['steps'][0]['text'], '搜索')
        body['ui_profile']['search_input_mode'] = 'guess'
        with self.assertRaisesRegex(GateError, 'invalid_ui_tasks'):
            self.ui.save_tasks(body)

    def test_filter_menu_recipe_is_configurable_and_preserved(self):
        labels = ['全部分类', '美食', '全部美食', '全城', '虚构商圈']
        body = {'tasks': [{'city': '示例市', 'search_term': '虚构广场'}],
                'ui_profile': {'filter_steps': labels}}
        self.ui.save_tasks(body)
        self.assertEqual([s['text'] for s in self.ui.config['tasks'][0]['ui_profile']['steps'][1:]], labels)
        for invalid in ([], [''], ['a'] * 21, [False]):
            body['ui_profile']['filter_steps'] = invalid
            with self.assertRaisesRegex(GateError, 'invalid_ui_tasks'):
                self.ui.save_tasks(body)

    def test_uncalibrated_task_is_blocked_before_model_request(self):
        self.save()
        self.ui.config['tasks'][0]['ui_profile']['calibration_required'] = True
        with patch('groupbuy.dashboard.make_transport') as factory:
            with self.assertRaisesRegex(GateError, 'ui_calibration_required'):
                self.ui.start({'model': 'fictional-model', 'api_key': 'fictional-key', 'allow_ui': True})
            factory.assert_not_called()

    def test_save_private_configuration_restart_and_cli_share_progress(self):
        result = self.save()
        c = load_config(result['config_path'], self.root)
        self.assertEqual(c['tasks'][0]['input']['kind'], 'reqable')
        self.assertTrue(c['_capture']['enabled'])
        restarted = Dashboard(self.c)
        self.assertEqual(restarted.config['_progress'], c['_progress'])
        self.assertEqual(restarted.state()['tasks'][0]['search_term'], '虚构广场')
        old_path = Path(result['config_path'])
        self.save()
        self.assertTrue(old_path.is_file())
        self.assertNotEqual(self.ui.config['_progress'], c['_progress'])

    def test_invalid_form_preserves_current_configuration(self):
        self.save()
        path = self.ui.config['_progress']
        for body in ({'tasks': []}, {'tasks': [{'city': '示例市', 'search_term': '虚构广场'}], 'category': []},
                     {'tasks': [{'city': '示例市', 'search_term': '虚构广场'}], 'batch_size': 0}):
            with self.assertRaises(GateError):
                self.ui.save_tasks(body)
            self.assertEqual(self.ui.config['_progress'], path)

    def test_demo_runs_without_model_or_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''}):
            self.assertEqual(self.ui.start({'demo': True})['status'], 'STARTED')
            self.ui.worker.join(timeout=10)
            self.assertFalse(self.ui.running())
            self.assertEqual(self.ui.result['status'], 'COMPLETE')
            self.assertEqual(self.ui.result['backend'], 'SCRIPTED_DEMO')

    def test_key_not_saved_state_logs_or_reports(self):
        self.save()
        received = []

        def fake_run(*args, **kwargs):
            received.append(kwargs['transport'].api_key)
            return {'status': 'INCOMPLETE', 'code': 'fictional_stop'}

        marker = 'fictional-memory-only-key'
        with patch('groupbuy.dashboard.run_agent', side_effect=fake_run):
            self.ui.start({'model': 'fictional-model', 'api_key': marker})
            self.ui.worker.join(timeout=3)
        self.assertEqual(received, [marker])
        self.assertNotIn(marker, json.dumps(self.ui.state()))
        self.assertTrue(all(marker not in p.read_text(encoding='utf-8') for p in self.root.rglob('*.json')))

    def test_stop_and_concurrent_start_guard(self):
        self.save()
        entered = threading.Event()

        def fake_run(*args, **kwargs):
            entered.set()
            kwargs['cancel_event'].wait(3)
            return {'status': 'INTERRUPTED'}

        with patch('groupbuy.dashboard.run_agent', side_effect=fake_run):
            self.ui.start({'model': 'fictional-model', 'api_key': 'fictional-key'})
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(GateError, 'agent_already_running'):
                self.ui.start({'demo': True})
            with self.assertRaisesRegex(GateError, 'agent_already_running'):
                self.save()
            self.ui.stop()
            self.ui.worker.join(timeout=3)
        self.assertEqual(self.ui.result['status'], 'INTERRUPTED')

    def test_http_static_host_origin_csrf_and_no_arbitrary_files(self):
        port = free_port()
        server = dashboard_server(self.c, port)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, body=None, headers=None):
            req = urllib.request.Request(f'http://127.0.0.1:{port}' + path,
                data=json.dumps(body).encode() if body is not None else None, headers=headers or {})
            try:
                with opener.open(req) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.read().decode()

        try:
            self.assertEqual(request('/')[0], 200)
            self.assertEqual(request('/app.js')[0], 200)
            self.assertEqual(request('/../config.local.json')[0], 404)
            self.assertEqual(request('/api/state', headers={'Host': 'evil.example.invalid'})[0], 403)
            self.assertEqual(request('/api/state', headers={'Origin': 'https://example.invalid'})[0], 403)
            token = json.loads(request('/api/bootstrap')[1])['csrf']
            self.assertEqual(request('/api/config', {})[0], 403)
            headers = {'Content-Type': 'application/json', 'X-Groupbuy-CSRF': token}
            body = {'tasks': [{'city': '示例市', 'search_term': '虚构广场'}]}
            self.assertEqual(request('/api/config', body, headers)[0], 200)
            info = json.loads(request('/api/capture')[1])
            self.assertTrue(info['report_url'].startswith('http://127.0.0.1:8765/report/'))
            self.assertFalse(info['listener_started'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


class ModelProxyTests(unittest.TestCase):
    def test_default_never_inherits_system_capture_proxy(self):
        with patch.dict(os.environ, {'OPENAI_PROXY_URL': ''}), patch(
                'groupbuy.agent_runtime.urllib.request.build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"output":[]}'
            OpenAITransport('fictional-key').create({}, timeout_seconds=1)
            self.assertEqual(build.call_args.args[0].proxies, {})

    def test_explicit_model_proxy_separate_from_capture(self):
        with patch.dict(os.environ, {'OPENAI_PROXY_URL': 'http://127.0.0.1:9876'}), patch(
                'groupbuy.agent_runtime.urllib.request.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = b'{}'
            OpenAITransport('fictional-key').create({}, timeout_seconds=1)
            self.assertEqual(build.call_args.args[0].proxies, {'https': 'http://127.0.0.1:9876'})

    def test_proxy_credentials_rejected(self):
        with patch.dict(os.environ, {'OPENAI_PROXY_URL': 'http://user:password@example.invalid'}):
            with self.assertRaisesRegex(GateError, 'invalid_model_proxy'):
                OpenAITransport('fictional-key').create({}, timeout_seconds=1)
