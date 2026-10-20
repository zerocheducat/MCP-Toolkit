"""Dependency-light launcher and setup entry for Aicy Web MCP."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def _system_browser() -> str:
    names = ("chrome.exe", "msedge.exe") if os.name == "nt" else ("google-chrome", "microsoft-edge", "chromium", "chromium-browser")
    for name in names:
        found = shutil.which(name)
        if found and Path(found).is_file():
            return str(Path(found).resolve())

    if os.name == "nt":
        try:
            import winreg
            for exe_name in ("chrome.exe", "msedge.exe"):
                subkey = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    for view in (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
                        try:
                            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                                value, _ = winreg.QueryValueEx(key, None)
                            path = Path(str(value or "").strip().strip('"'))
                            if path.is_file():
                                return str(path.resolve())
                        except OSError:
                            continue
        except Exception:
            pass

    candidates = []
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.getenv(env_name)
        if not base:
            continue
        base_path = Path(base)
        candidates.extend([
            base_path / "Google/Chrome/Application/chrome.exe",
            base_path / "Microsoft/Edge/Application/msedge.exe",
        ])
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    return ""


def _write_connection(python: str) -> None:
    data = {
        "name": "WebSearch",
        "transport": "stdio",
        "command": python,
        "args": ["-I", str((ROOT / "run_webmcp.py").resolve()), "serve"],
        "env": {},
        "cwd": str(ROOT.resolve()),
        "enabled": True,
    }
    (ROOT / "aicy-connection.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def setup(source: str) -> int:
    if sys.version_info < (3, 10):
        print("WebMCP requires Python 3.10 or newer.", file=sys.stderr)
        return 1
    index = {"pypi": "https://pypi.org/simple", "aliyun": "https://mirrors.aliyun.com/pypi/simple/"}[source]
    python = sys.executable
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST"):
        env.pop(key, None)
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PYTHONNOUSERSITE"] = "1"
    commands = [
        [python, "-m", "pip", "install", "--index-url", index, "--upgrade", "setuptools", "wheel"],
        [python, "-m", "pip", "install", "--index-url", index, "--no-build-isolation", "-e", str(ROOT)],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode:
            return result.returncode
    browser = _system_browser()
    config_path = ROOT / "webmcp_config.json"
    if not config_path.exists():
        example = json.loads((ROOT / "webmcp_config.example.json").read_text(encoding="utf-8"))
        if browser:
            example["browser_executable"] = browser
        config_path.write_text(json.dumps(example, ensure_ascii=False, indent=2), encoding="utf-8")
    elif browser:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and not str(data.get("browser_executable") or "").strip():
                data["browser_executable"] = browser
                config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    if not browser:
        print("No installed Edge/Chrome was detected; installing Playwright Chromium...", flush=True)
        result = subprocess.run([python, "-m", "playwright", "install", "chromium"], cwd=ROOT, env=env, check=False)
        if result.returncode:
            return result.returncode
    _write_connection(python)
    print("Installed into:", python)
    print("Aicy connection file:", ROOT / "aicy-connection.json")
    print("Next: configure the exact Clash proxy URL with:")
    print(f'  "{python}" -I "{ROOT / "run_webmcp.py"}" configure')
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"setup", "--setup"}:
        parser = argparse.ArgumentParser(prog="python run_webmcp.py --setup")
        parser.add_argument("source", nargs="?", choices=("pypi", "aliyun"), default="pypi")
        return setup(parser.parse_args(args[1:]).source)
    try:
        from webmcp.cli import main as cli_main
    except ModuleNotFoundError as exc:
        print(f"Missing dependency: {exc}\nRun: python run_webmcp.py --setup", file=sys.stderr)
        return 1
    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
