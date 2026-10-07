"""Reproducibly generate demonstrably fictional fixtures, never real captures."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from tests.support import config, entries, evidence


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    c = config(root)
    write(root / 'examples/config.synthetic.json', c)
    for t in c['tasks']:
        es = entries()
        for e in es:
            e.pop('source_hash')
            e.pop('entry_index')
        write(root / t['input']['path'], {'schema': 'groupbuy.local-responses.v1', 'synthetic': True, 'entries': es})
        write(root / t['input']['evidence'], evidence(t))
    c['synthetic'] = True
    c['mode'] = 'live'
    c['tasks'] = c['tasks'][:1]
    t = c['tasks'][0]
    t['input'] = {'kind': 'har', 'path': '../groupbuy-local/inputs/search.har', 'evidence': '../groupbuy-local/inputs/ui-evidence.json'}
    t['ui_profile'] = {'window_title_regex': '大众点评', 'process_names': ['WeChat.exe', 'Weixin.exe'],
                       'list_anchor': '智能排序',
                       'steps': [{'action': 'type_search', 'text': '搜索商家'},
                                 {'action': 'click', 'text': '美食'}, {'action': 'click', 'text': '3km'}]}
    write(root / 'examples/config.live-template.json', c)
