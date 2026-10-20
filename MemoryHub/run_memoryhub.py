"""Dependency-free launcher. Supports direct execution and --setup in an active environment."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def setup(source: str = 'pypi') -> int:
    if sys.version_info < (3, 10):
        print('MemoryHub requires Python 3.10 or newer.', file=sys.stderr)
        return 1
    index = {'pypi': 'https://pypi.org/simple', 'aliyun': 'https://mirrors.aliyun.com/pypi/simple/'}[source]
    python = sys.executable
    print('Installing into this exact interpreter:', python, flush=True)
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_TRUSTED_HOST'):
        env.pop(key, None)
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PYTHONNOUSERSITE'] = '1'
    commands = [
        [python, '-m', 'pip', 'install', '--index-url', index, '--upgrade', 'setuptools', 'wheel'],
        [python, '-m', 'pip', 'install', '--index-url', index, '--no-build-isolation', '-e', str(ROOT)],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode:
            print('Installation failed. Keep the complete error output above.', file=sys.stderr)
            return result.returncode
    (ROOT / '.memoryhub-python-path').write_text(python, encoding='utf-8')
    for command in ('test', 'connect'):
        result = subprocess.run([python, '-I', str(ROOT / 'run_memoryhub.py'), command], cwd=ROOT, env=env, check=False)
        if result.returncode:
            print(command + ' failed. Keep the complete error output above.', file=sys.stderr)
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('MemoryHub\n1. Install / update and connect to Jan\n2. Test MCP\n3. Connect to Jan\n4. Status\n0. Exit')
        print('Direct install: python run_memoryhub.py --setup')
        if not sys.stdin.isatty():
            return 0
        try:
            choice = input('Choose [0]: ').strip() or '0'
        except EOFError:
            return 0
        if choice == '0':
            return 0
        commands = {'1': 'setup', '2': 'test', '3': 'connect', '4': 'status'}
        if choice not in commands:
            print('Choose 0, 1, 2, 3 or 4.', file=sys.stderr)
            return 2
        args = [commands[choice]]
    if args[0] in ('setup', '--setup'):
        parser = argparse.ArgumentParser(prog='python run_memoryhub.py --setup')
        parser.add_argument('source', nargs='?', choices=('pypi', 'aliyun'), default='pypi')
        return setup(parser.parse_args(args[1:]).source)
    try:
        from memoryhub.cli import main as app_main
    except ModuleNotFoundError as exc:
        print('Missing dependency: ' + str(exc) + '\nRun: python run_memoryhub.py --setup', file=sys.stderr)
        return 1
    original = sys.argv
    try:
        sys.argv = [str(ROOT / 'run_memoryhub.py'), *args]
        app_main()
    finally:
        sys.argv = original
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except OSError as exc:
        print('MemoryHub launcher:', str(exc), file=sys.stderr)
        raise SystemExit(1)
