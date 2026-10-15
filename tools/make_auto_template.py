"""Generate only the fictional automatic-capture example."""
import json
from copy import deepcopy
from pathlib import Path

root = Path(__file__).resolve().parents[1]
value = json.loads((root / 'examples/config.live-template.json').read_text(encoding='utf-8'))
value['capture'] = {'enabled': True, 'port': 8765,
                    'settings_path': 'runtime/capture/receiver.local.json',
                    'storage_dir': 'runtime/capture', 'settle_seconds': 2, 'timeout_seconds': 30}
sample = value['tasks'][0]
sample['ui_profile']['process_names'].append('WeChatAppEx.exe')
sample['ui_profile']['calibration_required'] = True
sample['filters']['range_text'] = '按页面填写'
sample['ui_profile']['steps'] = [
    {'action': 'type_search', 'text': '搜索商家'},
    *({'action': 'click', 'text': label} for label in
      ('全部分类', '美食', '全部美食', '全城', '附近', '按页面填写'))]
value['tasks'] = []
for n in range(1, 6):
    task = deepcopy(sample)
    task['task_id'] = f'AUTO-{n:03}'
    task['search_term'] = f'虚构广场{n}'
    task['target_location'] = {'city': '示例市', 'center_name': task['search_term']}
    task['input'] = {'kind': 'reqable', 'path': f'runtime/capture/AUTO-{n:03}',
                     'evidence': f'runtime/manual-evidence/AUTO-{n:03}.json'}
    value['tasks'].append(task)
(root / 'examples/config.auto-template.json').write_text(
    json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
