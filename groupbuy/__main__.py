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
    sim = sub.add_parser('simulate', help='Full fictional HTTP/SSE + capture + export checks; no Key or desktop')
    sim.add_argument('--details', action='store_true', help='Include per-scenario details in console output')
    sim.add_argument('--output-dir', help='Custom output parent directory; D: is required on Windows')
    sub.add_parser('capture-init', help='Prepare the private loopback URL for Reqable report server')
    capture_probe = sub.add_parser('capture-probe', help='Key-free report connectivity test; no UI or payload storage')
    capture_probe.add_argument('--seconds', type=float, default=30)
    dashboard = sub.add_parser('dashboard', help='Local web UI; CLI and UI share the same Agent')
    dashboard.add_argument('--port', type=int, default=8787)
    r = sub.add_parser('run')
    r.add_argument('--offline', action='store_true')
    u = sub.add_parser('ui')
    u.add_argument('--task', required=True)
    u.add_argument('--resume', action='store_true')
    retry = sub.add_parser('retry')
    retry.add_argument('--task', required=True)
    sub.add_parser('schedule')
    a = sub.add_parser('agent', help='Independent multi-provider tool loop; no Codex required')
    a.add_argument('--settings', default='examples/agent.example.json')
    a.add_argument('--provider', help='openai, deepseek, qwen, kimi, anthropic, openai_compatible, responses_compatible')
    a.add_argument('--model', help='Model available in the selected provider account')
    a.add_argument('--base-url', help='API base URL, without endpoint suffix; never embed credentials')
    a.add_argument('--reasoning-effort', choices=['none', 'minimal', 'low', 'medium', 'high', 'xhigh'])
    a.add_argument('--allow-ui', action='store_true')
    a.add_argument('--demo', action='store_true', help='Scripted synthetic demo; no API calls')
    a.add_argument('--output-dir', help='Custom demo output parent; live tasks use runtime.output_dir in config')
    a.add_argument('--schedule', action='store_true', help='Foreground Agent schedule, disabled by default')
    probe = sub.add_parser('model-check', help='One API tool-call probe; no collection or UI actions')
    probe.add_argument('--settings', default='examples/agent.example.json')
    probe.add_argument('--provider')
    probe.add_argument('--model')
    probe.add_argument('--base-url')
    probe.add_argument('--reasoning-effort', choices=['none', 'minimal', 'low', 'medium', 'high', 'xhigh'])
    m = sub.add_parser('merge')
    m.add_argument('--inputs', nargs='+', required=True)
    m.add_argument('--output', required=True)
    args = p.parse_args()
    try:
        if args.command == 'simulate':
            from .simulation import run_simulation
            result = run_simulation(output_dir=args.output_dir)
            summary = {k: result[k] for k in ('status', 'backend', 'synthetic', 'live_collection_verified',
                       'cases_passed', 'cases_total', 'external_api_requests', 'desktop_actions',
                       'report_path', 'summary_path')}
            normal = next((c for c in result['scenarios'] if c['scenario'] == 'batch_and_resume' and c['passed']), {})
            summary.update({k: normal[k] for k in ('completed_tasks', 'shops', 'products', 'http_posts') if k in normal})
            print(json.dumps(result if args.details else summary, ensure_ascii=False, indent=2))
            return 0 if result['status'] == 'PASS' else 2
        if args.command == 'model-check':
            from .agent_runtime import load_settings
            from .model_providers import make_transport, probe_model
            settings = load_settings(args.settings, provider=args.provider, model=args.model,
                                     base_url=args.base_url, reasoning_effort=args.reasoning_effort)
            result = probe_model(settings, make_transport(settings))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status'] == 'MODEL_READY' else 2
        c = load_config(args.config)
        runner = Runner(c)
        if args.command == 'plan':
            result = runner.plan()
        elif args.command == 'capture-init':
            from .capture import capture_info
            result = capture_info(c)
        elif args.command == 'dashboard':
            from .dashboard import serve_dashboard
            serve_dashboard(c, args.port)
            return 0
        elif args.command == 'doctor':
            from .diagnostics import doctor
            result = doctor(c)
        elif args.command == 'capture-probe':
            from .capture import probe_capture
            result = probe_capture(c, args.seconds)
        elif args.command == 'merge':
            from .exporter import merge
            from .common import path_from
            result = merge([path_from(c['_root'], x) for x in args.inputs], path_from(c['_root'], args.output, write=True))
        elif args.command == 'retry':
            result = runner.retry(args.task)
        elif args.command == 'agent':
            from .agent import run_agent, serve_agent
            from .agent_runtime import load_settings
            settings = load_settings(args.settings, provider=args.provider, model=args.model,
                                     base_url=args.base_url, reasoning_effort=args.reasoning_effort)
            if args.demo and args.schedule:
                raise GateError('demo_schedule_rejected')
            if args.output_dir is not None and not args.demo:
                raise GateError('output_override_requires_demo')
            result = (serve_agent(c, settings, allow_ui=args.allow_ui) if args.schedule else
                      run_agent(c, settings, allow_ui=args.allow_ui, demo=args.demo, output_dir=args.output_dir))
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
        if isinstance(result, dict) and result.get('status') in {
                'ATTENTION_REQUIRED', 'STOPPED', 'REVIEW', 'WAITING_INPUT', 'UI_REQUIRED',
                'UI_UNAVAILABLE', 'INCOMPLETE', 'INTERRUPTED'}:
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
