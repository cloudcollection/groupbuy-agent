"""Loopback web UI sharing the CLI Agent, local config and task progress."""
import hmac
import json
import os
import secrets
import threading
import uuid
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .agent import run_agent
from .agent_runtime import AgentSettings, OpenAITransport
from .capture import capture_info
from .common import GateError, atomic_json, load_json
from .config import load_config
from .runner import Runner


class Dashboard:
    def __init__(self, config):
        self.root = config['_root']
        self.pointer = self.root / 'runtime/ui-state.local.json'
        self.config = config
        if self.pointer.exists():
            relative = load_json(self.pointer).get('config_path', '')
            path = (self.root / relative).resolve()
            if not relative or self.root / 'runtime' not in path.parents:
                raise GateError('invalid_ui_state')
            self.config = load_config(path, self.root)
        self.lock = threading.RLock()
        self.worker = None
        self.cancel = threading.Event()
        self.result = None

    def running(self):
        return bool(self.worker and self.worker.is_alive())

    def state(self):
        with self.lock:
            runner = Runner(self.config)
            state = runner.load_state()
            rows = []
            for task in self.config['tasks']:
                record = state['tasks'][task['task_id']]
                rows.append({'task_id': task['task_id'], 'search_term': task['search_term'],
                             'city': task['target_location']['city'], 'status': record['status'],
                             'code': record.get('error'), 'har_ready': bool(record.get('auto_har'))})
            return {'running': self.running(), 'tasks': rows, 'result': self.result,
                    'output_dir': str(runner.output), 'mode': self.config['mode'],
                    'config_path': str(self.root / load_json(self.pointer)['config_path']) if self.pointer.exists() else '',
                    'form_defaults': {'batch_size': self.config['runtime']['batch_size'],
                                      'filters': self.config['tasks'][0]['filters'],
                                      'ui_profile': self.config['tasks'][0].get('ui_profile', {})},
                    'capture_enabled': self.config.get('_capture', {}).get('enabled', False),
                    'configured_model': os.environ.get('OPENAI_MODEL', ''),
                    'api_key_available': bool(os.environ.get('OPENAI_API_KEY'))}

    def save_tasks(self, body):
        with self.lock:
            if self.running():
                raise GateError('agent_already_running')
            if set(body) - {'tasks', 'category', 'range_text', 'batch_size', 'ui_profile'}:
                raise GateError('invalid_ui_tasks')
            rows = body.get('tasks')
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
                raise GateError('invalid_ui_tasks')
            for key in ('category', 'range_text'):
                if key in body and (not isinstance(body[key], str) or not 1 <= len(body[key]) <= 200):
                    raise GateError('invalid_ui_tasks')
            base = 'runtime/ui-workspaces/' + uuid.uuid4().hex
            template = load_json(self.root / 'examples/config.auto-template.json')
            profile = body.get('ui_profile', {})
            if not isinstance(profile, dict) or set(profile) - {'window_title_regex', 'search_label', 'list_anchor', 'process_names'}:
                raise GateError('invalid_ui_tasks')
            for key in ('window_title_regex', 'search_label', 'list_anchor'):
                if key in profile and (not isinstance(profile[key], str) or not 1 <= len(profile[key]) <= 200):
                    raise GateError('invalid_ui_tasks')
            names = profile.get('process_names', ['WeChat.exe', 'Weixin.exe', 'WeChatAppEx.exe'])
            if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n.endswith('.exe') for n in names):
                raise GateError('invalid_ui_tasks')
            template['synthetic'] = False
            template['runtime'].update(output_dir=base + '/outputs', progress_path=base + '/progress.json',
                                       batch_size=body.get('batch_size', 5))
            template['capture']['storage_dir'] = base + '/capture'
            original = template['tasks'][0]
            tasks = []
            for i, row in enumerate(rows, 1):
                if not isinstance(row, dict) or set(row) != {'city', 'search_term'}:
                    raise GateError('invalid_ui_tasks')
                if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in row.values()):
                    raise GateError('invalid_ui_tasks')
                t = deepcopy(original)
                ident = f'TASK-{i:03}'
                t.update(task_id=ident, search_term=row['search_term'].strip(),
                         target_location={'city': row['city'].strip(), 'center_name': row['search_term'].strip()},
                         filters={'category': body.get('category', '美食'), 'range_text': body.get('range_text', '3km')})
                t['input'].update(path=base + '/capture/' + ident, evidence=base + '/manual-evidence/' + ident + '.json')
                t['ui_profile']['steps'][1]['text'] = t['filters']['category']
                t['ui_profile']['steps'][2]['text'] = t['filters']['range_text']
                t['ui_profile'].update(window_title_regex=profile.get('window_title_regex', '大众点评'),
                                       list_anchor=profile.get('list_anchor', '智能排序'), process_names=names)
                t['ui_profile']['steps'][0]['text'] = profile.get('search_label', '搜索商家')
                tasks.append(t)
            template['tasks'] = tasks
            path = self.root / base / 'config.local.json'
            # Validate the new owned workspace before updating the active pointer.
            atomic_json(path, template)
            loaded = load_config(path, self.root)
            atomic_json(self.pointer, {'config_path': path.relative_to(self.root).as_posix()})
            self.config, self.result = loaded, None
            return {'status': 'SAVED', 'task_count': len(tasks), 'config_path': str(path)}

    def start(self, body):
        with self.lock:
            if self.running():
                raise GateError('agent_already_running')
            if set(body) - {'demo', 'allow_ui', 'model', 'api_key'} or any(
                    type(body.get(k, False)) is not bool for k in ('demo', 'allow_ui')):
                raise GateError('invalid_ui_run')
            demo = body.get('demo', False)
            c = load_config(self.root / 'examples/config.synthetic.json', self.root) if demo else self.config
            model = body.get('model') or os.environ.get('OPENAI_MODEL') or None
            settings = AgentSettings(model=model)
            key = body.get('api_key') or os.environ.get('OPENAI_API_KEY', '')
            transport = None if demo else OpenAITransport(key)
            if not demo and not settings.model:
                raise GateError('agent_model_required')
            self.cancel = threading.Event()
            self.result = {'status': 'STARTING', 'backend': 'SCRIPTED_DEMO' if demo else 'OPENAI_RESPONSES'}

            def job():
                try:
                    result = run_agent(c, settings, allow_ui=body.get('allow_ui', False), demo=demo,
                                       transport=transport, cancel_event=self.cancel)
                except BaseException as error:
                    result = {'status': 'STOPPED', 'code': error.code if isinstance(error, GateError) else 'operation_failed'}
                with self.lock:
                    self.result = result
            self.worker = threading.Thread(target=job, daemon=True)
            self.worker.start()
            return {'status': 'STARTED'}

    def stop(self):
        self.cancel.set()
        return {'status': 'STOP_REQUESTED'}

    def retry(self, body):
        with self.lock:
            if self.running():
                raise GateError('agent_already_running')
            if set(body) != {'task_id'} or not isinstance(body['task_id'], str):
                raise GateError('invalid_ui_retry')
            return Runner(self.config).retry(body['task_id'])


def dashboard_server(config, port=8787):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise GateError('invalid_dashboard_port')
    controller = Dashboard(config)
    csrf = secrets.token_hex(32)
    host = f'127.0.0.1:{port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, value, content_type='application/json; charset=utf-8'):
            data = value.encode('utf-8') if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized_host(self):
            return self.headers.get('Host') == host and self.headers.get('Origin', 'http://' + host) == 'http://' + host

        def do_GET(self):
            if not self.authorized_host():
                self.send(403, {'code': 'ui_not_authorized'})
                return
            try:
                if self.path == '/':
                    self.send(200, (Path(__file__).parent / 'web/index.html').read_text(encoding='utf-8'), 'text/html; charset=utf-8')
                elif self.path == '/favicon.ico':
                    self.send(204, '', 'image/x-icon')
                elif self.path in ('/app.js', '/style.css', '/favicon.svg'):
                    name = self.path[1:]
                    self.send(200, (Path(__file__).parent / 'web' / name).read_text(encoding='utf-8'),
                              {'app.js': 'text/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8',
                               'favicon.svg': 'image/svg+xml'}[name])
                elif self.path == '/api/bootstrap':
                    self.send(200, {'csrf': csrf})
                elif self.path == '/api/state':
                    self.send(200, controller.state())
                elif self.path == '/api/capture':
                    self.send(200, capture_info(controller.config))
                else:
                    self.send(404, {'code': 'not_found'})
            except Exception as error:
                self.send(400, {'code': error.code if isinstance(error, GateError) else 'operation_failed'})

        def do_POST(self):
            self.connection.settimeout(5)
            if (not self.authorized_host() or self.headers.get('Content-Type') != 'application/json'
                    or not hmac.compare_digest(self.headers.get('X-Groupbuy-CSRF', '').encode(), csrf.encode())):
                self.send(403, {'code': 'ui_not_authorized'})
                return
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < n <= 65536:
                    raise GateError('invalid_ui_request')
                raw = self.rfile.read(n)
                if len(raw) != n:
                    raise GateError('invalid_ui_request')
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise GateError('invalid_ui_request')
                handlers = {'/api/config': controller.save_tasks, '/api/run': controller.start,
                            '/api/retry': controller.retry, '/api/stop': lambda _: controller.stop()}
                if self.path not in handlers:
                    raise GateError('unknown_ui_action')
                self.send(200, handlers[self.path](body))
            except Exception as error:
                self.send(400, {'code': error.code if isinstance(error, GateError) else 'operation_failed'})

    try:
        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass
        server = QuietServer(('127.0.0.1', port), Handler)
    except OSError:
        raise GateError('dashboard_port_in_use') from None
    server.daemon_threads = True
    server.controller = controller
    return server


def serve_dashboard(config, port=8787):
    server = dashboard_server(config, port)
    print(json.dumps({'status': 'UI_READY', 'url': f'http://127.0.0.1:{port}', 'local_only': True}))
    try:
        server.serve_forever()
    finally:
        server.controller.stop()
        if server.controller.worker:
            server.controller.worker.join(timeout=125)
        server.server_close()
