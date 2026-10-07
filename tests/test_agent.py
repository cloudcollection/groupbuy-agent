"""Synthetic local tools and fake model responses; never contacts an API."""
import json
import os
import tempfile
import unittest
import urllib.error
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from groupbuy.agent import ScriptedDemoTransport, run_agent, tick_agent
from groupbuy.agent_runtime import AgentSettings, NoRedirect, OpenAITransport, load_settings, run_loop
from groupbuy.agent_tools import CollectionTools
from groupbuy.common import GateError, atomic_json, load_json
from groupbuy.config import load_config
from groupbuy.runner import Runner
from tests.support import FakeDriver, config, entries, evidence, frame


def call(name, args=None, ident='fictional-call'):
    return {'status': 'completed', 'output': [{'type': 'function_call', 'call_id': ident,
            'name': name, 'arguments': json.dumps(args or {})}]}


def final():
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': 'I say every task is complete.'}]}]}


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def create(self, payload, *, timeout_seconds):
        self.payloads.append(deepcopy(payload))
        return self.responses.pop(0)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = config(self.root)
        self.c['tasks'] = self.c['tasks'][:3]
        self.c['runtime']['batch_size'] = 2
        for t in self.c['tasks']:
            atomic_json(self.root / t['input']['path'], {
                'schema': 'groupbuy.local-responses.v1', 'synthetic': True, 'entries': entries()})
            atomic_json(self.root / t['input']['evidence'], evidence(t))
        self.settings = AgentSettings(model='fictional-model')
        self.reload()

    def reload(self):
        path = self.root / 'config.local.json'
        atomic_json(path, self.c)
        self.loaded = load_config(path, self.root)
        self.runner = Runner(self.loaded)
        self.tools = CollectionTools(self.runner, self.settings)

    def loop(self, transport):
        return run_loop(self.settings, self.tools, transport)

    def test_standalone_loop_batch_and_next_run(self):
        result = self.loop(ScriptedDemoTransport())
        self.assertEqual(result['status'], 'COMPLETE')
        state = self.runner.load_state()['tasks']
        self.assertEqual([r['status'] for r in state.values()], ['COMPLETE', 'COMPLETE', 'PENDING'])
        self.tools = CollectionTools(self.runner, self.settings)
        self.assertEqual(self.loop(ScriptedDemoTransport())['status'], 'COMPLETE')
        self.tools = CollectionTools(self.runner, self.settings)
        self.assertEqual(self.loop(FakeTransport([]))['status'], 'NO_PENDING_TASKS')

    def test_model_receives_only_references_counts_and_codes(self):
        transport = FakeTransport([call('check_environment', ident='a'), call('get_status', ident='b'),
                                   call('process_task', {'task_ref': 'task-001'}, 'c'), final()])
        self.loop(transport)
        payloads = json.dumps(transport.payloads, ensure_ascii=False)
        for forbidden in ['示例广场', 'DEMO-001', '虚构餐厅', 'example/fixtures', str(self.root),
                          'fictional-shop-1', 'fictional-product-1', 'shopDealInfos']:
            self.assertNotIn(forbidden, payloads)
        self.assertIn('function_call_output', payloads)
        self.assertTrue(all(p['store'] is False and p['parallel_tool_calls'] is False for p in transport.payloads))

    def test_reasoning_items_replayed(self):
        first = call('get_status', ident='a')
        reasoning = {'type': 'reasoning', 'id': 'synthetic-reasoning', 'encrypted_content': 'fictional-encrypted'}
        first['output'].insert(0, reasoning)
        transport = FakeTransport([first, final()])
        self.loop(transport)
        self.assertIn(reasoning, transport.payloads[1]['input'])

    def test_model_cannot_assert_completion(self):
        result = self.loop(FakeTransport([final()]))
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(self.runner.plan()[0]['status'], 'PENDING')

    def test_unknown_tools_and_extra_arguments_do_not_execute(self):
        result = self.tools.execute('run_shell', {'command': 'fictional-command'})
        self.assertEqual(result['code'], 'unknown_agent_tool')
        result = self.tools.execute('process_task', {'task_ref': 'task-001', 'path': '../other'})
        self.assertEqual(result['code'], 'invalid_tool_arguments')
        self.assertFalse(self.runner.progress_path.exists())

    def test_order_and_batch_enforced(self):
        self.tools.execute('check_environment', {})
        for ref in ('task-002', 'task-003', 'DEMO-001'):
            self.assertEqual(self.tools.execute('process_task', {'task_ref': ref})['code'],
                             'task_outside_current_batch')
        self.assertEqual(self.tools.execute('process_task', {'task_ref': 'task-001'})['snapshot']['tasks'][0]['status'],
                         'COMPLETE')

    def test_environment_check_required(self):
        self.assertEqual(self.tools.execute('process_task', {'task_ref': 'task-001'})['code'],
                         'environment_check_required')

    def test_duplicate_call_never_reexecutes_mutation(self):
        transport = FakeTransport([call('check_environment', ident='a'),
                                   call('process_task', {'task_ref': 'task-001'}, 'b'),
                                   call('process_task', {'task_ref': 'task-002'}, 'b')])
        result = self.loop(transport)
        self.assertEqual(result['code'], 'duplicate_or_invalid_call')
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-002']['status'], 'PENDING')

    def test_multiple_calls_rejected_before_execution(self):
        response = call('process_task', {'task_ref': 'task-001'}, 'a')
        response['output'].extend(call('process_task', {'task_ref': 'task-002'}, 'b')['output'])
        self.assertEqual(self.loop(FakeTransport([response]))['code'], 'parallel_calls_rejected')
        self.assertFalse(self.runner.progress_path.exists())

    def test_invalid_json_tool_arguments_return_controlled_error(self):
        response = call('get_status')
        response['output'][0]['arguments'] = '{broken'
        transport = FakeTransport([response, final()])
        result = self.loop(transport)
        self.assertEqual(result['status'], 'INCOMPLETE')
        output = json.loads(transport.payloads[1]['input'][-1]['output'])
        self.assertEqual(output['code'], 'invalid_tool_arguments')

    def test_incomplete_model_response_executes_no_tools(self):
        response = call('process_task', {'task_ref': 'task-001'})
        response['status'] = 'incomplete'
        self.assertEqual(self.loop(FakeTransport([response]))['code'], 'model_response_incomplete')
        self.assertFalse(self.runner.progress_path.exists())

    def test_round_limit_is_not_completion(self):
        settings = AgentSettings(model='fictional-model', max_rounds=1)
        result = run_loop(settings, self.tools, FakeTransport([call('get_status')]))
        self.assertEqual(result['code'], 'agent_round_limit')
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_time_limit_before_network(self):
        with patch('groupbuy.agent_runtime.time.monotonic', side_effect=[0, 2000]):
            self.assertEqual(self.loop(FakeTransport([]))['code'], 'agent_time_limit')

    def test_pagination_failure_stops_before_next_task(self):
        t = self.c['tasks'][0]
        path = self.root / t['input']['path']
        doc = load_json(path)
        doc['entries'].pop()
        atomic_json(path, doc)
        result = self.loop(ScriptedDemoTransport())
        self.assertEqual(result['status'], 'ATTENTION_REQUIRED')
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-001']['error'], 'pagination_gap')
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-002']['status'], 'PENDING')

    def test_missing_input_waits_without_calling_model(self):
        (self.root / self.c['tasks'][0]['input']['path']).unlink()
        self.assertEqual(self.loop(FakeTransport([]))['status'], 'WAITING_INPUT')

    def test_recovery_verifies_artifact(self):
        self.runner.run()
        state = self.runner.load_state()
        state['tasks']['DEMO-001']['status'] = 'RUNNING'
        self.runner.save(state)
        tools = CollectionTools(self.runner, self.settings)
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-001']['status'], 'COMPLETE')
        self.assertEqual(len(tools.tasks), 1)
        target = self.runner.output / state['tasks']['DEMO-001']['artifact_dir'] / 'shops.csv'
        target.write_text('modified', encoding='utf-8')
        with self.assertRaisesRegex(GateError, 'artifact_integrity_failure'):
            CollectionTools(self.runner, self.settings)

    def test_uncommitted_interruption_requires_operator_retry(self):
        state = self.runner.load_state()
        state['tasks']['DEMO-001']['status'] = 'RUNNING'
        self.runner.save(state)
        tools = CollectionTools(self.runner, self.settings)
        self.assertEqual(tools.snapshot()['status'], 'ATTENTION_REQUIRED')
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-001']['error'], 'interrupted')

    def test_global_stop_cannot_be_bypassed_by_narrowed_runner(self):
        state = self.runner.load_state()
        state['tasks']['DEMO-003'].update(status='STOPPED', error='verification_or_login')
        self.runner.save(state)
        result = self.runner.run(offline=True, task_ids=['DEMO-001'])
        self.assertEqual(result['status'], 'ATTENTION_REQUIRED')
        self.assertEqual(result['processed'], [])

    def live_tools(self, frames, max_scrolls=180, max_segments=3):
        self.c['mode'] = 'live'
        self.c['runtime']['max_scrolls'] = max_scrolls
        for t in self.c['tasks']:
            t['ui_profile'] = {'steps': [{'action': 'type_search', 'text': '搜索商家'}]}
        self.reload()
        self.tools = CollectionTools(self.runner, AgentSettings(model='fictional-model', max_ui_segments=max_segments),
                                     allow_ui=True, driver_factory=lambda: FakeDriver(frames))
        self.tools.execute('check_environment', {})

    def test_ui_ready_waits_for_manual_export_then_resumes(self):
        self.live_tools([frame(), frame(), frame(), frame(True), frame(True)])
        result = self.tools.execute('collect_ui', {'task_ref': 'task-001', 'resume': False})
        self.assertEqual(result['snapshot']['status'], 'WAITING_INPUT')
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-001']['status'], 'PENDING')
        record = self.runner.load_state()['tasks']['DEMO-001']
        e = load_json(self.runner.output / record['ui_evidence'])
        local_entries = entries()
        for item in local_entries:
            item['captured_at'] = e['capture_start']
        atomic_json(self.root / self.c['tasks'][0]['input']['path'], {
            'schema': 'groupbuy.local-responses.v1', 'synthetic': True, 'entries': local_entries})
        tools = CollectionTools(self.runner, self.settings)
        tools.execute('check_environment', {})
        result = tools.execute('process_task', {'task_ref': 'task-001'})
        self.assertEqual(result['processed'][0]['status'], 'COMPLETE')

    def test_ui_disabled_and_risk_stops(self):
        self.live_tools([frame(risk=True)])
        result = self.tools.execute('collect_ui', {'task_ref': 'task-001', 'resume': False})
        self.assertEqual(result['snapshot']['status'], 'ATTENTION_REQUIRED')
        self.assertEqual(result['code'], 'verification_or_login')
        self.assertNotIn('验证码', json.dumps(result, ensure_ascii=False))
        self.runner.retry('DEMO-001')
        tools = CollectionTools(self.runner, self.settings)
        self.assertEqual(tools.snapshot()['status'], 'UI_REQUIRED')

    def test_scroll_resume_and_segment_cap(self):
        self.live_tools([frame()] * 6, max_scrolls=1, max_segments=2)
        first = self.tools.execute('collect_ui', {'task_ref': 'task-001', 'resume': False})
        self.assertEqual(first['snapshot']['tasks'][0]['stage'], 'RESUME_UI')
        self.assertEqual(self.tools.execute('collect_ui', {'task_ref': 'task-001', 'resume': False})['code'],
                         'unsafe_scroll_resume')
        second = self.tools.execute('collect_ui', {'task_ref': 'task-001', 'resume': True})
        self.assertEqual(second['snapshot']['code'], 'ui_segment_limit')
        self.assertEqual(self.runner.load_state()['tasks']['DEMO-001']['status'], 'REVIEW')

    def test_demo_isolated_and_no_credentials(self):
        result = run_agent(self.loaded, AgentSettings(), demo=True)
        self.assertEqual(result['backend'], 'SCRIPTED_DEMO')
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertFalse(self.runner.progress_path.exists())
        self.assertNotIn('report_path', load_json(result['report_path']))

    def test_demo_rejects_real_configuration(self):
        self.loaded['synthetic'] = False
        with self.assertRaisesRegex(GateError, 'demo_requires_synthetic_config'):
            run_agent(self.loaded, self.settings, demo=True)

    def test_schedule_claim_prevents_repeated_api_session(self):
        self.loaded['schedule']['enabled'] = True
        now = datetime.fromisoformat('2030-01-01T09:00:00+08:00')
        first = tick_agent(self.loaded, self.settings, now, transport=ScriptedDemoTransport())
        self.assertEqual(first['status'], 'COMPLETE')
        self.assertEqual(tick_agent(self.loaded, self.settings, now, transport=FakeTransport([]))['status'],
                         'ALREADY_CLAIMED')

    def test_live_schedule_requires_validation(self):
        self.loaded['mode'] = 'live'
        with self.assertRaisesRegex(GateError, 'live_schedule_not_validated'):
            tick_agent(self.loaded, self.settings, datetime.fromisoformat('2030-01-01T09:00:00+08:00'))

    def test_exception_text_not_exported(self):
        secret = 'sk-' + 'Z' * 30
        with patch.object(self.runner, 'run', side_effect=RuntimeError(secret + str(self.root))):
            self.tools.execute('check_environment', {})
            result = self.tools.execute('process_task', {'task_ref': 'task-001'})
        self.assertEqual(result, {'ok': False, 'code': 'agent_tool_failed'})

    def test_stop_during_model_request_never_runs_returned_tool(self):
        import threading
        event = threading.Event()
        self.tools.execute('check_environment', {})
        class StopTransport:
            def create(self, payload, **kwargs):
                event.set()
                return call('process_task', {'task_ref': 'task-001'})
        result = run_loop(self.settings, self.tools, StopTransport(), cancel_event=event)
        self.assertEqual(result['status'], 'INTERRUPTED')
        self.assertEqual(self.runner.plan()[0]['status'], 'PENDING')


class TransportTests(unittest.TestCase):
    def test_key_from_environment_and_hidden_repr(self):
        key = 'sk-' + 'S' * 30
        with patch.dict(os.environ, {'OPENAI_API_KEY': key}):
            transport = OpenAITransport.from_environment()
        self.assertNotIn(key, repr(transport))
        with self.assertRaisesRegex(GateError, 'api_key_required'):
            OpenAITransport('')

    def test_http_error_body_and_key_never_logged(self):
        transport = OpenAITransport('fictional-credential')
        error = urllib.error.HTTPError('https://api.openai.com/v1/responses', 401,
                                       'fictional-sensitive-body', {}, None)
        with patch('urllib.request.OpenerDirector.open', side_effect=error):
            with self.assertRaisesRegex(GateError, '^model_auth_failed$'):
                transport.create({'model': 'fictional-model'}, timeout_seconds=1)

    def test_redirect_refused(self):
        with self.assertRaisesRegex(GateError, 'model_redirect_rejected'):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid')

    def test_endpoint_and_header_fixed(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return b'{"output": []}'
        transport = OpenAITransport('fictional-credential')
        with patch('urllib.request.OpenerDirector.open', return_value=Response()) as opened:
            transport.create({'model': 'fictional-model'}, timeout_seconds=3)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fictional-credential')
        self.assertEqual(opened.call_args.kwargs['timeout'], 3)

    def test_settings_rejects_credentials_and_invalid_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agent.local.json'
            atomic_json(path, {'version': 1, 'api_key': 'fictional'})
            with self.assertRaisesRegex(GateError, 'invalid_agent_settings'):
                load_settings(path)
        for values in ({'max_rounds': True}, {'timeout_seconds': float('nan')}, {'max_ui_segments': 0}):
            with self.assertRaisesRegex(GateError, 'invalid_agent_settings'):
                AgentSettings(**values)


if __name__ == '__main__':
    unittest.main()
