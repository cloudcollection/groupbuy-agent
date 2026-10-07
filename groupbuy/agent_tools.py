"""Narrow local capabilities. Neither raw files nor business text reach the LLM."""
import importlib.util
import os
from collections import Counter
from .common import FileLock, GateError, path_from


def function(name, description, properties=None):
    properties = properties or {}
    return {'type': 'function', 'name': name, 'description': description, 'strict': True,
            'parameters': {'type': 'object', 'properties': properties,
                           'required': list(properties), 'additionalProperties': False}}


class CollectionTools:
    def __init__(self, runner, settings, *, allow_ui=False, driver_factory=None, capture=None, cancel_event=None):
        self.runner, self.settings, self.allow_ui = runner, settings, allow_ui
        self.driver_factory, self.driver = driver_factory, None
        self.capture = capture
        self.cancel_event = cancel_event
        self.checked_environment = False
        self.ui_unavailable = False
        self.waiting_after_ui = False
        self.fatal_code = None
        self.segment_counts = Counter()
        state = self._state()
        selected = [t for t in runner.c['tasks'] if state['tasks'][t['task_id']]['status']
                    in {'PENDING', 'RUNNING', 'REVIEW', 'STOPPED'}][:runner.c['runtime']['batch_size']]
        self.tasks = {f'task-{i:03}': task for i, task in enumerate(selected, 1)}

    def _state(self):
        with FileLock(self.runner.progress_path.with_suffix('.lock')):
            state = self.runner.load_state()
            changed = False
            for task in self.runner.c['tasks']:
                record = state['tasks'][task['task_id']]
                if record['status'] in {'COMPLETE', 'RUNNING'}:
                    if self.runner._recover(task, record):
                        changed = True
                    elif record['status'] == 'RUNNING':
                        # No committed artifact: require explicit operator retry after interruption.
                        record.update(status='STOPPED', error='interrupted')
                        record.pop('ui_evidence', None)
                        changed = True
            if changed:
                self.runner.save(state)
            return state

    def _stage(self, task, record):
        if record['status'] == 'COMPLETE':
            return 'COMPLETE'
        if record['status'] == 'REVIEW' and record.get('error') == 'scroll_budget':
            return 'RESUME_UI'
        if record['status'] != 'PENDING':
            return 'BLOCKED'
        if self.runner.c['mode'] == 'live' and not record.get('ui_evidence'):
            return 'COLLECT_UI'
        if task['input']['kind'] == 'reqable':
            if not record.get('capture_session'):
                return 'COLLECT_UI'
            if record.get('auto_har'):
                from .capture import capture_source
                capture_source(self.runner.c, task, record)
                return 'PROCESS_LOCAL'
            return 'EXPORT_HAR'
        source = path_from(self.runner.c['_root'], task['input']['path'])
        proof = (self.runner.output / record['ui_evidence'] if record.get('ui_evidence') else
                 path_from(self.runner.c['_root'], task['input']['evidence']))
        return 'PROCESS_LOCAL' if source.is_file() and proof.is_file() else 'WAITING_INPUT'

    def snapshot(self):
        state = self._state()
        rows = [{'task_ref': ref, 'status': state['tasks'][t['task_id']]['status'],
                 'stage': self._stage(t, state['tasks'][t['task_id']])} for ref, t in self.tasks.items()]
        status, code = 'READY', None
        active = {t['task_id']: state['tasks'][t['task_id']] for t in self.runner.c['tasks']}
        blocked = [r for r in active.values() if r['status'] == 'STOPPED' or
                   r['status'] == 'REVIEW' and r.get('error') != 'scroll_budget']
        if self.fatal_code:
            status, code = 'ATTENTION_REQUIRED', self.fatal_code
        elif blocked:
            status, code = 'ATTENTION_REQUIRED', 'manual_retry_required'
        elif any(r['status'] == 'REVIEW' and r.get('error') == 'scroll_budget'
                 for ident, r in active.items() if ident not in {t['task_id'] for t in self.tasks.values()}):
            status, code = 'ATTENTION_REQUIRED', 'review_outside_batch'
        elif self.waiting_after_ui:
            status, code = 'WAITING_INPUT', 'local_export_required'
        elif self.ui_unavailable:
            status, code = 'UI_UNAVAILABLE', 'optional_ui_dependencies_missing'
        elif not rows:
            status = 'NO_PENDING_TASKS'
        elif all(r['stage'] == 'COMPLETE' for r in rows):
            status = 'BATCH_COMPLETE'
        else:
            current = next(r for r in rows if r['stage'] != 'COMPLETE')
            if current['stage'] == 'WAITING_INPUT':
                status, code = 'WAITING_INPUT', 'local_input_required'
            elif current['stage'] in {'COLLECT_UI', 'RESUME_UI'}:
                if not self.allow_ui:
                    status, code = 'UI_REQUIRED', 'ui_not_enabled'
                elif self.segment_counts[current['task_ref']] >= self.settings.max_ui_segments:
                    status, code = 'ATTENTION_REQUIRED', 'ui_segment_limit'
        counts = dict(Counter(state['tasks'][t['task_id']]['status'] for t in self.runner.c['tasks']))
        result = {'status': status, 'mode': self.runner.c['mode'], 'tasks': rows, 'task_counts': counts}
        if code:
            result['code'] = code
        return result

    def schemas(self):
        ref = {'task_ref': {'type': 'string', 'description': 'Opaque reference from get_status.'}}
        return [function('check_environment', 'Check local core and optional UI dependencies.'),
                function('get_status', 'Get batch stages and verified progress, without task text or raw data.'),
                function('process_task', 'Validate local evidence and responses, export one task and commit progress.', ref),
                function('export_har', 'Finish the normally reported local HAR snapshot after UI evidence is ready.', ref),
                function('collect_ui', 'Execute the configured observed UI flow for the next task.',
                         {**ref, 'resume': {'type': 'boolean', 'description': 'True only for RESUME_UI.'}})]

    def _current(self, ref):
        snapshot = self.snapshot()
        if snapshot['status'] != 'READY':
            raise GateError('agent_batch_not_ready')
        first = next(r for r in snapshot['tasks'] if r['stage'] != 'COMPLETE')
        if ref not in self.tasks or first['task_ref'] != ref:
            raise GateError('task_outside_current_batch')
        if not self.checked_environment:
            raise GateError('environment_check_required')
        return self.tasks[ref], first['stage']

    def execute(self, name, arguments):
        schemas = {t['name']: t['parameters'] for t in self.schemas()}
        try:
            if name not in schemas:
                raise GateError('unknown_agent_tool')
            schema = schemas[name]
            if set(arguments) != set(schema['properties']):
                raise GateError('invalid_tool_arguments')
            for key, prop in schema['properties'].items():
                expected = str if prop['type'] == 'string' else bool
                if type(arguments[key]) is not expected:
                    raise GateError('invalid_tool_arguments')
            details = {}
            if name == 'check_environment':
                available = self.driver_factory is not None or os.name == 'nt' and all(
                    importlib.util.find_spec(x) for x in ('pywinauto', 'rapidocr_onnxruntime', 'psutil', 'win32gui'))
                self.checked_environment = True
                if self.allow_ui and self.runner.c['mode'] == 'live' and not available:
                    self.ui_unavailable = True
                details = {'core_ready': True, 'ui_dependencies_available': bool(available), 'live_verified': False}
            elif name != 'get_status':
                ref = arguments['task_ref']
                task, stage = self._current(ref)
                if name == 'process_task':
                    if stage != 'PROCESS_LOCAL':
                        raise GateError('local_input_or_ui_required')
                    result = self.runner.run(offline=True, task_ids=[task['task_id']])
                    details = {'processed': [{'task_ref': ref, **{k: v for k, v in row.items() if k != 'task_id'}}
                                             for row in result['processed']]}
                elif name == 'export_har':
                    if stage != 'EXPORT_HAR' or self.capture is None:
                        raise GateError('capture_input_not_ready')
                    with FileLock(self.runner.progress_path.with_suffix('.lock')):
                        state = self.runner.load_state()
                        record = state['tasks'][task['task_id']]
                        try:
                            record['auto_har'] = self.capture.seal(task, record)
                        except Exception as error:
                            record.update(status='STOPPED', error=error.code if isinstance(error, GateError) else 'capture_failed')
                            raise
                        finally:
                            self.runner.save(state)
                    details = {'status': 'HAR_READY'}
                else:
                    if not self.allow_ui or self.runner.c['mode'] != 'live':
                        raise GateError('ui_not_enabled')
                    if stage not in {'COLLECT_UI', 'RESUME_UI'} or arguments['resume'] != (stage == 'RESUME_UI'):
                        raise GateError('unsafe_scroll_resume')
                    if self.driver is None:
                        if self.driver_factory:
                            self.driver = self.driver_factory()
                        else:
                            from .windows_ui import WindowsDriver
                            self.driver = WindowsDriver(cancel_event=self.cancel_event)
                    if task['input']['kind'] == 'reqable':
                        if self.capture is None:
                            raise GateError('capture_receiver_required')
                        with FileLock(self.runner.progress_path.with_suffix('.lock')):
                            state = self.runner.load_state()
                            record = state['tasks'][task['task_id']]
                            prior = record.get('capture_session') if arguments['resume'] else None
                            record['capture_session'] = self.capture.arm(task, prior)
                            record.pop('auto_har', None)
                            self.runner.save(state)
                    self.segment_counts[ref] += 1
                    result = self.runner.ui_segment(task['task_id'], self.driver, arguments['resume'])
                    details = {k: v for k, v in result.items() if k != 'task_id'}
                    if result['status'] == 'UI_READY':
                        self.waiting_after_ui = task['input']['kind'] != 'reqable'
            return {'ok': True, **details, 'snapshot': self.snapshot()}
        except Exception as error:
            # Unknown exception strings can include paths, OCR or credentials; never return them.
            code = error.code if isinstance(error, GateError) else 'agent_tool_failed'
            if code not in {'unknown_agent_tool', 'invalid_tool_arguments', 'environment_check_required',
                            'task_outside_current_batch', 'unsafe_scroll_resume', 'agent_batch_not_ready',
                            'local_input_or_ui_required', 'ui_not_enabled'}:
                self.fatal_code = code
            return {'ok': False, 'code': code}
