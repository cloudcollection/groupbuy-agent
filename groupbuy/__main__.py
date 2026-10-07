"""CLI outputs only counts, task status and controlled error codes."""
import argparse
import json
from .common import GateError
from .config import load_config
from .runner import Runner


def main():
    p = argparse.ArgumentParser(prog='groupbuy')
    p.add_argument('--config', default='examples/config.synthetic.json')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('plan')
    sub.add_parser('doctor')
    r = sub.add_parser('run')
    r.add_argument('--offline', action='store_true')
    u = sub.add_parser('ui')
    u.add_argument('--task', required=True)
    u.add_argument('--resume', action='store_true')
    retry = sub.add_parser('retry')
    retry.add_argument('--task', required=True)
    sub.add_parser('schedule')
    m = sub.add_parser('merge')
    m.add_argument('--inputs', nargs='+', required=True)
    m.add_argument('--output', required=True)
    args = p.parse_args()
    try:
        c = load_config(args.config)
        runner = Runner(c)
        if args.command == 'plan':
            result = runner.plan()
        elif args.command == 'doctor':
            import importlib.util
            import os
            result = {'platforms': ['dianping'], 'core_ready': True,
                      'live_environment': os.name == 'nt' and all(importlib.util.find_spec(x) for x in ('pywinauto', 'rapidocr_onnxruntime', 'psutil')),
                      'live_verified': False, 'schedule_enabled': c['schedule']['enabled']}
        elif args.command == 'merge':
            from .exporter import merge
            from .common import path_from
            result = merge([path_from(c['_root'], x) for x in args.inputs], path_from(c['_root'], args.output, write=True))
        elif args.command == 'retry':
            result = runner.retry(args.task)
        else:
            driver = None
            if args.command == 'ui' or c['mode'] == 'live' and not getattr(args, 'offline', False):
                from .windows_ui import WindowsDriver
                driver = WindowsDriver()
            if args.command == 'ui':
                result = runner.ui_segment(args.task, driver, args.resume)
            elif args.command == 'schedule':
                from .scheduler import serve
                serve(runner, driver)
                return 0
            else:
                result = runner.run(driver, args.offline)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get('status') in {'ATTENTION_REQUIRED', 'STOPPED', 'REVIEW'}:
            return 2
        return 0
    except KeyboardInterrupt:
        print('{"status":"INTERRUPTED"}')
        return 130
    except Exception as error:
        code = error.code if isinstance(error, GateError) else 'operation_failed'
        print(json.dumps({'status': 'STOPPED', 'code': code}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
