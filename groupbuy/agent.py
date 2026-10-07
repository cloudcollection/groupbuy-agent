"""Standalone Agent entry and an explicitly labelled offline loop demonstration."""
import json
import time
import uuid
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from .agent_runtime import AgentSettings, run_loop
from .model_providers import make_transport
from .agent_tools import CollectionTools
from .common import FileLock, GateError, atomic_json, load_json, path_from, utcnow
from .runner import Runner
from .scheduler import due_slot


class ScriptedDemoTransport:
    """Exercise real tools without an API key. This is not a language model."""
    def __init__(self):
        self.round = 0

    def create(self, payload, *, timeout_seconds):
        self.round += 1
        if self.round == 1:
            name, args = 'check_environment', {}
        elif self.round == 2:
            name, args = 'get_status', {}
        else:
            last = json.loads(payload['input'][-1]['output'])
            snapshot = last.get('snapshot', {})
            pending = [t for t in snapshot.get('tasks', []) if t['stage'] != 'COMPLETE']
            if not pending:
                return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'Scripted demo finished.'}]}]}
            name, args = 'process_task', {'task_ref': pending[0]['task_ref']}
        return {'status': 'completed', 'output': [{'type': 'function_call', 'name': name,
                'arguments': json.dumps(args), 'call_id': f'demo-call-{self.round}'}]}


def demo_config(config):
    # Explicit demo must never operate on real files or reuse live progress.
    if config['mode'] != 'offline' or config.get('synthetic') is not True:
        raise GateError('demo_requires_synthetic_config')
    fixture_root = (config['_root'] / 'examples' / 'fixtures').resolve()
    for task in config['tasks']:
        for key in ('path', 'evidence'):
            source = path_from(config['_root'], task['input'][key])
            if fixture_root not in source.parents or load_json(source).get('synthetic') is not True:
                raise GateError('demo_requires_synthetic_config')
    c = deepcopy(config)
    base = path_from(c['_root'], 'runtime/agent-demo/' + uuid.uuid4().hex, write=True)
    c['_output'], c['_progress'] = base / 'outputs', base / 'progress.json'
    return c


def run_agent(config, settings, *, allow_ui=False, demo=False, transport=None, driver_factory=None, cancel_event=None):
    if demo and allow_ui:
        raise GateError('demo_ui_rejected')
    c = demo_config(config) if demo else config
    if not demo and not settings.model:
        raise GateError('agent_model_required')
    transport = transport or (ScriptedDemoTransport() if demo else make_transport(settings))
    runner = Runner(c)
    # Serialise Agent sessions; each deterministic operation also takes the original progress lock.
    with FileLock(runner.progress_path.with_suffix('.agent.lock')):
        from .capture import CaptureReceiver
        context = CaptureReceiver(c, cancel_event=cancel_event) if c.get('_capture', {}).get('enabled') else nullcontext(None)
        with context as capture:
            tools = CollectionTools(runner, settings, allow_ui=allow_ui, driver_factory=driver_factory,
                                    capture=capture, cancel_event=cancel_event)
            result = run_loop(settings, tools, transport, demo=demo, cancel_event=cancel_event)
        result['at'] = utcnow()
        report_path = runner.output / 'agent-runs' / (uuid.uuid4().hex + '.json')
        atomic_json(report_path, result)
        # A local path is for the operator only; it is not sent to the model or saved in logs.
        return {**result, 'report_path': str(report_path)}


def tick_agent(config, settings, now, *, allow_ui=False, transport=None):
    if config['mode'] == 'live' and not config['schedule'].get('live_confirmed', False):
        raise GateError('live_schedule_not_validated')
    slot = due_slot(config, now)
    if not slot:
        return {'status': 'NOT_DUE'}
    runner = Runner(config)
    with FileLock(runner.progress_path.with_suffix('.lock')):
        state = runner.load_state()
        if slot in state['schedule_slots']:
            return {'status': 'ALREADY_CLAIMED'}
        state['schedule_slots'][slot] = 'CLAIMED'
        runner.save(state)
    try:
        return run_agent(config, settings, allow_ui=allow_ui, transport=transport)
    finally:
        with FileLock(runner.progress_path.with_suffix('.lock')):
            state = runner.load_state()
            state['schedule_slots'][slot] = 'FINISHED'
            runner.save(state)


def serve_agent(config, settings, *, allow_ui=False):
    if not config['schedule']['enabled']:
        raise GateError('schedule_disabled')
    if not settings.model:
        raise GateError('agent_model_required')
    transport = make_transport(settings)
    while True:
        result = tick_agent(config, settings, datetime.now(timezone.utc),
                            allow_ui=allow_ui, transport=transport)
        if result['status'] not in {'NOT_DUE', 'ALREADY_CLAIMED'}:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result['status'] != 'COMPLETE':
                # Manual input, risk, API failure and unfinished work stop the foreground service.
                return result
        time.sleep(20)
