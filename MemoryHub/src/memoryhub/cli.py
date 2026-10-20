from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from memoryhub.config import db_path, project_root
from memoryhub.core.service import MemoryService
from memoryhub.core.storage import SQLiteMemoryStore


def connect_to_jan() -> int:
    root = project_root()
    python = str(Path(sys.executable).resolve())
    entry = str(root / 'run_memoryhub.py')
    info = ('Name: memoryhub\nTransport: STDIO\nCommand: ' + python +
            '\nArgs (three separate items):\n-I\n' + entry + '\nserve\n\n' +
            'Enable this server in Jan. Then add docs/JAN_PROMPT.md to the conversation instructions.\n')
    target = root / 'jan-connection.txt'
    target.write_text(info,encoding='utf-8')
    print(info)
    print('Connection details saved:',target)
    jan = shutil.which('jan')
    if jan is None:
        print('Jan CLI was not found in PATH. Add the exact values above in Settings > MCP Servers.')
        return 0
    # Jan Desktop installs a model-serving CLI with the same executable name as
    # Jan Agent. Only Jan Agent currently exposes `jan cli mcp`. Probe the exact
    # command instead of inferring capabilities from the executable name/path.
    probe = subprocess.run(
        [jan, 'cli', 'mcp', '--help'],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode:
        print('The installed Jan CLI does not expose `jan cli mcp`.')
        print('Use Jan Settings > MCP Servers with the exact values in jan-connection.txt.')
        return 0
    # Official Jan CLI. Never edit or infer Jan's private configuration location/schema.
    cmd = [jan,'cli','mcp','add','memoryhub','--command',python,'--arg=-I','--arg='+entry,'--arg=serve','--active']
    result = subprocess.run(cmd,check=False)
    if result.returncode:
        print('Jan CLI registration failed. Use the manual Settings form and jan-connection.txt.',file=sys.stderr)
        return result.returncode
    result = subprocess.run([jan,'cli','mcp','enable','memoryhub'],check=False)
    if result.returncode:
        print('Registration completed but enabling failed; enable memoryhub in Jan Settings.',file=sys.stderr)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(prog='memoryhub')
    sub = parser.add_subparsers(dest='command',required=True)
    for name in ('serve','status','connect','test'):
        sub.add_parser(name)
    backup = sub.add_parser('backup')
    backup.add_argument('destination',type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'serve':
            from memoryhub.mcp.server import main as serve
            serve()
        elif args.command == 'connect':
            raise SystemExit(connect_to_jan())
        elif args.command == 'test':
            from memoryhub.selftest import main as selftest
            raise SystemExit(selftest())
        else:
            s = MemoryService(SQLiteMemoryStore(db_path()))
            if args.command == 'status':
                print(json.dumps(s.status(),ensure_ascii=False,indent=2))
            else:
                s.store.backup(args.destination)
                print('Backup saved:',args.destination.resolve())
    except (ValueError,OSError) as exc:
        print('MemoryHub:',str(exc),file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
