"""Fictional end-to-end checks using real loopback HTTP/SSE and pipeline code.

No desktop driver, provider key, external endpoint or active workspace is used.
"""
import gzip
import json
import socket
import threading
import urllib.error
import urllib.request
import uuid
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import run_agent
from .agent_runtime import AgentSettings, post_json
from .capture import capture_info
from .common import GateError, atomic_json, digest, path_from, utcnow
from .config import load_config
from .exporter import verify_export
from .model_providers import codex_responses_body
from .runner import Runner


FAILURES = {
    'no_reports': 'capture_incomplete_or_not_reporting',
    'wrong_report_url': 'capture_incomplete_or_not_reporting',
    'missing_page': 'capture_incomplete_or_not_reporting',
    'verification': 'verification_or_login',
    'network_error': 'page_network_error',
    'loading_timeout': 'loading_timeout',
    'foreground_lost': 'foreground_changed',
    'wrong_target': 'target_or_filter_not_visible',
    'scroll_budget': 'ui_segment_limit',
    'partial_model_response': 'model_response_incomplete',
    'model_no_progress': 'agent_no_progress',
}


def require(condition):
    if not condition:
        raise GateError('simulation_expectation_failed')


class ModelServer:
    """A deterministic planner speaks Responses SSE; it is not an AI model."""
    def __init__(self, mode):
        self.mode, self.requests = mode, 0

    def reply(self, body):
        self.requests += 1
        require(body.get('stream') is True and body.get('store') is False)
        require(body.get('parallel_tool_calls') is False)
        if self.mode == 'model_no_progress':
            name, args = 'get_status', {}
        elif self.requests == 1:
            name, args = 'check_environment', {}
        elif self.requests == 2:
            name, args = 'get_status', {}
        else:
            outputs = [i for i in body['input'] if i.get('type') == 'function_call_output']
            snapshot = json.loads(outputs[-1]['output']).get('snapshot', {})
            current = next((t for t in snapshot.get('tasks', []) if t['stage'] != 'COMPLETE'), None)
            if current is None:
                return {'type': 'message', 'role': 'assistant', 'content': [
                    {'type': 'output_text', 'text': 'Fictional batch finished.'}]}
            args = {'task_ref': current['task_ref']}
            if current['stage'] in {'COLLECT_UI', 'RESUME_UI'}:
                name = 'collect_ui'
                args['resume'] = current['stage'] == 'RESUME_UI'
            else:
                name = {'EXPORT_HAR': 'export_har', 'PROCESS_LOCAL': 'process_task'}.get(current['stage'])
                require(name is not None)
        return {'type': 'function_call', 'name': name, 'arguments': json.dumps(args),
                'call_id': f'fictional-call-{self.requests}'}

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.connection.settimeout(5)
                if self.path != '/v1/responses':
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    require(0 < length <= 8 * 1024 * 1024)
                    body = json.loads(self.rfile.read(length))
                    item = owner.reply(body)
                    events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': item}]
                    if owner.mode != 'partial_model_response':
                        events.append({'type': 'response.completed', 'response': {
                            'status': 'completed', 'output': [item]}})
                    data = ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    self.send_error(400)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class LoopbackTransport:
    backend = 'SIMULATED_RESPONSES_SSE'

    def __init__(self, server):
        self.server = server

    def create(self, payload, *, timeout_seconds):
        # Explicitly no environment keys or proxies, even when the user configured them.
        return post_json(self.server.url + '/responses', codex_responses_body(payload, 'medium'),
                         {'Accept': 'text/event-stream'}, timeout_seconds, stream=True)


class SimulatedInterface:
    def __init__(self, config, mode):
        self.c, self.mode = config, mode
        self.url = capture_info(config)['report_url']
        self.task, self.scrolls, self.total_scrolls, self.posts = None, 0, 0, []
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def observe(self, profile):
        selected = next(t for t in self.c['tasks'] if t['ui_profile'] == profile)
        if self.task is None or selected['task_id'] != self.task['task_id']:
            self.task, self.scrolls = selected, 0
        text = ' '.join([self.task['search_term'], *self.task['target_location'].values(),
                         *self.task['filters'].values()])
        if self.mode == 'wrong_target':
            text = 'Unrelated fictional target'
        if self.mode == 'verification':
            text += ' 验证码'
        if self.mode == 'network_error':
            text += ' 网络错误'
        bottom = self.scrolls >= 2 and self.mode != 'scroll_budget'
        return {'window_id': 'synthetic-window', 'window_size': [640, 960],
                'foreground': self.mode != 'foreground_lost', 'list_observed': True,
                'frame_hash': digest(['fictional-frame', text, self.scrolls]),
                'list_hash': digest(['fictional-list', self.task['task_id'], self.scrolls]), 'text': text,
                'list_text': ('加载中' if self.mode == 'loading_timeout' else
                              '没有更多' if bottom else '虚构餐厅列表')}

    def click_observed(self, label, frame):
        require(frame['foreground'] and isinstance(label, str))

    def type_search(self, text):
        require(text == self.task['search_term'])
        self.first = self.payload(0, 1, False, 1)
        if self.mode != 'no_reports':
            self.send(self.first)

    def scroll_observed(self, frame, steps):
        self.scrolls += 1
        self.total_scrolls += 1
        if self.mode in {'no_reports', 'wrong_target'}:
            return
        if self.scrolls == 1:
            self.send(deepcopy(self.first))
        elif self.scrolls == 2 and self.mode not in {'missing_page', 'scroll_budget'}:
            self.send(self.payload(1, 0, True, 2))

    def wait(self, seconds):
        require(1 <= seconds <= 2)  # No actual waits or desktop activity in a simulation.

    def payload(self, start, nxt, end, page):
        number = int(self.task['task_id'].split('-')[-1]) * 10 + page
        shop = {'shopUuid': f'fictional-shop-{number}', 'shopName': f'虚构餐厅{number}',
                'categoryName': '川菜', 'score': '4.8', 'avgPrice': '¥80/人', 'commentCount': '100+',
                'address': f'虚构路{number}号', 'shopDealInfos': [{
                    'dealId': f'fictional-product-{number}', 'dealTitle': '虚构双人套餐', 'dealType': 1,
                    'salePrice': '¥79.90', 'originalPrice': '¥120', 'salesText': '已售1000+',
                    'useRules': ['周一至周五可用', '需提前预约']}]}
        request = {'keyword': self.task['search_term'], 'start': start, 'category': '美食',
                   'rangetext': '3km', 'cityname': '示例市', 'token': 'fictional-private-marker'}
        response = {'code': 200, 'data': {'list': [{'shopInfo': shop}], 'nextStartIndex': nxt, 'isEnd': end}}
        return {'startedDateTime': utcnow(), 'request': {'method': 'POST',
                'url': 'https://example.dianping.com/search',
                'headers': [{'name': 'Authorization', 'value': 'fictional-private-marker'}],
                'postData': {'text': json.dumps(request, ensure_ascii=False)}},
                'response': {'status': 200, 'headers': [], 'content': {'text': json.dumps(response, ensure_ascii=False)}}}

    def send(self, entry):
        data = gzip.compress(json.dumps({'log': {'entries': [entry]}}, ensure_ascii=False).encode())
        url = self.url if self.mode != 'wrong_report_url' else self.url + '/wrong'
        request = urllib.request.Request(url, data=data, headers={
            'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        try:
            with self.opener.open(request, timeout=5) as response:
                self.posts.append(response.status)
        except urllib.error.HTTPError as error:
            with error:
                self.posts.append(error.code)
        require(self.posts[-1] == (403 if self.mode == 'wrong_report_url' else 200))


def make_case(base, name, count):
    root = base / name
    root.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    tasks = []
    for number in range(1, count + 1):
        ident, term = f'SIM-{number:03}', f'虚构广场{number:02}'
        tasks.append({'task_id': ident, 'platform': 'dianping', 'search_term': term,
                      'target_location': {'city': '示例市', 'center_name': term},
                      'filters': {'category': '美食', 'range_text': '3km'},
                      'fields': {'shops': '*', 'products': '*'}, 'output_formats': ['json', 'csv'],
                      'status': 'PENDING', 'input': {'kind': 'reqable', 'path': 'runtime/capture/' + ident,
                                                   'evidence': 'runtime/evidence/' + ident + '.json'},
                      'ui_profile': {'window_title_regex': ident, 'calibration_required': False,
                                     'list_anchor': '智能排序', 'steps': [
                                         {'action': 'type_search', 'text': '搜索商家'},
                                         {'action': 'click', 'text': '美食'}, {'action': 'click', 'text': '3km'}]}})
    raw = {'version': 1, 'mode': 'live', 'synthetic': True, 'tasks': tasks,
           'runtime': {'batch_size': 5, 'max_scrolls': 2, 'wheel_steps': 10, 'wait_seconds': [1, 2],
                       'retry_limit': 1, 'evidence_mode': 'structured',
                       'output_dir': 'runtime/outputs', 'progress_path': 'runtime/progress.json'},
           'capture': {'enabled': True, 'port': port, 'settle_seconds': 0, 'timeout_seconds': 0},
           'schedule': {'enabled': False, 'timezone': 'Asia/Shanghai',
                        'times': ['09:00', '12:00', '15:00', '19:00'], 'live_confirmed': False}}
    atomic_json(root / 'config.local.json', raw)
    return load_config(root / 'config.local.json', root)


def execute(config, mode, cancel_event=None):
    driver = SimulatedInterface(config, mode)
    with ModelServer(mode) as server:
        settings = AgentSettings(provider='responses_compatible', model='fictional-simulation-model',
                                 base_url=server.url, reasoning_effort='medium', max_ui_segments=2)
        result = run_agent(config, settings, allow_ui=True, transport=LoopbackTransport(server),
                           driver_factory=lambda: driver, cancel_event=cancel_event)
        require(result['rounds'] == server.requests)
    return result, driver


def checked_results(config):
    runner = Runner(config)
    state = runner.load_state()
    rows = []
    for task in config['tasks']:
        record = state['tasks'][task['task_id']]
        if record['status'] != 'COMPLETE':
            continue
        folder = runner._artifact_path(record)
        value, _ = verify_export(folder)
        require(value['quality']['pagination_complete'] and value['quality']['data_safety_passed'])
        require(all(r['task_id'] == task['task_id'] for r in value['shops'] + value['products']))
        require(all(p['shop_id'] in {s['shop_id'] for s in value['shops']} for p in value['products']))
        rows.append({'task_id': task['task_id'], 'shops': len(value['shops']), 'products': len(value['products']),
                     'duplicate_pages': value['quality']['duplicate_pages'],
                     'directory': folder.relative_to(config['_root']).as_posix()})
    for file in config['_root'].rglob('input.har'):
        require('fictional-private-marker' not in file.read_text(encoding='utf-8'))
    return state, rows


def run_simulation(project_root=None, *, cancel_event=None, on_event=None):
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    base = path_from(root, 'runtime/simulations/' + uuid.uuid4().hex, write=True)
    base.mkdir(parents=True)
    cases = []
    interrupted = False
    for mode in ['batch_and_resume', *FAILURES]:
        if cancel_event is not None and cancel_event.is_set():
            interrupted = True
            break
        if on_event:
            on_event({'event': 'simulation_case', 'status': mode})
        try:
            c = make_case(base, mode, 6 if mode == 'batch_and_resume' else 1)
            result, driver = execute(c, 'normal' if mode == 'batch_and_resume' else mode, cancel_event)
            if result['status'] == 'INTERRUPTED':
                interrupted = True
                break
            state, rows = checked_results(c)
            item = {'scenario': mode, 'passed': True}
            if mode == 'batch_and_resume':
                require(result['status'] == 'COMPLETE' and len(rows) == 5)
                require(state['tasks']['SIM-006']['status'] == 'PENDING')
                resumed, other = execute(c, 'normal', cancel_event)
                if resumed['status'] == 'INTERRUPTED':
                    interrupted = True
                    break
                _, rows = checked_results(c)
                require(resumed['status'] == 'COMPLETE' and len(rows) == 6)
                require(sum(r['duplicate_pages'] for r in rows) == 6)
                require(sum(r['shops'] for r in rows) == sum(r['products'] for r in rows) == 12)
                item.update(completed_tasks=6, shops=12, products=12,
                            http_posts=len(driver.posts) + len(other.posts),
                            model_http_requests=result['rounds'] + resumed['rounds'],
                            tool_calls=result['tool_calls'] + resumed['tool_calls'], artifacts=rows)
            else:
                code = state['tasks']['SIM-001'].get('error')
                require(not rows and FAILURES[mode] in {code, result.get('code')})
                require(state['tasks']['SIM-001']['status'] != 'COMPLETE')
                item.update(expected_code=FAILURES[mode], task_error=code, summary_code=result.get('code'),
                            false_completion=False, http_posts=len(driver.posts),
                            model_http_requests=result['rounds'], tool_calls=result['tool_calls'])
                if mode == 'partial_model_response':
                    require(result['tool_calls'] == 0 and not driver.posts)
                if mode == 'wrong_report_url':
                    require(driver.posts and all(status == 403 for status in driver.posts))
                if mode == 'missing_page':
                    Runner(c).retry('SIM-001')
                    fixed, _ = execute(c, 'normal', cancel_event)
                    if fixed['status'] == 'INTERRUPTED':
                        interrupted = True
                        break
                    require(fixed['status'] == 'COMPLETE' and len(checked_results(c)[1]) == 1)
                    item['explicit_retry_recovered'] = True
            cases.append(item)
        except Exception as error:
            cases.append({'scenario': mode, 'passed': False,
                          'code': error.code if isinstance(error, GateError) else 'simulation_operation_failed'})
    passed = len(cases) == 1 + len(FAILURES) and all(c['passed'] for c in cases)
    report = {'schema': 'groupbuy.simulation.v1', 'status': 'INTERRUPTED' if interrupted else 'PASS' if passed else 'FAIL',
              'backend': 'SIMULATED_RESPONSES_SSE', 'synthetic': True, 'live_collection_verified': False,
              'external_api_requests': 0, 'desktop_actions': 0, 'at': utcnow(),
              'cases_passed': sum(c['passed'] for c in cases), 'cases_total': 1 + len(FAILURES), 'scenarios': cases}
    atomic_json(base / 'report.json', report)
    lines = ['# 完整链路模拟结果', '', '所有业务数据、模型决策与界面观察均为虚构。',
             '模型侧使用真实本机 HTTP/SSE；报告侧使用真实本机 HTTP/Gzip；解析、门禁、导出与恢复使用实际代码。',
             '不调用外部模型，不操作桌面，不修改原工作区，不证明真实采集成功。', '',
             '| 场景 | 检查结果 |', '|---|---|']
    lines.extend('| ' + c['scenario'] + ' | ' + ('通过' if c['passed'] else '未通过') + ' |' for c in cases)
    lines.extend(['', '总体结果：' + report['status'], '',
                  '虚构 CSV / JSON 位于 batch_and_resume/runtime/outputs/；分批 5 项，再续跑 1 项。',
                  '首个完整结果：batch_and_resume/runtime/outputs/SIM-001/，详见 report.json 中的 artifacts。'])
    (base / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {**report, 'report_path': str(base / 'report.json'), 'summary_path': str(base / 'report.md')}
