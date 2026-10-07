"""Reqable's official report-server protocol, loopback only, projected before disk."""
import gzip
import hmac
import io
import json
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .adapters import get_adapter
from .common import FileLock, GateError, atomic_json, load_json, moment, path_from, utcnow
from .config import task_hash
from .exporter import file_hash
from .local_input import params

MAX_REPORT_BYTES = 8 * 1024 * 1024


def validate_capture_config(config):
    raw = config.get('capture', {'enabled': False})
    if not isinstance(raw, dict) or type(raw.get('enabled')) is not bool:
        raise GateError('invalid_capture_config')
    if not raw['enabled']:
        if any(t['input']['kind'] == 'reqable' for t in config['tasks']):
            raise GateError('capture_not_enabled')
        config['_capture'] = {'enabled': False}
        return
    if set(raw) - {'enabled', 'port', 'settings_path', 'storage_dir', 'settle_seconds', 'timeout_seconds'}:
        raise GateError('invalid_capture_config')
    c = {'port': 8765, 'settings_path': 'runtime/capture/receiver.local.json',
         'storage_dir': 'runtime/capture', 'settle_seconds': 2, 'timeout_seconds': 30, **raw}
    if type(c['port']) is not int or not 1024 <= c['port'] <= 65535:
        raise GateError('invalid_capture_port')
    if (type(c['settle_seconds']) not in (int, float) or type(c['timeout_seconds']) not in (int, float)
            or not 0 <= c['settle_seconds'] <= c['timeout_seconds'] <= 120):
        raise GateError('invalid_capture_wait')
    storage = path_from(config['_root'], c['storage_dir'], write=True)
    settings = path_from(config['_root'], c['settings_path'], write=True)
    root = config['_root']
    if storage == root or storage in (root / x for x in ('groupbuy', 'docs', 'tests', 'examples')):
        raise GateError('unsafe_capture_directory')
    for path in (storage, settings):
        if root in path.parents and path.relative_to(root).parts[0] != 'runtime':
            raise GateError('unsafe_capture_directory')
    if settings.suffix != '.json' or not settings.name.endswith('.local.json'):
        raise GateError('invalid_capture_settings_path')
    c['_storage'], c['_settings'] = storage, settings
    config['_capture'] = c
    paths = []
    for task in config['tasks']:
        if task['input']['kind'] == 'reqable':
            path = path_from(root, task['input']['path'], write=True)
            if storage not in path.parents:
                raise GateError('capture_path_outside_storage')
            paths.append(path)
    if len(set(paths)) != len(paths):
        raise GateError('duplicate_capture_path')


def receiver_settings(config):
    c = config['_capture']
    if not c['enabled']:
        raise GateError('capture_not_enabled')
    path = c['_settings']
    with FileLock(path.with_suffix('.lock')):
        if path.exists():
            value = load_json(path)
        else:
            value = {'version': 1, 'route': '/report/' + secrets.token_hex(16)}
            atomic_json(path, value)
    if value.get('version') != 1 or not re.fullmatch(r'/report/[a-f0-9]{32}', value.get('route', '')):
        raise GateError('invalid_receiver_settings')
    return value


def capture_info(config):
    value = receiver_settings(config)
    return {'status': 'READY', 'report_url': f"http://127.0.0.1:{config['_capture']['port']}{value['route']}",
            'rules': ['https://*.dianping.com/*', 'https://*.meituan.com/*'],
            'compression': 'gzip or none', 'listener_started': False}


def capture_source(config, task, record):
    info = record.get('auto_har')
    if not info or info.get('task_hash') != task_hash(task):
        raise GateError('capture_input_not_ready')
    base = path_from(config['_root'], task['input']['path'], write=True)
    relative = Path(info.get('relative_path', ''))
    path = (base / relative).resolve()
    if relative.is_absolute() or '..' in relative.parts or base not in path.parents:
        raise GateError('invalid_capture_reference')
    proof = config['_output'] / record.get('ui_evidence', '')
    if (not path.is_file() or file_hash(path) != info.get('sha256') or not proof.is_file()
            or file_hash(proof) != info.get('evidence_sha256')):
        raise GateError('capture_integrity_failure')
    return path


class CaptureReceiver:
    def __init__(self, config, *, cancel_event=None):
        self.config, self.cancel_event = config, cancel_event
        self.route = receiver_settings(config)['route']
        self.lock = threading.RLock()
        self.active = None
        self.server = None

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # HTTP paths, headers and bodies are never logged.

            def do_POST(self):
                self.connection.settimeout(5)
                if self.headers.get('Origin') or not hmac.compare_digest(self.path.encode(), owner.route.encode()):
                    self.reply(403, 'report_not_authorized')
                    return
                try:
                    if self.headers.get('Transfer-Encoding'):
                        raise GateError('unsupported_report_transfer')
                    n = int(self.headers.get('Content-Length', '0'))
                    if not 0 < n <= MAX_REPORT_BYTES:
                        raise GateError('report_too_large')
                    data = self.rfile.read(n)
                    if len(data) != n:
                        raise GateError('incomplete_report')
                    encoding = self.headers.get('Content-Encoding', 'identity').lower()
                    if encoding == 'gzip':
                        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                            data = f.read(MAX_REPORT_BYTES + 1)
                    elif encoding not in ('identity', ''):
                        raise GateError('unsupported_report_encoding')
                    if len(data) > MAX_REPORT_BYTES:
                        raise GateError('report_too_large')
                    value = json.loads(data)
                    owner.accept(value)
                    self.reply(200, 'accepted')
                except Exception as error:
                    code = error.code if isinstance(error, GateError) else 'invalid_report'
                    self.reply(400, code)

            def reply(self, status, code):
                body = json.dumps({'code': code}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        try:
            class QuietServer(ThreadingHTTPServer):
                def handle_error(self, request, client_address):
                    pass
            self.server = QuietServer(('127.0.0.1', self.config['_capture']['port']), Handler)
        except OSError:
            raise GateError('capture_port_in_use') from None
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()

    def arm(self, task, prior=None):
        base = path_from(self.config['_root'], task['input']['path'], write=True)
        if prior:
            if prior.get('task_hash') != task_hash(task) or not re.fullmatch(r'[a-f0-9]{32}', prior.get('id', '')):
                raise GateError('capture_session_mismatch')
            meta = load_json(base / prior['id'] / 'session.json')
            if meta != prior:
                raise GateError('capture_session_mismatch')
        else:
            meta = {'id': uuid.uuid4().hex, 'task_hash': task_hash(task), 'started_at': utcnow()}
            atomic_json(base / meta['id'] / 'session.json', meta)
        with self.lock:
            folder = base / meta['id']
            journal = sorted((folder / 'entries').glob('*.json'))
            self.active = {'task': task, 'meta': meta, 'folder': folder,
                           'entries': [load_json(p) for p in journal], 'last': time.monotonic(),
                           'error': load_json(folder / 'error.json')['code'] if (folder / 'error.json').exists() else None}
        return meta

    def accept(self, document):
        if not isinstance(document, dict):
            raise GateError('invalid_report')
        raw = document.get('log', {}).get('entries') if isinstance(document.get('log'), dict) else None
        if raw is None and 'request' in document and 'response' in document:
            raw = [document]
        if not isinstance(raw, list) or not raw or len(raw) > 1000:
            raise GateError('invalid_report')
        with self.lock:
            if self.active is None:
                return 0
            current = self.active
            count = 0
            try:
                for entry in raw:
                    if not isinstance(entry, dict):
                        raise GateError('invalid_report')
                    # Unrelated traffic is rejected by the adapter before anything is persisted.
                    safe = get_adapter(current['task']['platform']).capture_entry(current['task'], entry)
                    if safe is None or moment(safe['startedDateTime']) < moment(current['meta']['started_at']):
                        continue
                    if moment(safe['startedDateTime']) > moment(utcnow()):
                        raise GateError('capture_clock_mismatch')
                    if len(current['entries']) >= 5000:
                        raise GateError('capture_entry_limit')
                    number = len(current['entries'])
                    atomic_json(current['folder'] / 'entries' / f'{number:06}.json', safe)
                    current['entries'].append(safe)
                    current['last'] = time.monotonic()
                    count += 1
            except Exception as error:
                current['error'] = error.code if isinstance(error, GateError) else 'invalid_report'
                atomic_json(current['folder'] / 'error.json', {'code': current['error']})
                raise
            return count

    def seal(self, task, record):
        adapter = get_adapter(task['platform'])
        proof_path = self.config['_output'] / record['ui_evidence']
        proof = load_json(proof_path)
        adapter.validate_evidence(task, proof)
        # Restore a stopped receiver from the task's persisted, projected journal.
        with self.lock:
            current = self.active
        if current is None or current['meta']['id'] != record['capture_session']['id']:
            self.arm(task, record['capture_session'])
        deadline = time.monotonic() + self.config['_capture']['timeout_seconds']
        while True:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise KeyboardInterrupt
            with self.lock:
                current = self.active
                if current['error']:
                    raise GateError(current['error'])
                entries = [e for e in current['entries'] if moment(proof['capture_start']) <=
                           moment(e['startedDateTime']) <= moment(proof['capture_end'])]
                settled = time.monotonic() - current['last'] >= self.config['_capture']['settle_seconds']
                normalized = [{'captured_at': e['startedDateTime'], 'request': params(e['request']),
                               'response': json.loads(e['response']['content']['text'])} for e in entries]
                try:
                    adapter.select_pages(task, normalized)
                    complete = True
                except GateError as error:
                    if error.code not in {'no_matching_responses', 'pagination_gap'}:
                        raise
                    complete = False
                if complete and settled:
                    target = current['folder'] / 'input.har'
                    snapshot = {'log': {'version': '1.2', 'creator': {'name': 'groupbuy-local',
                                'version': '0.4.0'}, 'entries': entries}, '_groupbuy_projected': True}
                    if target.exists():
                        if load_json(target) != snapshot:
                            raise GateError('capture_integrity_failure')
                    else:
                        atomic_json(target, snapshot)
                    base = path_from(self.config['_root'], task['input']['path'], write=True)
                    binding = {'relative_path': target.relative_to(base).as_posix(), 'sha256': file_hash(target),
                               'evidence_sha256': file_hash(proof_path), 'task_hash': task_hash(task)}
                    if (current['folder'] / 'binding.json').exists() and load_json(current['folder'] / 'binding.json') != binding:
                        raise GateError('capture_integrity_failure')
                    atomic_json(current['folder'] / 'binding.json', binding)
                    self.active = None
                    return binding
            if time.monotonic() >= deadline:
                raise GateError('capture_incomplete_or_not_reporting')
            time.sleep(.1)
