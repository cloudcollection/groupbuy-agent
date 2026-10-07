"""Compact JSON/CSV, immutable result directories and verified batch merges."""
import csv
import hashlib
import json
from pathlib import Path
from .common import GateError, atomic_json, digest, load_json, path_from
from .privacy import safe


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def csv_cell(v):
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + v  # Spreadsheet formula injection protection; JSON stays faithful.
    return v


def export_result(result, task, target):
    target = path_from('.', target, write=True)
    if target.exists():
        raise GateError('result_directory_exists')
    if not safe(result):
        raise GateError('data_safety_failure')
    target.mkdir(parents=True)
    atomic_json(target / 'result.json', result)  # Internal canonical result for resume/merge.
    projection = {}
    for table in ('shops', 'products'):
        chosen = task['fields'][table]
        columns = list(result[table][0]) if chosen == '*' and result[table] else chosen
        if columns == '*':
            from dataclasses import fields
            from .models import Shop, PublicProduct
            columns = [f.name for f in fields(Shop if table == 'shops' else PublicProduct)]
        projection[table] = [{k: r[k] for k in columns} for r in result[table]]
        if 'csv' in task['output_formats']:
            with (target / (table + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                for r in projection[table]:
                    writer.writerow({k: csv_cell(v) for k, v in r.items()})
    if 'json' in task['output_formats']:
        atomic_json(target / 'compact.json', {'task_id': task['task_id'], **projection})
    manifest = {'task_id': task['task_id'], 'result_digest': digest(result),
                'files': {p.name: file_hash(p) for p in target.iterdir() if p.is_file()}}
    atomic_json(target / 'manifest.json', manifest)
    return manifest


def verify_export(path):
    m = load_json(path / 'manifest.json')
    if any(Path(name).name != name or file_hash(path / name) != sha for name, sha in m['files'].items()):
        raise GateError('artifact_integrity_failure')
    result = load_json(path / 'result.json')
    if result['task_id'] != m['task_id'] or digest(result) != m['result_digest']:
        raise GateError('artifact_integrity_failure')
    return result, m


def merge(paths, output):
    if not paths or len({str(Path(p).resolve()) for p in paths}) != len(paths):
        raise GateError('invalid_merge_inputs')
    all_rows = {'schema': 'groupbuy.batch.v1', 'tasks': [], 'shops': [], 'products': [], 'provenance': {}}
    for p in paths:
        result, manifest = verify_export(Path(p))
        if result['task_id'] in all_rows['tasks']:
            raise GateError('duplicate_merge_task')
        if not result['quality'].get('data_safety_passed') or not result['quality'].get('pagination_complete'):
            raise GateError('merge_quality_failure')
        all_rows['tasks'].append(result['task_id'])
        for table in ('shops', 'products'):
            all_rows[table].extend(result[table])
        if set(all_rows['provenance']) & set(result['provenance']):
            raise GateError('provenance_collision')
        all_rows['provenance'].update(result['provenance'])
    if not safe(all_rows):
        raise GateError('data_safety_failure')
    output = path_from('.', output, write=True)
    if output.exists():
        raise GateError('merge_output_exists')
    atomic_json(output, all_rows)
    return {'task_count': len(all_rows['tasks']), 'shop_count': len(all_rows['shops']), 'product_count': len(all_rows['products'])}
