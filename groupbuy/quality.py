"""Quality gates run before any export or completion update."""
from .common import GateError
from .privacy import clean, safe


def validate_result(task, result):
    # Sanitization happens here even if a platform plugin failed to do it.
    result = clean(result)
    if result.get('task_id') != task['task_id'] or not result['quality'].get('pagination_complete'):
        raise GateError('result_identity_or_pagination_failure')
    shops = {(s['task_id'], s['platform'], s['shop_id']) for s in result['shops']}
    if len(shops) != len(result['shops']):
        raise GateError('duplicate_shop_identity')
    for row in result['shops'] + result['products']:
        if row['task_id'] != task['task_id'] or row['platform'] != task['platform']:
            raise GateError('cross_task_record')
        if row['provenance_id'] not in result['provenance']:
            raise GateError('missing_provenance')
    for row in result['products']:
        if (row['task_id'], row['platform'], row['shop_id']) not in shops:
            raise GateError('orphan_product')
    if not safe(result):
        raise GateError('data_safety_failure')
    result['quality']['data_safety_passed'] = True
    result['quality']['association_passed'] = True
    result['quality']['review_shop_count'] = sum(bool(s['quality_flags']) for s in result['shops'])
    return result
