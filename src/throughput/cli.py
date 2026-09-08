"""JSON-only controller CLI. No provider imports."""
import argparse
import json
from pathlib import Path
from .controller import ThroughputController


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'run-once', 'consume', 'status', 'report',
        'validate-config', 'discover', 'inspect', 'clone-test'))
    parser.add_argument('--episode', required=True)
    parser.add_argument('--config')
    parser.add_argument('--output-dir')
    parser.add_argument('--test-mode', action='store_true')
    parser.add_argument('--poll-interval', type=float, default=1)
    parser.add_argument('--max-seconds', type=float)
    args = parser.parse_args(argv)
    try:
        if args.command == 'clone-test':
            if not args.test_mode or not args.output_dir or args.config:
                raise ValueError('clone-test requires --test-mode and --output-dir, and forbids --config')
            from .discovery import run_test_clone
            fixture_worker = Path(__file__).resolve().parents[2] / 'tests' / 'throughput_fixture_worker.py'
            result = run_test_clone(args.episode, args.output_dir, fixture_worker=fixture_worker)
        else:
            controller = ThroughputController(args.episode, config_path=args.config, test_mode=args.test_mode)
            if args.command in ('discover', 'inspect'):
                from .discovery import discover
                result = discover(args.episode)
            elif args.command == 'run':
                result = controller.run(poll_interval=args.poll_interval, max_seconds=args.max_seconds)
            elif args.command in ('run-once', 'consume'):
                result = controller.run_once()
            elif args.command == 'validate-config':
                result = controller.validate_config()
            else:
                result = getattr(controller, args.command)()
    except (ValueError, OSError, KeyError, TypeError) as error:
        result = {'status': 'BLOCKED_PREREQUISITE', 'reason': str(error)}
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 2 if result.get('status') == 'BLOCKED_PREREQUISITE' else 0
