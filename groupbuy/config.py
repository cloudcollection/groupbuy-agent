"""Strict task configuration with project-relative paths."""
import re
from dataclasses import fields
from .common import GateError, digest, load_json, path_from
from .models import PublicProduct, Shop

STATES = {'PENDING', 'RUNNING', 'COMPLETE', 'REVIEW', 'STOPPED', 'PAUSED'}
SHOP_FIELDS = {f.name for f in fields(Shop)}
PRODUCT_FIELDS = {f.name for f in fields(PublicProduct)}
REQUIRED_FIELDS = {'task_id', 'platform', 'shop_id', 'captured_at', 'provenance_id'}


def task_hash(task):
    return digest({k: v for k, v in task.items() if k != 'status'})


def validate_task(t):
    required = {'task_id', 'platform', 'search_term', 'target_location', 'filters',
                'fields', 'output_formats', 'status', 'input'}
    if not required <= t.keys() or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', t['task_id']):
        raise GateError('invalid_task')
    if t['platform'] != 'dianping':
        raise GateError('unsupported_platform')
    if not isinstance(t['search_term'], str) or not t['search_term'].strip():
        raise GateError('invalid_search_term')
    if not isinstance(t['target_location'], dict) or not t['target_location'].get('city'):
        raise GateError('invalid_target_location')
    if set(t['target_location']) - {'city', 'business_area', 'merchant_name', 'address', 'center_name'} or any(
        not isinstance(v, str) for v in t['target_location'].values()):
        raise GateError('unsupported_target_location')
    if not isinstance(t['filters'], dict) or set(t['filters']) - {'category', 'range_text'}:
        raise GateError('unsupported_filter')
    if t['status'] not in STATES or not set(t['output_formats']) <= {'json', 'csv'} or not t['output_formats']:
        raise GateError('invalid_task_options')
    if set(t['fields']) != {'shops', 'products'}:
        raise GateError('invalid_fields')
    for key, allowed in [('shops', SHOP_FIELDS), ('products', PRODUCT_FIELDS)]:
        f = t['fields'][key]
        if f != '*' and (not isinstance(f, list) or not REQUIRED_FIELDS <= set(f) or not set(f) <= allowed):
            raise GateError('invalid_fields')
        if f != '*':
            needs = {'sale_price': {'price_unit'}, 'original_price': {'price_unit'},
                     'average_price': {'average_price_unit'},
                     'distance_m': {'distance_text', 'distance_center', 'distance_source'},
                     'classification': {'classification_basis'}}
            if any(k in f and not deps <= set(f) for k, deps in needs.items()):
                raise GateError('missing_field_semantics')
    if t['input'].get('kind') not in {'har', 'json', 'reqable'}:
        raise GateError('unsupported_input')
    for k in ('path', 'evidence'):
        if not isinstance(t['input'].get(k), str):
            raise GateError('missing_input_path')


def load_config(path, project_root=None):
    from pathlib import Path
    c = load_json(path)
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    if c.get('version') != 1 or c.get('mode') not in {'offline', 'live'}:
        raise GateError('invalid_config_version')
    ids = []
    for task in c.get('tasks', []):
        validate_task(task)
        ids.append(task['task_id'])
    if not ids or len(ids) != len(set(ids)):
        raise GateError('duplicate_or_empty_tasks')
    r = c['runtime']
    for name in ('batch_size', 'max_scrolls', 'wheel_steps', 'retry_limit'):
        if not isinstance(r.get(name), int) or isinstance(r[name], bool) or r[name] < (0 if name == 'retry_limit' else 1):
            raise GateError('invalid_runtime')
    if r['retry_limit'] > 10 or r['batch_size'] > 100 or r['wheel_steps'] > 30:
        raise GateError('invalid_runtime')
    waits = r.get('wait_seconds', [])
    if len(waits) != 2 or not 0.1 <= waits[0] <= waits[1] <= 60:
        raise GateError('invalid_wait')
    if r.get('evidence_mode') not in {'structured', 'hashes'}:
        raise GateError('unsupported_evidence_mode')
    c['_root'] = root
    c['_output'] = path_from(root, r['output_dir'], write=True)
    c['_progress'] = path_from(root, r['progress_path'], write=True)
    from .capture import validate_capture_config
    validate_capture_config(c)
    if c['_output'] == root or root in c['_output'].parents and c['_output'].parts[-1] in {'groupbuy', 'tests', 'examples', 'docs'}:
        raise GateError('unsafe_output_directory')
    s = c['schedule']
    if s.get('timezone') != 'Asia/Shanghai' or not isinstance(s.get('enabled'), bool):
        raise GateError('unsupported_schedule')
    if not s.get('times') or len(set(s['times'])) != len(s['times']) or any(
        not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in s['times']):
        raise GateError('invalid_schedule_times')
    return c
