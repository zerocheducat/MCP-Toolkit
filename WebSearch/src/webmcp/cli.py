from __future__ import annotations

import argparse
import asyncio
import json
from getpass import getpass
from pathlib import Path

from .clash import status, url_for_display
from .config import CONFIG_PATH, load_config, save_config
from .browser import browser_runtime_info


def _json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_configure(args: argparse.Namespace) -> int:
    updates = {}
    if args.proxy_url is not None:
        updates["proxy_url"] = args.proxy_url
    if args.controller_url is not None:
        updates["clash_controller_url"] = args.controller_url
    if args.controller_secret is not None:
        updates["clash_controller_secret"] = args.controller_secret
    if args.probe_url is not None:
        updates["probe_url"] = args.probe_url
    if args.browser_executable is not None:
        updates["browser_executable"] = args.browser_executable
    if args.browser_cdp_url is not None:
        updates["browser_cdp_url"] = args.browser_cdp_url
    if not updates and not args.non_interactive:
        current = load_config()
        proxy = input(f"Clash proxy URL [{url_for_display(str(current.get('proxy_url') or ''))}]: ").strip()
        if proxy:
            updates["proxy_url"] = proxy
        controller = input(f"Clash Controller URL (optional) [{url_for_display(str(current.get('clash_controller_url') or ''))}]: ").strip()
        if controller:
            updates["clash_controller_url"] = controller
        if controller:
            secret = getpass("Clash Controller secret (optional, hidden): ").strip()
            if secret:
                updates["clash_controller_secret"] = secret
    config = save_config(updates)
    print("Saved:", CONFIG_PATH)
    # Local config remains intact, but command output must not reveal credentials.
    public_config = dict(config)
    public_config["clash_controller_secret"] = "[REDACTED]" if config.get("clash_controller_secret") else ""
    for key in ("proxy_url", "clash_controller_url", "probe_url", "browser_cdp_url"):
        public_config[key] = url_for_display(str(config.get(key) or ""))
    _json(public_config)
    return 0


def cmd_status() -> int:
    result = asyncio.run(status())
    result = {**result, "browser": browser_runtime_info()}
    _json(result)
    return 0 if result.get("ok") else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="webmcp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("status")
    configure = sub.add_parser("configure")
    configure.add_argument("--proxy-url")
    configure.add_argument("--controller-url")
    configure.add_argument("--controller-secret")
    configure.add_argument("--probe-url")
    configure.add_argument("--browser-executable")
    configure.add_argument("--browser-cdp-url")
    configure.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "serve":
        from .mcp.server import main as serve
        serve()
        return 0
    if args.command == "status":
        return cmd_status()
    if args.command == "configure":
        return cmd_configure(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
