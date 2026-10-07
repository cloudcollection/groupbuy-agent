"""Serial batches, per-task transactions, checkpoint recovery and safe status logs."""
import uuid
from pathlib import Path
from .adapters import get_adapter
from .common import FileLock, GateError, atomic_json, load_json, path_from, utcnow
from .config import task_hash
from .exporter import export_result, file_hash, verify_export
from .local_input import read_responses
from .quality import validate_result


class Runner:
    def __init__(self, config):
        self.c = config
        self.progress_path = config['_progress']
        self.output = config['_output']

    def load_state(self):
        state = load_json(self.progress_path) if self.progress_path.exists() else {'version': 1, 'tasks': {}, 'schedule_slots': {}}
        if state.get('version') != 1:
            raise GateError('invalid_progress')
        for task in self.c['tasks']:
            ident = task['task_id']
            h = task_hash(task)
            record = state['tasks'].get(ident)
            if record and record['task_hash'] != h:
                raise GateError('task_configuration_changed')
            if not record:
                if task['status'] in {'RUNNING', 'COMPLETE'}:
                    raise GateError('status_requires_verified_progress')
                state['tasks'][ident] = {'task_hash': h, 'status': task['status'], 'attempts': 0}
        return state

    def save(self, state):
        atomic_json(self.progress_path, state)

    def log(self, task_id, status, code=None):
        # No exception string, query, response, absolute path, URL or account data.
        from .common import digest
        import json
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / 'status.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'at': utcnow(), 'task_ref': digest(task_id)[:16],
                                'status': status, 'code': code}) + '\n')

    def plan(self):
        state = self.load_state()
        return [{'task_id': t['task_id'], 'status': state['tasks'][t['task_id']]['status']}
                for t in self.c['tasks'] if state['tasks'][t['task_id']]['status'] in {'PENDING', 'RUNNING'}][:self.c['runtime']['batch_size']]

    def _artifact_path(self, record):
        relative = record.get('artifact_dir')
        if not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise GateError('invalid_artifact_reference')
        path = (self.output / relative).resolve()
        if self.output != path and self.output not in path.parents:
            raise GateError('invalid_artifact_reference')
        return path

    def _recover(self, task, record):
        if not record.get('artifact_dir'):
            return False
        target = self._artifact_path(record)
        if not (target / 'manifest.json').exists():
            if record['status'] == 'COMPLETE':
                raise GateError('completed_artifact_missing')
            return False
        result, manifest = verify_export(target)
        if record.get('manifest_sha256') and file_hash(target / 'manifest.json') != record['manifest_sha256']:
            raise GateError('artifact_integrity_failure')
        if record.get('result_digest') and record['result_digest'] != manifest['result_digest']:
            raise GateError('artifact_integrity_failure')
        if result['task_hash'] != task_hash(task):
            raise GateError('artifact_task_mismatch')
        validate_result(task, result)
        record.update(status='COMPLETE', result_digest=manifest['result_digest'],
                      manifest_sha256=file_hash(target / 'manifest.json'))
        return True

    def _ui(self, task, record, adapter, driver, prior=None):
        if prior is None:
            record.pop('ui_evidence', None)
        e = adapter.collect_ui(task, self.c['runtime'], driver, prior)
        relative = f"{task['task_id']}/ui/{uuid.uuid4().hex}.json"
        atomic_json(self.output / relative, e)
        record['ui_evidence'] = relative
        if e['stop_reason'] == 'scroll_budget':
            raise GateError('scroll_budget')
        return e

    def ui_segment(self, task_id, driver, resume=False):
        with FileLock(self.progress_path.with_suffix('.lock')):
            state = self.load_state()
            task = next((t for t in self.c['tasks'] if t['task_id'] == task_id), None)
            if not task:
                raise GateError('unknown_task')
            record = state['tasks'][task_id]
            if record['status'] == 'COMPLETE':
                raise GateError('task_already_complete')
            prior = None
            if resume:
                if record.get('error') != 'scroll_budget' or not record.get('ui_evidence'):
                    raise GateError('unsafe_scroll_resume')
                prior = load_json(self.output / record['ui_evidence'])
            elif record['status'] != 'PENDING':
                raise GateError('manual_retry_required')
            record['status'] = 'RUNNING'
            if not resume:
                record.pop('ui_evidence', None)
            self.save(state)
            try:
                e = self._ui(task, record, get_adapter(task['platform']), driver, prior)
                record.update(status='PENDING', error=None)
                return {'task_id': task_id, 'status': 'UI_READY', 'stop_reason': e['stop_reason']}
            except (Exception, KeyboardInterrupt) as error:
                code = error.code if isinstance(error, GateError) else 'interrupted' if isinstance(error, KeyboardInterrupt) else 'operation_failed'
                record.update(status='REVIEW' if code == 'scroll_budget' else 'STOPPED', error=code)
                if code != 'scroll_budget':
                    record.pop('ui_evidence', None)
                return {'task_id': task_id, 'status': record['status'], 'code': code}
            finally:
                self.save(state)

    def retry(self, task_id):
        with FileLock(self.progress_path.with_suffix('.lock')):
            state = self.load_state()
            if task_id not in state['tasks'] or state['tasks'][task_id]['status'] not in {'STOPPED', 'REVIEW'}:
                raise GateError('task_not_retryable')
            record = state['tasks'][task_id]
            task = next(t for t in self.c['tasks'] if t['task_id'] == task_id)
            if task['input']['kind'] == 'reqable':
                # A manual retry starts a fresh normal search; interrupted capture is immutable.
                for key in ('ui_evidence', 'capture_session', 'auto_har'):
                    record.pop(key, None)
            # Risk/focus interrupted UI must be freshly observed and searched.
            if record.get('error') in {'verification_or_login', 'foreground_changed', 'window_changed', 'interrupted'}:
                record.pop('ui_evidence', None)
            record.update(status='PENDING', error=None)
            self.save(state)
            return {'task_id': task_id, 'status': 'PENDING'}

    def run(self, driver=None, offline=False, schedule_slot=None, task_ids=None):
        # A caller may narrow a batch, but must retain all global failure gates.
        if task_ids is not None and (not task_ids or not set(task_ids) <= {t['task_id'] for t in self.c['tasks']}):
            raise GateError('unknown_task')
        with FileLock(self.progress_path.with_suffix('.lock')):
            state = self.load_state()
            if schedule_slot:
                if schedule_slot in state['schedule_slots']:
                    return {'status': 'ALREADY_CLAIMED', 'processed': []}
                # Claim before work: a crash cannot launch the same timed batch twice.
                state['schedule_slots'][schedule_slot] = 'CLAIMED'
                self.save(state)
            processed = []
            try:
                # Verify completed artifacts and recover committed running results first.
                for task in self.c['tasks']:
                    record = state['tasks'][task['task_id']]
                    if record['status'] in {'COMPLETE', 'RUNNING'}:
                        if self._recover(task, record):
                            self.save(state)
                if any(state['tasks'][t['task_id']]['status'] in {'STOPPED', 'REVIEW'} for t in self.c['tasks']):
                    return {'status': 'ATTENTION_REQUIRED', 'processed': []}
                for task in self.c['tasks']:
                    record = state['tasks'][task['task_id']]
                    if record['status'] not in {'PENDING', 'RUNNING'}:
                        continue
                    if task_ids is not None and task['task_id'] not in task_ids:
                        continue
                    if len(processed) >= self.c['runtime']['batch_size']:
                        break
                    record.update(status='RUNNING', attempts=record['attempts'] + 1)
                    record.pop('result_digest', None)
                    record.pop('manifest_sha256', None)
                    record['artifact_dir'] = f"{task['task_id']}/results/{uuid.uuid4().hex}"
                    self.save(state)
                    try:
                        adapter = get_adapter(task['platform'])
                        if self.c['mode'] == 'live' and not offline:
                            if driver is None:
                                raise GateError('live_driver_required')
                            record.pop('ui_evidence', None)
                            self.save(state)
                            evidence = self._ui(task, record, adapter, driver)
                            self.save(state)
                        elif record.get('ui_evidence'):
                            evidence = load_json(self.output / record['ui_evidence'])
                        else:
                            evidence = load_json(path_from(self.c['_root'], task['input']['evidence']))
                        adapter.validate_evidence(task, evidence)
                        if task['input']['kind'] == 'reqable':
                            from .capture import capture_source
                            source = capture_source(self.c, task, record)
                        else:
                            source = path_from(self.c['_root'], task['input']['path'])
                        if not source.is_file():
                            raise GateError('input_not_ready')
                        kind = 'har' if task['input']['kind'] == 'reqable' else task['input']['kind']
                        entries = list(read_responses(source, kind, evidence['capture_start'], evidence['capture_end']))
                        result = adapter.parse(task, entries, evidence)
                        result['task_hash'] = task_hash(task)
                        # UI proof is a narrow, sanitized record; no OCR dump or screenshot.
                        result['ui_evidence'] = {k: evidence[k] for k in (
                            'schema', 'task_id', 'task_hash', 'capture_start', 'capture_end',
                            'filter_frame_hash', 'final_frame_hash', 'list_hash', 'bottom_text', 'stop_reason')}
                        result = validate_result(task, result)
                        manifest = export_result(result, task, self._artifact_path(record))
                        record.update(status='COMPLETE', result_digest=manifest['result_digest'], error=None,
                                      manifest_sha256=file_hash(self._artifact_path(record) / 'manifest.json'))
                        self.save(state)
                        self.log(task['task_id'], 'COMPLETE')
                        processed.append({'task_id': task['task_id'], 'status': 'COMPLETE',
                                          'shops': len(result['shops']), 'products': len(result['products'])})
                    except (Exception, KeyboardInterrupt) as error:
                        code = error.code if isinstance(error, GateError) else 'interrupted' if isinstance(error, KeyboardInterrupt) else 'operation_failed'
                        if code == 'interrupted':
                            record.pop('ui_evidence', None)
                        record.update(status='REVIEW' if code in {'scroll_budget', 'pagination_gap', 'conflicting_duplicate_page'} else 'STOPPED', error=code)
                        self.save(state)
                        self.log(task['task_id'], record['status'], code)
                        processed.append({'task_id': task['task_id'], 'status': record['status'], 'code': code})
                        break  # A risk, interruption or failed gate always stops the batch.
                return {'status': 'COMPLETE' if all(x['status'] == 'COMPLETE' for x in processed) else 'ATTENTION_REQUIRED', 'processed': processed}
            finally:
                if schedule_slot:
                    state['schedule_slots'][schedule_slot] = 'FINISHED'
                    self.save(state)
