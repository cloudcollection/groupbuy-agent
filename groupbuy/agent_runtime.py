"""Independent Responses API loop; no Codex process or arbitrary shell tools."""
import copy
import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from .common import GateError, load_json


INSTRUCTIONS = """You orchestrate a local public group-buying collection batch.
Use check_environment, then get_status. Process the first unfinished task_ref in
configuration order. Use collect_ui only for COLLECT_UI or RESUME_UI; pass resume
true only for RESUME_UI. Use process_task only for PROCESS_LOCAL. Recheck status.
Use export_har for EXPORT_HAR; it reads the local normally reported traffic.
Task references are opaque; configuration, evidence and business data remain local.
All tools execute fixed, validated local code. Never invent task references,
change settings, bypass quality gates, retry a stopped task, or request credentials.
WAITING_INPUT means a human must provide a normal local export and restart.
UI_REQUIRED, UI_UNAVAILABLE and ATTENTION_REQUIRED also require human attention.
Finish when the batch is complete. Your text cannot mark any task complete.
"""


@dataclass(frozen=True)
class AgentSettings:
    model: str | None = None
    max_rounds: int = 48
    max_output_tokens: int = 2048
    timeout_seconds: float = 60
    max_elapsed_seconds: float = 1800
    max_ui_segments: int = 3

    def __post_init__(self):
        for name, lo, hi in [('max_rounds', 1, 128), ('max_output_tokens', 256, 32768),
                             ('max_ui_segments', 1, 10)]:
            n = getattr(self, name)
            if type(n) is not int or not lo <= n <= hi:
                raise GateError('invalid_agent_settings')
        for name, lo, hi in [('timeout_seconds', 1, 120), ('max_elapsed_seconds', 1, 7200)]:
            n = getattr(self, name)
            if type(n) not in (int, float) or not math.isfinite(n) or not lo <= n <= hi:
                raise GateError('invalid_agent_settings')
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()
                                       or len(self.model) > 200 or any(c.isspace() for c in self.model)):
            raise GateError('invalid_agent_model')


def load_settings(path):
    raw = load_json(path)
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise GateError('invalid_agent_settings')
    allowed = set(AgentSettings.__dataclass_fields__) | {'version'}
    if set(raw) - allowed:
        raise GateError('invalid_agent_settings')
    values = {k: v for k, v in raw.items() if k != 'version'}
    if not values.get('model'):
        values['model'] = os.environ.get('OPENAI_MODEL') or None
    return AgentSettings(**values)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GateError('model_redirect_rejected')


@dataclass(repr=False)
class OpenAITransport:
    api_key: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise GateError('api_key_required')
        if any(c.isspace() for c in self.api_key):
            raise GateError('invalid_api_key')

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get('OPENAI_API_KEY', ''))

    def create(self, payload, *, timeout_seconds):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(body) > 8 * 1024 * 1024:
            raise GateError('model_context_limit')
        request = urllib.request.Request('https://api.openai.com/v1/responses', data=body,
                                         headers={'Authorization': 'Bearer ' + self.api_key,
                                                  'Content-Type': 'application/json'}, method='POST')
        # Endpoint is fixed. No redirects, request capture, private API or global proxy changes.
        # Never inherit the capture/system proxy. Optional model proxy is separate.
        proxy_url = os.environ.get('OPENAI_PROXY_URL', '')
        if proxy_url:
            from urllib.parse import urlsplit
            proxy = urlsplit(proxy_url)
            if proxy.scheme not in {'http', 'https'} or not proxy.hostname or proxy.username or proxy.password:
                raise GateError('invalid_model_proxy')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {'https': proxy_url} if proxy_url else {}), NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                data = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            error.close()  # Do not log error bodies, headers, requests or credentials.
            code = ('model_auth_failed' if error.code in (401, 403) else
                    'model_rate_limited' if error.code == 429 else 'model_http_failed')
            raise GateError(code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise GateError('model_connection_failed') from None
        if len(data) > 4 * 1024 * 1024:
            raise GateError('model_response_too_large')
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError):
            raise GateError('invalid_model_response') from None
        if not isinstance(result, dict):
            raise GateError('invalid_model_response')
        return result


HALTS = {'WAITING_INPUT', 'UI_REQUIRED', 'UI_UNAVAILABLE', 'ATTENTION_REQUIRED'}


def run_loop(settings, tools, transport, *, demo=False, on_event=None, cancel_event=None):
    """Only host-verified results enter the report; model prose is not persisted."""
    if not demo and not settings.model:
        raise GateError('agent_model_required')
    rounds, calls, tokens = 0, 0, 0
    start = time.monotonic()
    seen_calls = set()
    history = [{'role': 'user', 'content': 'Execute the next configured collection batch.'}]

    def report(status, code=None):
        snapshot = tools.snapshot()
        result = {'schema': 'groupbuy.agent-run.v1',
                  'backend': 'SCRIPTED_DEMO' if demo else 'OPENAI_RESPONSES',
                  'status': status, 'rounds': rounds, 'tool_calls': calls,
                  'reported_tokens': tokens, 'batch': snapshot}
        if code:
            result['code'] = code
        return result

    try:
        snapshot = tools.snapshot()
        if snapshot['status'] in HALTS:
            return report(snapshot['status'], snapshot.get('code'))
        if snapshot['status'] == 'NO_PENDING_TASKS':
            return report('NO_PENDING_TASKS')
        for _ in range(settings.max_rounds):
            if cancel_event is not None and cancel_event.is_set():
                return report('INTERRUPTED', 'interrupted')
            remaining = settings.max_elapsed_seconds - (time.monotonic() - start)
            if remaining <= 0:
                return report('INCOMPLETE', 'agent_time_limit')
            rounds += 1
            if on_event:
                on_event({'event': 'model_round', 'round': rounds})
            response = transport.create({'model': settings.model or 'scripted-demo',
                                         'instructions': INSTRUCTIONS, 'input': copy.deepcopy(history),
                                         'tools': tools.schemas(), 'tool_choice': 'auto',
                                         'parallel_tool_calls': False, 'store': False,
                                         'max_output_tokens': settings.max_output_tokens},
                                        timeout_seconds=min(settings.timeout_seconds, remaining))
            if cancel_event is not None and cancel_event.is_set():
                return report('INTERRUPTED', 'interrupted')
            if response.get('error') or response.get('status', 'completed') != 'completed':
                raise GateError('model_response_incomplete')
            output = response.get('output')
            if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
                raise GateError('invalid_model_response')
            usage = response.get('usage') or {}
            n = usage.get('total_tokens') if isinstance(usage, dict) else None
            if type(n) is int and n >= 0:
                tokens += n
            # Preserve reasoning and all output items for stateless multi-round continuity.
            history.extend(copy.deepcopy(output))
            pending = [item for item in output if item.get('type') == 'function_call']
            if len(pending) > 1:
                raise GateError('parallel_calls_rejected')
            if not pending:
                has_message = any(item.get('type') == 'message' for item in output)
                snapshot = tools.snapshot()
                if has_message and snapshot['status'] == 'BATCH_COMPLETE':
                    return report('COMPLETE')
                return report('INCOMPLETE', 'model_stopped_before_completion')
            call = pending[0]
            ident = call.get('call_id')
            if not isinstance(ident, str) or not ident or ident in seen_calls:
                raise GateError('duplicate_or_invalid_call')
            seen_calls.add(ident)
            name = call.get('name')
            if not isinstance(name, str):
                raise GateError('invalid_model_response')
            arguments = call.get('arguments')
            try:
                if not isinstance(arguments, str) or len(arguments) > 4096:
                    raise ValueError
                args = json.loads(arguments)
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                result = {'ok': False, 'code': 'invalid_tool_arguments'}
            else:
                calls += 1
                result = tools.execute(name, args)
            history.append({'type': 'function_call_output', 'call_id': ident,
                            'output': json.dumps(result, ensure_ascii=False, allow_nan=False)})
            snapshot = tools.snapshot()
            if snapshot['status'] in HALTS:
                return report(snapshot['status'], snapshot.get('code'))
        return report('INCOMPLETE', 'agent_round_limit')
    except KeyboardInterrupt:
        return report('INTERRUPTED', 'interrupted')
    except Exception as error:
        code = error.code if isinstance(error, GateError) else 'agent_operation_failed'
        return report('STOPPED', code)
