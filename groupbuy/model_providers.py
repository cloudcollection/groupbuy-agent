"""Official public API protocols; credentials and native model history stay in memory."""
import copy
import json
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from .common import GateError


PROVIDERS = {
    'openai': {'label': 'OpenAI · Responses', 'protocol': 'responses',
               'base_url': 'https://api.openai.com/v1', 'key_env': 'OPENAI_API_KEY', 'model_env': 'OPENAI_MODEL'},
    'deepseek': {'label': 'DeepSeek', 'protocol': 'chat_completions',
                 'base_url': 'https://api.deepseek.com/v1', 'key_env': 'DEEPSEEK_API_KEY', 'model_env': 'DEEPSEEK_MODEL'},
    'qwen': {'label': '通义千问 · 阿里云百炼', 'protocol': 'chat_completions',
             'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
             'key_env': 'DASHSCOPE_API_KEY', 'model_env': 'QWEN_MODEL'},
    'kimi': {'label': 'Kimi · Moonshot', 'protocol': 'chat_completions',
             'base_url': 'https://api.moonshot.cn/v1', 'key_env': 'MOONSHOT_API_KEY', 'model_env': 'KIMI_MODEL'},
    'anthropic': {'label': 'Claude · Anthropic', 'protocol': 'messages',
                  'base_url': 'https://api.anthropic.com/v1', 'key_env': 'ANTHROPIC_API_KEY', 'model_env': 'ANTHROPIC_MODEL'},
    'openai_compatible': {'label': '其他 · OpenAI 兼容 API', 'protocol': 'chat_completions',
                          'base_url': None, 'key_env': 'GROUPBUY_API_KEY', 'model_env': 'GROUPBUY_MODEL'},
    'responses_compatible': {'label': 'Codex / Responses 兼容 API', 'protocol': 'responses_stream',
                             'base_url': None, 'key_env': 'GROUPBUY_API_KEY', 'model_env': 'GROUPBUY_MODEL'},
}


def validate_provider(provider, base_url=None):
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise GateError('invalid_model_provider')
    if base_url is None:
        return
    if (not isinstance(base_url, str) or not base_url or len(base_url) > 500
            or any(c.isspace() or ord(c) < 32 for c in base_url)):
        raise GateError('invalid_model_base_url')
    try:
        url = urlsplit(base_url)
        port = url.port
    except ValueError:
        raise GateError('invalid_model_base_url') from None
    local = url.hostname in {'127.0.0.1', 'localhost', '::1'}
    if (not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment
            or '\\' in base_url or '%' in url.netloc or port == 0
            or not (url.scheme == 'https' or provider in {'openai_compatible', 'responses_compatible'} and url.scheme == 'http' and local)
            or url.path.rstrip('/').endswith(('/chat/completions', '/messages', '/responses'))):
        raise GateError('invalid_model_base_url')
    if provider == 'openai' and base_url.rstrip('/') != PROVIDERS['openai']['base_url']:
        raise GateError('openai_endpoint_fixed')


def environment_defaults(provider=None):
    provider = provider or os.environ.get('GROUPBUY_PROVIDER') or 'openai'
    validate_provider(provider)
    spec = PROVIDERS[provider]
    base_url = os.environ.get('GROUPBUY_BASE_URL') or None
    return {'provider': provider, 'model': os.environ.get('GROUPBUY_MODEL') or os.environ.get(spec['model_env']) or None,
            'base_url': base_url, 'reasoning_effort': (os.environ.get('GROUPBUY_REASONING_EFFORT') or None)
            if provider in {'openai', 'responses_compatible'} else None}


def environment_key(provider):
    validate_provider(provider)
    # Never send another provider's specific key to the selected API.
    return os.environ.get('GROUPBUY_API_KEY') or os.environ.get(PROVIDERS[provider]['key_env'], '')


def provider_choices():
    return [{'id': ident, **spec} for ident, spec in PROVIDERS.items()]


def make_transport(settings, api_key=None):
    from .agent_runtime import OpenAITransport
    key = environment_key(settings.provider) if api_key is None else api_key
    if settings.provider == 'openai':
        return OpenAITransport(key, settings.reasoning_effort)
    return ProviderTransport(key, settings.provider, settings.base_url, settings.reasoning_effort)


def codex_responses_body(payload, reasoning_effort=None):
    """Public Codex HTTP wire shape, independently implemented; no login emulation."""
    history = [{'type': 'message', 'role': 'developer',
                'content': [{'type': 'input_text', 'text': payload['instructions']}]}]
    for item in payload['input']:
        item = copy.deepcopy(item)
        if not item.get('type') and item.get('role') in {'user', 'developer'}:
            item['type'] = 'message'
            item['content'] = [{'type': 'input_text', 'text': item['content']}]
        history.append(item)
    # Codex streaming servers can reject max_output_tokens: only send their supported wire fields.
    return {'model': payload['model'], 'stream': True, 'input': history,
            'tools': copy.deepcopy(payload['tools']), 'tool_choice': 'auto', 'parallel_tool_calls': False,
            'reasoning': {'effort': reasoning_effort} if reasoning_effort else None,
            'store': False, 'include': ['reasoning.encrypted_content']}


def read_responses_stream(response, timeout_seconds):
    """Only a completed SSE response permits tool execution; partial deltas never do."""
    total, data_lines, done = 0, [], {}
    deadline = time.monotonic() + timeout_seconds
    while True:
        if time.monotonic() >= deadline:
            raise GateError('model_connection_failed')
        line = response.readline(1024 * 1024 + 1)
        total += len(line)
        if total > 4 * 1024 * 1024 or len(line) > 1024 * 1024:
            raise GateError('model_response_too_large')
        if not line:
            raise GateError('model_response_incomplete')
        try:
            line = line.decode('utf-8').rstrip('\r\n')
        except UnicodeError:
            raise GateError('invalid_model_response') from None
        if line.startswith('data:'):
            data_lines.append(line[5:].lstrip(' '))
        elif not line and data_lines:
            raw, data_lines = '\n'.join(data_lines), []
            if raw == '[DONE]':
                raise GateError('model_response_incomplete')
            try:
                event = json.loads(raw)
            except ValueError:
                raise GateError('invalid_model_response') from None
            if not isinstance(event, dict):
                raise GateError('invalid_model_response')
            kind = event.get('type')
            if kind in {'error', 'response.failed', 'response.incomplete'}:
                raise GateError('model_response_incomplete')
            if kind == 'response.output_item.done':
                index, item = event.get('output_index', len(done)), event.get('item')
                if type(index) is not int or index < 0 or not isinstance(item, dict) or index in done:
                    raise GateError('invalid_model_response')
                done[index] = item
            if kind == 'response.completed':
                final = event.get('response')
                if not isinstance(final, dict) or final.get('status', 'completed') != 'completed':
                    raise GateError('model_response_incomplete')
                final = copy.deepcopy(final)
                if done:
                    if sorted(done) != list(range(len(done))):
                        raise GateError('invalid_model_response')
                    items = [done[i] for i in range(len(done))]
                    if 'output' in final and final['output'] != items:
                        raise GateError('invalid_model_response')
                    final['output'] = items
                final['status'] = 'completed'
                if not isinstance(final.get('output'), list):
                    raise GateError('invalid_model_response')
                return final


def probe_model(settings, transport, cancel_event=None):
    """One paid tool-call probe; no task files, UI actions or business data."""
    from .agent_tools import function
    result = {'provider': settings.provider, 'backend': transport.backend}
    try:
        if not settings.model:
            raise GateError('agent_model_required')
        response = transport.create({'model': settings.model,
            'instructions': 'Call connection_probe exactly once with an empty JSON object. Do not call any other tool.',
            'input': [{'role': 'user', 'content': 'Test tool calling without collecting any data.'}],
            'tools': [function('connection_probe', 'Verify the API tool-call protocol without performing actions.')],
            'tool_choice': 'auto', 'parallel_tool_calls': False, 'store': False, 'max_output_tokens': settings.max_output_tokens},
            timeout_seconds=settings.timeout_seconds)
        if cancel_event is not None and cancel_event.is_set():
            raise GateError('interrupted')
        if response.get('error') or response.get('status', 'completed') != 'completed':
            raise GateError('model_response_incomplete')
        output = response.get('output')
        if not isinstance(output, list) or any(not isinstance(i, dict) for i in output):
            raise GateError('invalid_model_response')
        calls = [i for i in output if i.get('type') == 'function_call']
        if len(calls) != 1 or calls[0].get('name') != 'connection_probe' or not isinstance(calls[0].get('call_id'), str) or not calls[0]['call_id']:
            raise GateError('model_tools_not_supported')
        if json.loads(calls[0].get('arguments', '')) != {}:
            raise GateError('invalid_tool_arguments')
        return {**result, 'status': 'MODEL_READY'}
    except Exception as error:
        return {**result, 'status': 'STOPPED', 'code': error.code if isinstance(error, GateError) else 'invalid_model_response'}


def chat_history(history):
    messages = []
    for item in history:
        if item.get('type') == 'provider_context' and item.get('protocol') == 'chat_completions':
            messages.append(copy.deepcopy(item['message']))
        elif item.get('type') == 'function_call_output':
            messages.append({'role': 'tool', 'tool_call_id': item['call_id'], 'content': item['output']})
        elif item.get('role') == 'user' and not item.get('type'):
            messages.append({'role': 'user', 'content': item['content']})
    return messages


def normalize_chat(result):
    choices = result.get('choices')
    if result.get('error') or not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise GateError('invalid_model_response')
    choice = choices[0]
    msg = choice.get('message')
    if not isinstance(msg, dict) or msg.get('role') != 'assistant' or msg.get('refusal'):
        raise GateError('invalid_model_response')
    calls = msg.get('tool_calls') or []
    if not isinstance(calls, list) or choice.get('finish_reason') != ('tool_calls' if calls else 'stop'):
        raise GateError('model_response_incomplete')
    native = {'role': 'assistant', 'content': msg.get('content')}
    if native['content'] is not None and not isinstance(native['content'], str):
        raise GateError('invalid_model_response')
    if 'reasoning_content' in msg:
        if msg['reasoning_content'] is not None and not isinstance(msg['reasoning_content'], str):
            raise GateError('invalid_model_response')
        native['reasoning_content'] = msg['reasoning_content']
    output = []
    if calls:
        native['tool_calls'] = []
        for call in calls:
            if not isinstance(call, dict) or call.get('type') != 'function' or not isinstance(call.get('function'), dict):
                raise GateError('invalid_model_response')
            fn = call['function']
            native['tool_calls'].append({'id': call.get('id'), 'type': 'function',
                                         'function': {'name': fn.get('name'), 'arguments': fn.get('arguments')}})
            output.append({'type': 'function_call', 'call_id': call.get('id'),
                           'name': fn.get('name'), 'arguments': fn.get('arguments')})
    else:
        if not isinstance(native['content'], str) or not native['content'].strip():
            raise GateError('invalid_model_response')
        output.append({'type': 'message', 'role': 'assistant',
                       'content': [{'type': 'output_text', 'text': native['content']}]})
    return {'status': 'completed', 'output': [
        {'type': 'provider_context', 'protocol': 'chat_completions', 'message': native}, *output],
        'usage': result.get('usage')}


def messages_history(history):
    messages = []
    for item in history:
        if item.get('type') == 'provider_context' and item.get('protocol') == 'messages':
            messages.append(copy.deepcopy(item['message']))
        elif item.get('type') == 'function_call_output':
            messages.append({'role': 'user', 'content': [{'type': 'tool_result',
                             'tool_use_id': item['call_id'], 'content': item['output']}]})
        elif item.get('role') == 'user' and not item.get('type'):
            messages.append({'role': 'user', 'content': item['content']})
    return messages


def normalize_messages(result):
    content = result.get('content')
    if (result.get('type') != 'message' or result.get('role') != 'assistant'
            or not isinstance(content, list) or not content):
        raise GateError('invalid_model_response')
    native, output = [], []
    for block in content:
        if not isinstance(block, dict):
            raise GateError('invalid_model_response')
        kind = block.get('type')
        if kind == 'tool_use':
            args = block.get('input')
            if not isinstance(args, dict):
                raise GateError('invalid_model_response')
            native.append({k: block.get(k) for k in ('type', 'id', 'name', 'input')})
            output.append({'type': 'function_call', 'call_id': block.get('id'), 'name': block.get('name'),
                           'arguments': json.dumps(args, ensure_ascii=False, allow_nan=False)})
        elif kind == 'text' and isinstance(block.get('text'), str):
            native.append({'type': 'text', 'text': block['text']})
        else:
            # Extended thinking / server tools are not enabled by this adapter.
            raise GateError('unsupported_model_content')
    has_calls = bool(output)
    if (has_calls and result.get('stop_reason') != 'tool_use' or
            not has_calls and result.get('stop_reason') not in {'end_turn', 'stop_sequence'}):
        raise GateError('model_response_incomplete')
    if not has_calls:
        if not any(b.get('text', '').strip() for b in native):
            raise GateError('invalid_model_response')
        output.append({'type': 'message', 'role': 'assistant', 'content': copy.deepcopy(native)})
    usage = result.get('usage') or {}
    total = sum(usage.get(k, 0) for k in ('input_tokens', 'output_tokens')) if isinstance(usage, dict) and all(
        type(usage.get(k, 0)) is int and usage.get(k, 0) >= 0 for k in ('input_tokens', 'output_tokens')) else 0
    return {'status': 'completed', 'output': [
        {'type': 'provider_context', 'protocol': 'messages', 'message': {'role': 'assistant', 'content': native}},
        *output], 'usage': {'total_tokens': total}}


@dataclass(repr=False)
class ProviderTransport:
    api_key: str = field(repr=False)
    provider: str
    base_url: str | None = None
    reasoning_effort: str | None = None

    def __post_init__(self):
        from .agent_runtime import OpenAITransport
        OpenAITransport(self.api_key)  # Reuse strict in-memory credential validation.
        validate_provider(self.provider, self.base_url)
        spec = PROVIDERS[self.provider]
        if spec['protocol'] == 'responses':
            raise GateError('invalid_model_provider')
        self.base_url = (self.base_url or spec['base_url'] or '').rstrip('/')
        if not self.base_url:
            raise GateError('model_base_url_required')
        self.backend = {'messages': 'ANTHROPIC_MESSAGES', 'chat_completions': 'CHAT_COMPLETIONS',
                        'responses_stream': 'RESPONSES_COMPATIBLE'}[spec['protocol']]

    def create(self, payload, *, timeout_seconds):
        from .agent_runtime import post_json
        spec = PROVIDERS[self.provider]
        proxy = os.environ.get('GROUPBUY_MODEL_PROXY_URL', '')
        if spec['protocol'] == 'responses_stream':
            return post_json(self.base_url + '/responses', codex_responses_body(payload, self.reasoning_effort),
                             {'Authorization': 'Bearer ' + self.api_key, 'Accept': 'text/event-stream'},
                             timeout_seconds, proxy, stream=True)
        if spec['protocol'] == 'messages':
            body = {'model': payload['model'], 'system': payload['instructions'],
                    'messages': messages_history(payload['input']), 'max_tokens': payload['max_output_tokens'],
                    'tools': [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']}
                              for t in payload['tools']],
                    'tool_choice': {'type': 'auto', 'disable_parallel_tool_use': True}}
            result = post_json(self.base_url + '/messages', body,
                               {'x-api-key': self.api_key, 'anthropic-version': '2023-06-01'}, timeout_seconds, proxy)
            return normalize_messages(result)
        body = {'model': payload['model'], 'messages': [{'role': 'system', 'content': payload['instructions']},
                                                       *chat_history(payload['input'])],
                'tools': [{'type': 'function', 'function': {k: t[k] for k in ('name', 'description', 'parameters')}}
                          for t in payload['tools']], 'tool_choice': 'auto', 'stream': False,
                'max_tokens': payload['max_output_tokens']}
        if self.provider == 'qwen':
            body['enable_thinking'] = False
        # Providers differ on strict/parallel flags; host checks every call regardless.
        result = post_json(self.base_url + '/chat/completions', body,
                           {'Authorization': 'Bearer ' + self.api_key}, timeout_seconds, proxy)
        return normalize_chat(result)
