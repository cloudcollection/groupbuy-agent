"""Fictional protocol replies, real local tools, and loopback HTTP only."""
import json
import io
import os
import tempfile
import threading
import unittest
import urllib.error
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from groupbuy.agent import run_agent
from groupbuy.agent_runtime import AgentSettings, load_settings
from groupbuy.common import GateError, atomic_json, load_json
from groupbuy.config import load_config
from groupbuy.model_providers import (PROVIDERS, ProviderTransport, environment_key, make_transport,
                                     normalize_chat, normalize_messages, codex_responses_body,
                                     read_responses_stream, probe_model)
from groupbuy.dashboard import Dashboard
from tests.support import config, entries, evidence


def chat_reply(name=None, args=None, ident='fictional-call', reasoning=False):
    msg = {'role': 'assistant', 'content': 'fictional-assistant-note'}
    if name:
        msg['tool_calls'] = [{'type': 'function', 'id': ident,
                             'function': {'name': name, 'arguments': json.dumps(args or {})}}]
    if reasoning:
        msg['reasoning_content'] = 'fictional-reasoning-note'
    return {'choices': [{'message': msg, 'finish_reason': 'tool_calls' if name else 'stop'}],
            'usage': {'total_tokens': 3}}


def messages_reply(name=None, args=None, ident='fictional-call'):
    content = [{'type': 'text', 'text': 'fictional-assistant-note'}]
    if name:
        content.append({'type': 'tool_use', 'id': ident, 'name': name, 'input': args or {}})
    return {'type': 'message', 'role': 'assistant', 'content': content,
            'stop_reason': 'tool_use' if name else 'end_turn', 'usage': {'input_tokens': 2, 'output_tokens': 1}}


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.data[:limit]
    def getheader(self, name, default=''):
        return 'application/json' if name == 'Content-Type' else default


class ProviderConfigurationTests(unittest.TestCase):
    def test_invalid_urls_providers_and_fixed_openai(self):
        for url in ('http://example.invalid/v1', 'https://user:pass@example.invalid/v1',
                    'https://example.invalid/v1?secret=value', 'https://example.invalid/v1#token',
                    'https://example.invalid:bad/v1', 'https://example.invalid:0/v1',
                    'https://example.invalid/v1/chat/completions', 'https://example.invalid/\n',
                    'https://example.invalid\\evil/v1', 'https://%65xample.invalid/v1'):
            with self.subTest(url=url), self.assertRaises(GateError):
                AgentSettings(provider='openai_compatible', base_url=url)
        for value in ('unknown', [], None):
            with self.subTest(provider=value), self.assertRaises(GateError):
                AgentSettings(provider=value)
        with self.assertRaisesRegex(GateError, 'openai_endpoint_fixed'):
            AgentSettings(base_url='https://example.invalid/v1')
        with self.assertRaisesRegex(GateError, 'model_base_url_required'):
            make_transport(AgentSettings(provider='openai_compatible'), 'fictional-key')
        for host in ('127.0.0.1', 'localhost', '[::1]'):
            make_transport(AgentSettings(provider='openai_compatible', base_url=f'http://{host}:8766/v1'), 'fictional-key')

    def test_provider_keys_isolated_and_explicit_generic_override(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fictional-openai'}, clear=True):
            self.assertEqual(environment_key('openai'), 'fictional-openai')
            for provider in PROVIDERS:
                if provider != 'openai':
                    self.assertEqual(environment_key(provider), '')
            with self.assertRaisesRegex(GateError, 'api_key_required'):
                make_transport(AgentSettings(provider='deepseek'))
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fictional-deepseek', 'GROUPBUY_API_KEY': 'fictional-explicit'}, clear=True):
            self.assertEqual(environment_key('deepseek'), 'fictional-explicit')

    def test_settings_priority_and_legacy_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agent.local.json'
            atomic_json(path, {'version': 1})
            with patch.dict(os.environ, {'OPENAI_MODEL': 'fictional-legacy'}, clear=True):
                self.assertEqual(load_settings(path).model, 'fictional-legacy')
            with patch.dict(os.environ, {'GROUPBUY_PROVIDER': 'deepseek', 'DEEPSEEK_MODEL': 'fictional-model'}, clear=True):
                s = load_settings(path)
                self.assertEqual((s.provider, s.model), ('deepseek', 'fictional-model'))
                override = load_settings(path, provider='kimi', model='fictional-explicit')
                self.assertEqual((override.provider, override.model), ('kimi', 'fictional-explicit'))
            atomic_json(path, {'version': 1, 'provider': 'qwen', 'model': 'fictional-file',
                               'base_url': 'https://api.example.invalid/v1'})
            with patch.dict(os.environ, {'GROUPBUY_BASE_URL': 'http://bad.invalid', 'GROUPBUY_MODEL': 'fictional-env'}, clear=True):
                self.assertEqual(load_settings(path).model, 'fictional-file')
            for key in ('api_key', 'headers', 'proxy_url'):
                atomic_json(path, {'version': 1, key: 'fictional-forbidden'})
                with self.assertRaisesRegex(GateError, 'invalid_agent_settings'):
                    load_settings(path)

    def test_secret_repr_and_proxy_isolation(self):
        transport = ProviderTransport('fictional-secret', 'deepseek')
        self.assertNotIn('fictional-secret', repr(transport))
        payload = {'model': 'fictional-model', 'instructions': 'fictional-instructions',
                   'input': [{'role': 'user', 'content': 'fictional-request'}], 'tools': [], 'max_output_tokens': 256}
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:9999', 'OPENAI_PROXY_URL': 'http://127.0.0.1:9876'}, clear=True), patch(
                'groupbuy.agent_runtime.urllib.request.build_opener') as build:
            build.return_value.open.return_value = Response(chat_reply())
            transport.create(payload, timeout_seconds=1)
            self.assertEqual(build.call_args.args[0].proxies, {})
        with patch.dict(os.environ, {'GROUPBUY_MODEL_PROXY_URL': 'http://127.0.0.1:9876'}, clear=True), patch(
                'groupbuy.agent_runtime.urllib.request.build_opener') as build:
            build.return_value.open.return_value = Response(chat_reply())
            transport.create(payload, timeout_seconds=1)
            self.assertEqual(build.call_args.args[0].proxies, {'https': 'http://127.0.0.1:9876'})
        with patch.dict(os.environ, {'GROUPBUY_MODEL_PROXY_URL': 'http://user:pass@example.invalid'}, clear=True):
            with self.assertRaisesRegex(GateError, 'invalid_model_proxy'):
                transport.create(payload, timeout_seconds=1)


class ProviderLoopTests(unittest.TestCase):
    def setup_config(self, root):
        c = config(root)
        c['tasks'] = c['tasks'][:2]
        for task in c['tasks']:
            atomic_json(root / task['input']['path'], {'schema': 'groupbuy.local-responses.v1',
                                                      'synthetic': True, 'entries': entries()})
            atomic_json(root / task['input']['evidence'], evidence(task))
        atomic_json(root / 'config.local.json', c)
        return load_config(root / 'config.local.json', root)

    def test_full_batch_each_provider_native_history_and_private_reports(self):
        for provider in ('deepseek', 'qwen', 'kimi', 'openai_compatible', 'anthropic'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                loaded = self.setup_config(root)
                settings = AgentSettings(provider=provider, model='fictional-model',
                    base_url='https://api.example.invalid/v1' if provider == 'openai_compatible' else None)
                steps = [('check_environment', {}), ('get_status', {}),
                         ('process_task', {'task_ref': 'task-001'}), ('process_task', {'task_ref': 'task-002'}), (None, {})]
                reply = messages_reply if provider == 'anthropic' else chat_reply
                responses = [reply(name, args, f'fictional-{i}') for i, (name, args) in enumerate(steps)]
                if provider == 'deepseek':
                    for response in responses:
                        response['choices'][0]['message']['reasoning_content'] = 'fictional-reasoning-note'
                requests = []
                def opened(request, **kwargs):
                    requests.append(request)
                    return Response(responses.pop(0))
                with patch('urllib.request.OpenerDirector.open', side_effect=opened):
                    result = run_agent(loaded, settings, transport=make_transport(settings, 'fictional-secret'))
                self.assertEqual(result['status'], 'COMPLETE')
                self.assertEqual(result['provider'], provider)
                self.assertEqual(result['reported_tokens'], 15)
                self.assertEqual(result['backend'], 'ANTHROPIC_MESSAGES' if provider == 'anthropic' else 'CHAT_COMPLETIONS')
                bodies = [json.loads(r.data) for r in requests]
                text = json.dumps(bodies, ensure_ascii=False)
                for private in ('DEMO-001', '示例广场', 'fictional-shop', 'fictional-secret', str(root)):
                    self.assertNotIn(private, text)
                if provider == 'anthropic':
                    self.assertEqual(requests[0].get_header('X-api-key'), 'fictional-secret')
                    self.assertEqual(requests[0].get_header('Anthropic-version'), '2023-06-01')
                    self.assertIsNone(requests[0].get_header('Authorization'))
                    self.assertEqual(bodies[1]['messages'][-1]['content'][0]['tool_use_id'], 'fictional-0')
                    self.assertEqual(bodies[1]['messages'][-2]['content'][-1]['type'], 'tool_use')
                    self.assertIn('input_schema', bodies[0]['tools'][0])
                else:
                    self.assertEqual(requests[0].get_header('Authorization'), 'Bearer fictional-secret')
                    self.assertEqual(bodies[1]['messages'][-1]['role'], 'tool')
                    self.assertEqual(bodies[1]['messages'][-1]['tool_call_id'], 'fictional-0')
                    self.assertEqual(bodies[1]['messages'][-2]['tool_calls'][0]['id'], 'fictional-0')
                    self.assertIn('function', bodies[0]['tools'][0])
                    self.assertNotIn('store', bodies[0])
                    if provider == 'deepseek':
                        self.assertEqual(bodies[1]['messages'][-2]['reasoning_content'], 'fictional-reasoning-note')
                    if provider == 'qwen':
                        self.assertIs(bodies[0]['enable_thinking'], False)
                persisted = '\n'.join(p.read_text(encoding='utf-8') for p in (root / 'runtime').rglob('*.json'))
                for secret in ('fictional-secret', 'fictional-assistant-note', 'fictional-reasoning-note', 'provider_context'):
                    self.assertNotIn(secret, persisted)
                with patch('urllib.request.OpenerDirector.open') as network:
                    self.assertEqual(run_agent(loaded, settings, transport=make_transport(settings, 'fictional-secret'))['status'],
                                     'NO_PENDING_TASKS')
                    network.assert_not_called()

    def test_unsafe_replies_never_execute_local_task(self):
        for protocol in ('chat', 'messages'):
            for problem in ('parallel', 'truncated', 'duplicate', 'early_final'):
                with self.subTest(protocol=protocol, problem=problem), tempfile.TemporaryDirectory() as directory:
                    loaded = self.setup_config(Path(directory))
                    reply = messages_reply if protocol == 'messages' else chat_reply
                    first = reply('process_task', {'task_ref': 'task-001'})
                    if problem == 'parallel':
                        blocks = first['content'] if protocol == 'messages' else first['choices'][0]['message']['tool_calls']
                        blocks.append(deepcopy(blocks[-1]))
                    elif problem == 'truncated':
                        if protocol == 'messages':
                            first['stop_reason'] = 'max_tokens'
                        else:
                            first['choices'][0]['finish_reason'] = 'length'
                    elif problem == 'early_final':
                        first = reply()
                    responses = [first]
                    if problem == 'duplicate':
                        responses = [reply('check_environment'), first]
                    settings = AgentSettings(provider='anthropic' if protocol == 'messages' else 'deepseek', model='fictional-model')
                    with patch('urllib.request.OpenerDirector.open', side_effect=lambda *a, **k: Response(responses.pop(0))):
                        result = run_agent(loaded, settings, transport=make_transport(settings, 'fictional-key'))
                    self.assertNotEqual(result['status'], 'COMPLETE')
                    self.assertEqual(result['code'], {'parallel': 'parallel_calls_rejected', 'truncated': 'model_response_incomplete',
                                                      'duplicate': 'duplicate_or_invalid_call', 'early_final': 'model_stopped_before_completion'}[problem])
                    self.assertFalse((loaded['_output'] / 'DEMO-001' / 'result.json').exists())

    def test_stop_during_provider_request_and_safe_http_error(self):
        for provider in ('deepseek', 'anthropic'):
            for problem in ('cancel', 'http'):
                with self.subTest(provider=provider, problem=problem), tempfile.TemporaryDirectory() as directory:
                    loaded = self.setup_config(Path(directory))
                    event = threading.Event()
                    def opened(request, **kwargs):
                        if problem == 'http':
                            raise urllib.error.HTTPError(request.full_url, 401, 'fictional-secret', {}, None)
                        event.set()
                        return Response((messages_reply if provider == 'anthropic' else chat_reply)('process_task', {'task_ref': 'task-001'}))
                    settings = AgentSettings(provider=provider, model='fictional-model')
                    with patch('urllib.request.OpenerDirector.open', side_effect=opened):
                        result = run_agent(loaded, settings, transport=make_transport(settings, 'fictional-secret'), cancel_event=event)
                    self.assertEqual(result['code'], 'interrupted' if problem == 'cancel' else 'model_auth_failed')
                    self.assertNotIn('fictional-secret', json.dumps(result))
                    self.assertFalse((loaded['_output'] / 'DEMO-001' / 'result.json').exists())

    def test_ui_provider_factory_and_memory_only_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = self.setup_config(root)
            ui = Dashboard(loaded)
            captured = []
            def fake_run(*args, **kwargs):
                captured.append((args[1].provider, kwargs['transport'].api_key, kwargs['transport'].backend))
                return {'status': 'INCOMPLETE'}
            for provider in ('deepseek', 'anthropic', 'openai_compatible'):
                with patch('groupbuy.dashboard.run_agent', side_effect=fake_run):
                    ui.start({'provider': provider, 'model': 'fictional-model', 'api_key': 'fictional-ui-key',
                              'base_url': 'https://api.example.invalid/v1'})
                    ui.worker.join(timeout=3)
                self.assertEqual(captured[-1][0], provider)
                self.assertNotIn('fictional-ui-key', json.dumps(ui.state()))
            for body in ({'provider': []}, {'base_url': []}, {'provider': 'unknown'},
                         {'provider': 'deepseek', 'base_url': 'https://user:pass@example.invalid/v1'}):
                with self.assertRaises(GateError):
                    ui.start(body)
            self.assertTrue(all('fictional-ui-key' not in p.read_text(encoding='utf-8') for p in root.rglob('*.json')))


class ProtocolValidationTests(unittest.TestCase):
    def test_malformed_and_unsupported_content_rejected(self):
        for body in ({}, {'choices': []}, {'choices': [{}, {}]}, {'choices': [{'message': {}, 'finish_reason': 'stop'}]}):
            with self.assertRaises(GateError):
                normalize_chat(body)
        for body in ({}, {'type': 'message', 'role': 'assistant', 'content': []}):
            with self.assertRaises(GateError):
                normalize_messages(body)
        for protocol in ('chat', 'messages'):
            body = messages_reply() if protocol == 'messages' else chat_reply()
            if protocol == 'messages':
                body['content'] = [{'type': 'thinking', 'thinking': 'fictional-text'}]
            else:
                body['choices'][0]['message']['refusal'] = 'fictional-refusal'
            with self.assertRaises(GateError):
                (normalize_messages if protocol == 'messages' else normalize_chat)(body)

    def test_local_compatible_http_and_redirect_rejected(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                requests.append(self.path)
                if self.path.startswith('/redirect/'):
                    self.send_response(302)
                    self.send_header('Location', '/stolen')
                    self.end_headers()
                    return
                self.rfile.read(int(self.headers['Content-Length']))
                body = json.dumps(chat_reply()).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            payload = {'model': 'fictional-model', 'instructions': 'fictional-instructions',
                       'input': [{'role': 'user', 'content': 'fictional-request'}], 'tools': [], 'max_output_tokens': 256}
            with patch.dict(os.environ, {'GROUPBUY_MODEL_PROXY_URL': '', 'OPENAI_PROXY_URL': ''}):
                result = ProviderTransport('fictional-key', 'openai_compatible', base + '/v1').create(payload, timeout_seconds=3)
                self.assertEqual(result['status'], 'completed')
                with self.assertRaisesRegex(GateError, 'model_redirect_rejected'):
                    ProviderTransport('fictional-key', 'openai_compatible', base + '/redirect').create(payload, timeout_seconds=3)
            self.assertEqual(requests, ['/v1/chat/completions', '/redirect/chat/completions'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


def stream_bytes(output, *, terminal=True, failed=False, include_output=True):
    events = [{'type': 'response.created', 'response': {'status': 'in_progress'}}]
    events += [{'type': 'response.output_item.done', 'output_index': i, 'item': item} for i, item in enumerate(output)]
    if terminal:
        final = {'status': 'incomplete' if failed else 'completed', 'usage': {'total_tokens': 3}}
        if include_output:
            final['output'] = output
        events.append({'type': 'response.incomplete' if failed else 'response.completed', 'response': final})
    return ''.join('event: ' + e['type'] + '\r\ndata: ' + json.dumps(e) + '\r\n\r\n' for e in events).encode()


class StreamResponse(Response):
    def __init__(self, data):
        self.stream = io.BytesIO(data)
    def getheader(self, name, default=''):
        return 'text/event-stream; charset=utf-8' if name == 'Content-Type' else default
    def readline(self, limit):
        return self.stream.readline(limit)


class ResponsesCompatibilityTests(unittest.TestCase):
    def test_real_loopback_sse_transport_and_truncated_stream(self):
        requests = []
        items = [{'type': 'function_call', 'call_id': 'a', 'name': 'connection_probe', 'arguments': '{}'}]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw = stream_bytes(items, terminal=not self.path.startswith('/broken'))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                # Split across lines/chunks as an actual SSE connection does.
                for i in range(0, len(raw), 17):
                    self.wfile.write(raw[i:i+17]); self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with patch.dict(os.environ, {'GROUPBUY_MODEL_PROXY_URL': ''}):
                for suffix, expected in (('/v1', 'MODEL_READY'), ('/broken', 'STOPPED')):
                    settings = AgentSettings(provider='responses_compatible', model='fictional-model',
                                             base_url=base+suffix, reasoning_effort='medium')
                    result = probe_model(settings, make_transport(settings, 'fictional-key'))
                    self.assertEqual(result['status'], expected)
            self.assertTrue(all(r['reasoning']['effort'] == 'medium' and r['stream'] is True for r in requests))
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)

    def test_codex_wire_shape_and_medium_preserve_typed_history(self):
        payload = {'model': 'fictional-model', 'instructions': 'fictional-instructions',
                   'input': [{'role': 'user', 'content': 'fictional-request'},
                             {'type': 'reasoning', 'encrypted_content': 'fictional-encrypted'},
                             {'type': 'function_call_output', 'call_id': 'a', 'output': '{}'}],
                   'tools': [], 'max_output_tokens': 2048}
        body = codex_responses_body(payload, 'medium')
        self.assertEqual(body['reasoning'], {'effort': 'medium'})
        self.assertIs(body['stream'], True)
        self.assertIs(body['store'], False)
        self.assertIs(body['parallel_tool_calls'], False)
        self.assertEqual(body['include'], ['reasoning.encrypted_content'])
        self.assertEqual(body['input'][0]['role'], 'developer')
        self.assertEqual(body['input'][1]['content'], [{'type': 'input_text', 'text': 'fictional-request'}])
        self.assertEqual(body['input'][2], payload['input'][1])
        self.assertNotIn('max_output_tokens', body)
        self.assertNotIn('instructions', body)
        self.assertNotIn('previous_response_id', body)
        for value in ('unknown', 3, []):
            with self.assertRaisesRegex(GateError, 'invalid_reasoning_effort'):
                AgentSettings(reasoning_effort=value)
        with self.assertRaisesRegex(GateError, 'invalid_reasoning_effort'):
            AgentSettings(provider='qwen', reasoning_effort='medium')

    def test_stream_done_items_or_full_output_and_truncation(self):
        items = [{'type': 'function_call', 'call_id': 'a', 'name': 'connection_probe', 'arguments': '{}'}]
        for include in (True, False):
            result = read_responses_stream(io.BytesIO(stream_bytes(items, include_output=include)), 3)
            self.assertEqual(result['output'], items)
        without_index = stream_bytes(items).replace(b'"output_index": 0, ', b'')
        self.assertEqual(read_responses_stream(io.BytesIO(without_index), 3)['output'], items)
        for raw in (stream_bytes(items, terminal=False), stream_bytes(items, failed=True),
                    b'data: [DONE]\n\n', b'data: bad-json\n\n', b'data: {"type":"error"}\n\n'):
            with self.subTest(raw=raw[:20]), self.assertRaises(GateError):
                read_responses_stream(io.BytesIO(raw), 3)
        with self.assertRaisesRegex(GateError, 'model_response_too_large'):
            read_responses_stream(io.BytesIO(b'data: ' + b'X' * (1024 * 1024)), 3)
        with patch('groupbuy.model_providers.time.monotonic', side_effect=[0, 4]):
            with self.assertRaisesRegex(GateError, 'model_connection_failed'):
                read_responses_stream(io.BytesIO(b': keepalive\n\n'), 3)

    def test_native_responses_multiround_sealed_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = ProviderLoopTests().setup_config(root)
            settings = AgentSettings(provider='responses_compatible', base_url='https://api.example.invalid/v1',
                                     model='fictional-model', reasoning_effort='medium')
            steps = [('check_environment', {}), ('get_status', {}),
                     ('process_task', {'task_ref': 'task-001'}), ('process_task', {'task_ref': 'task-002'}), (None, {})]
            streams = []
            reasoning = {'type': 'reasoning', 'id': 'reasoning-a', 'encrypted_content': 'fictional-encrypted'}
            for i, (name, args) in enumerate(steps):
                items = [{'type': 'function_call', 'name': name, 'arguments': json.dumps(args), 'call_id': f'fictional-{i}'}] if name else [
                         {'type': 'message', 'role': 'assistant', 'phase': 'final_answer',
                          'content': [{'type': 'output_text', 'text': 'fictional-assistant-note'}]}]
                if i == 0:
                    items.insert(0, reasoning)
                streams.append(stream_bytes(items, include_output=False))
            bodies = []
            def opened(request, **kwargs):
                self.assertEqual(request.full_url, 'https://api.example.invalid/v1/responses')
                bodies.append(json.loads(request.data))
                return StreamResponse(streams.pop(0))
            with patch('urllib.request.OpenerDirector.open', side_effect=opened):
                result = run_agent(loaded, settings, transport=make_transport(settings, 'fictional-secret'))
            self.assertEqual(result['status'], 'COMPLETE')
            self.assertEqual(result['backend'], 'RESPONSES_COMPATIBLE')
            self.assertEqual(result['reported_tokens'], 15)
            self.assertIn(reasoning, bodies[1]['input'])
            self.assertEqual(bodies[1]['input'][-1]['type'], 'function_call_output')
            self.assertEqual(bodies[1]['input'][-1]['call_id'], 'fictional-0')
            self.assertTrue(all(b['reasoning']['effort'] == 'medium' for b in bodies))
            persisted = '\n'.join(p.read_text(encoding='utf-8') for p in (root / 'runtime').rglob('*.json'))
            for secret in ('fictional-secret', 'fictional-encrypted', 'fictional-assistant-note', 'api.example.invalid'):
                self.assertNotIn(secret, persisted)
            for term in ('示例广场', 'fictional-secret', 'DEMO-001', str(root)):
                self.assertNotIn(term, json.dumps(bodies, ensure_ascii=False))

    def test_probe_all_protocols_and_cancellation_without_task_mutation(self):
        for provider in ('openai', 'deepseek', 'anthropic', 'responses_compatible'):
            with self.subTest(provider=provider):
                settings = AgentSettings(provider=provider, model='fictional-model',
                    base_url='https://api.example.invalid/v1' if provider == 'responses_compatible' else None)
                transport = make_transport(settings, 'fictional-secret')
                if provider in {'openai', 'responses_compatible'}:
                    items = [{'type': 'function_call', 'name': 'connection_probe', 'call_id': 'a', 'arguments': '{}'}]
                    response = StreamResponse(stream_bytes(items)) if provider == 'responses_compatible' else Response({'status': 'completed', 'output': items})
                else:
                    response = Response((messages_reply if provider == 'anthropic' else chat_reply)('connection_probe'))
                with patch('urllib.request.OpenerDirector.open', return_value=response):
                    result = probe_model(settings, transport)
                    self.assertEqual(result['status'], 'MODEL_READY')
                    self.assertNotIn('fictional-secret', json.dumps(result))
        event = threading.Event();event.set()
        settings = AgentSettings(provider='deepseek', model='fictional-model')
        with patch('urllib.request.OpenerDirector.open', return_value=Response(chat_reply('connection_probe'))):
            self.assertEqual(probe_model(settings, make_transport(settings, 'fictional-key'), event)['code'], 'interrupted')
        with patch('urllib.request.OpenerDirector.open', return_value=Response(chat_reply())):
            self.assertEqual(probe_model(settings, make_transport(settings, 'fictional-key'))['code'], 'model_tools_not_supported')

    def test_dashboard_probe_keeps_progress_and_no_saved_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = ProviderLoopTests().setup_config(root)
            ui = Dashboard(loaded)
            before = ui.state()['tasks']
            items = [{'type': 'function_call', 'name': 'connection_probe', 'call_id': 'a', 'arguments': '{}'}]
            with patch('urllib.request.OpenerDirector.open', return_value=StreamResponse(stream_bytes(items))):
                ui.start({'provider': 'responses_compatible', 'base_url': 'https://api.example.invalid/v1',
                          'model': 'fictional-model', 'reasoning_effort': 'medium', 'api_key': 'fictional-secret', 'check_only': True})
                ui.worker.join(timeout=3)
            self.assertEqual(ui.result['status'], 'MODEL_READY')
            self.assertEqual(ui.state()['tasks'], before)
            self.assertTrue(all('fictional-secret' not in p.read_text(encoding='utf-8') for p in root.rglob('*.json')))
