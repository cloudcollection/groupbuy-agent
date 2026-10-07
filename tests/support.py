"""All business data in this module is fictional."""
from copy import deepcopy
from groupbuy.common import digest
from groupbuy.config import task_hash


def task(number=1):
    return {'task_id': f'DEMO-{number:03}', 'platform': 'dianping', 'search_term': '示例广场',
            'target_location': {'city': '示例市', 'business_area': '虚构商圈', 'center_name': '示例广场'},
            'filters': {'category': '美食', 'range_text': '3km'},
            'fields': {'shops': '*', 'products': '*'}, 'output_formats': ['json', 'csv'],
            'status': 'PENDING', 'input': {'kind': 'json', 'path': f'examples/fixtures/DEMO-{number:03}.json',
                                          'evidence': f'examples/fixtures/DEMO-{number:03}.evidence.json'}}


def evidence(t):
    return {'schema': 'groupbuy.ui-evidence.v1', 'synthetic': True,
            'task_id': t['task_id'], 'task_hash': task_hash(t), 'search_term': t['search_term'],
            'target_location': deepcopy(t['target_location']), 'selected_filters': deepcopy(t['filters']),
            'search_confirmed': True, 'target_confirmed': True, 'filters_confirmed': True,
            'capture_start': '2030-01-01T09:00:00+08:00', 'capture_end': '2030-01-01T09:10:00+08:00',
            'filter_frame_hash': digest('fictional filter frame'), 'final_frame_hash': digest('fictional terminal frame'),
            'list_hash': digest('fictional list'), 'window_id': 'synthetic-window', 'window_size': [640, 960],
            'risk': False, 'stop_reason': 'explicit_bottom', 'bottom_reached': True, 'bottom_text': '没有更多'}


def shop(number=1):
    return {'shopUuid': f'fictional-shop-{number}', 'shopName': f'虚构餐厅{number}', 'branchName': '示例店',
            'categoryName': '川菜', 'score': '4.8', 'avgPrice': '¥80/人', 'commentCount': '100+',
            'address': f'虚构路{number}号', 'regionName': '虚构商圈', 'distanceText': '距搜索中心 1.2km',
            'distanceCenter': '示例广场', 'shopDealInfos': [
                {'dealId': f'fictional-product-{number}', 'dealTitle': '虚构双人套餐', 'dealType': 1,
                 'salePrice': '¥79.90', 'originalPrice': '¥120', 'salesText': '已售1000+',
                 'useRules': ['周一至周五可用', '需提前预约']}]}


def entry(start=0, nxt=1, end=False, number=1, minute=1, round_id='fictional-round-1'):
    return {'captured_at': f'2030-01-01T09:{minute:02}:00+08:00', 'round_id': round_id,
            'request': {'keyword': '示例广场', 'start': start, 'category': '美食', 'rangetext': '3km', 'cityname': '示例市'},
            'response': {'code': 200, 'data': {'list': [{'shopInfo': shop(number)}], 'nextStartIndex': nxt, 'isEnd': end}},
            'source_hash': digest('fictional-source'), 'entry_index': minute}


def entries():
    a = entry()
    duplicate = deepcopy(a)
    duplicate['captured_at'] = '2030-01-01T09:02:00+08:00'
    duplicate['entry_index'] = 2
    return [a, duplicate, entry(1, 0, True, 2, 3)]


def config(root):
    return {'version': 1, 'mode': 'offline', 'synthetic': True,
            'runtime': {'batch_size': 5, 'max_scrolls': 180, 'wheel_steps': 10,
                        'wait_seconds': [1, 2], 'retry_limit': 2, 'evidence_mode': 'structured',
                        'output_dir': 'runtime/outputs', 'progress_path': 'runtime/progress.json'},
            'schedule': {'enabled': False, 'timezone': 'Asia/Shanghai',
                         'times': ['09:00', '12:00', '15:00', '19:00'], 'live_confirmed': False},
            'tasks': [task(i) for i in range(1, 7)]}


class FakeDriver:
    def __init__(self, frames):
        self.frames = frames
        self.index = 0
        self.actions = []

    def observe(self, profile):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return deepcopy(frame)

    def click_observed(self, label, frame):
        self.actions.append(('click', label))

    def type_search(self, text):
        self.actions.append(('type_search', text))

    def scroll_observed(self, frame, steps):
        self.actions.append(('scroll', steps))

    def wait(self, seconds):
        self.actions.append(('wait', seconds))


def frame(bottom=False, risk=False, window='synthetic-window', list_hash=None):
    return {'window_id': window, 'window_size': [640, 960], 'foreground': True,
            'frame_hash': digest('fake-frame'), 'list_hash': list_hash or digest('fake-list'),
            'text': '示例广场 示例市 虚构商圈 美食 3km' + (' 验证码' if risk else ''),
            'list_text': '没有更多' if bottom else '虚构餐厅'}
