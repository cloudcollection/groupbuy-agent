"""Dianping UI policy. All clicks come from current observed text boxes."""
import random
import time
from ..common import GateError, digest, utcnow
from ..config import task_hash
from ..privacy import clean

BOTTOM = ('已经到底', '没有更多', '到底啦', '到底了', '暂无结果')
RISK = ('验证码', '验证身份', '访问受限', '操作频繁', '请登录', '登录后', '安全验证')


def check_frame(frame, expected=None):
    if not frame.get('foreground'):
        raise GateError('foreground_changed')
    if any(word in frame['text'] for word in RISK):
        raise GateError('verification_or_login')
    if expected and (frame['window_id'] != expected['window_id'] or frame['window_size'] != expected['window_size']):
        raise GateError('window_changed')


def confirm(task, frame):
    text = ''.join(frame['text'].split())
    labels = [task['search_term'], *task['target_location'].values(), *task['filters'].values()]
    return all(isinstance(v, str) and ''.join(v.split()) in text for v in labels if v)


def can_resume(task, prior, fresh):
    if (prior.get('task_hash') != task_hash(task) or prior.get('task_id') != task['task_id']
            or prior.get('stop_reason') != 'scroll_budget' or prior.get('risk')
            or not prior.get('filter_frame_hash') or not prior.get('search_confirmed')
            or not prior.get('filters_confirmed') or not prior.get('target_confirmed')
            or prior.get('selected_filters') != task['filters']
            or prior.get('target_location') != task['target_location']):
        raise GateError('unsafe_scroll_resume')
    if (fresh.get('list_observed') is False or prior['window_id'] != fresh['window_id'] or prior['window_size'] != fresh['window_size']
            or prior['list_hash'] != fresh['list_hash']):
        raise GateError('resume_window_or_list_mismatch')
    check_frame(fresh)


def collect(task, runtime, driver, prior=None):
    profile = task.get('ui_profile')
    if not profile:
        raise GateError('ui_profile_required')
    wait = lambda: driver.wait(random.uniform(*runtime['wait_seconds']))
    first = driver.observe(profile)
    check_frame(first)
    if prior:
        can_resume(task, prior, first)
        evidence = dict(prior)
    else:
        evidence = {'schema': 'groupbuy.ui-evidence.v1', 'task_id': task['task_id'],
                    'task_hash': task_hash(task), 'search_term': task['search_term'],
                    'target_location': task['target_location'], 'selected_filters': task['filters'],
                    'capture_start': utcnow(), 'risk': False, 'search_confirmed': False,
                    'target_confirmed': False, 'filters_confirmed': False}
        # The ordered recipe is local configuration, not shared pixel coordinates.
        steps = profile.get('steps', [])
        if not steps or not any(s.get('action') == 'type_search' for s in steps):
            raise GateError('missing_search_recipe')
        for step in steps:
            frame = driver.observe(profile)
            check_frame(frame, first)
            if step['action'] == 'click':
                driver.click_observed(step['text'], frame)
            elif step['action'] == 'type_search':
                driver.click_observed(step['text'], frame)
                driver.type_search(task['search_term'])
            else:
                raise GateError('unsupported_ui_action')
            wait()
        frame = driver.observe(profile)
        check_frame(frame, first)
        if frame.get('list_observed') is False or not confirm(task, frame):
            raise GateError('target_or_filter_not_visible')
        evidence.update(search_confirmed=True, target_confirmed=True, filters_confirmed=True,
                        filter_frame_hash=frame['frame_hash'], window_id=frame['window_id'],
                        window_size=frame['window_size'])
    frame = driver.observe(profile)
    check_frame(frame, first)
    count = 0
    while True:
        check_frame(frame, first)
        bottom = next((x for x in BOTTOM if x in frame['list_text'].splitlines()), None)
        if bottom:
            wait()
            stable = driver.observe(profile)
            check_frame(stable, first)
            if bottom in stable['list_text'].splitlines() and '加载中' not in stable['list_text']:
                frame = stable
                reason = 'explicit_bottom'
                break
        if count >= runtime['max_scrolls']:
            reason = 'scroll_budget'
            break
        if '加载中' in frame['list_text']:
            for _ in range(runtime['retry_limit'] + 1):
                wait()
                frame = driver.observe(profile)
                check_frame(frame, first)
                if '加载中' not in frame['list_text']:
                    break
            else:
                raise GateError('loading_timeout')
        driver.scroll_observed(frame, runtime['wheel_steps'])
        count += 1
        wait()
        frame = driver.observe(profile)
    evidence.update(stop_reason=reason, bottom_reached=reason == 'explicit_bottom',
                    bottom_text=bottom if reason == 'explicit_bottom' else None,
                    capture_end=utcnow(), list_hash=frame['list_hash'],
                    final_frame_hash=frame['frame_hash'], scroll_count=evidence.get('scroll_count', 0) + count,
                    evidence_mode=runtime['evidence_mode'])
    if runtime['evidence_mode'] == 'structured':
        evidence['observation_summary'] = {'search_visible': True, 'target_visible': True,
                                           'filters_visible': True, 'terminal_in_list_region': reason == 'explicit_bottom'}
    # Only purpose-specific observations are saved; OCR text and screenshots stay in memory.
    return clean(evidence)
