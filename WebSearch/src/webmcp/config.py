from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("WEBMCP_CONFIG_PATH", ROOT / "webmcp_config.json"))
SCREENSHOT_DIR = ROOT / "screenshots"

DEFAULT_CONFIG: dict[str, Any] = {
    "proxy_url": "",
    "require_proxy": True,
    "probe_url": "https://www.gstatic.com/generate_204",
    "probe_timeout_seconds": 6.0,
    "clash_controller_url": "",
    "clash_controller_secret": "",
    "clash_controller_required": False,
    "search_provider": "duckduckgo_rendered_browser",
    "browser_headless": False,
    "browser_executable": "",
    "browser_cdp_url": "",
    "browser_timeout_ms": 15000,
    "max_search_results": 10,
}


def _validate_proxy_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
        raise ValueError("proxy_url 必须是包含协议、主机和端口的完整代理地址，例如 http://127.0.0.1:7890")
    return value


def _validate_cdp_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("browser_cdp_url 必须是浏览器远程调试地址，例如 http://127.0.0.1:9222")
    return value


@lru_cache(maxsize=8)
def _read_config(path: str, content: str) -> dict[str, Any]:
    # Key by the actual small config text, not filesystem timestamps: Windows
    # timestamp resolution can leave same-sized rapid edits looking unchanged.
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("webmcp_config.json 顶层必须是 JSON object")
    return data


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        content = CONFIG_PATH.read_text(encoding="utf-8")
        data = _read_config(str(CONFIG_PATH), content)
        for key in DEFAULT_CONFIG:
            if key in data:
                config[key] = data[key]
    env_proxy = os.getenv("WEBMCP_PROXY_URL")
    if env_proxy is not None:
        config["proxy_url"] = env_proxy
    config["proxy_url"] = _validate_proxy_url(str(config.get("proxy_url") or ""))
    config["require_proxy"] = bool(config.get("require_proxy", True))
    config["clash_controller_required"] = bool(config.get("clash_controller_required", False))
    config["browser_headless"] = bool(config.get("browser_headless", True))
    config["browser_executable"] = str(config.get("browser_executable") or "").strip()
    env_cdp = os.getenv("WEBMCP_BROWSER_CDP_URL")
    if env_cdp is not None:
        config["browser_cdp_url"] = env_cdp
    config["browser_cdp_url"] = _validate_cdp_url(config.get("browser_cdp_url", ""))
    config["probe_timeout_seconds"] = max(1.0, min(float(config.get("probe_timeout_seconds", 6.0)), 60.0))
    config["browser_timeout_ms"] = max(1000, min(int(config.get("browser_timeout_ms", 15000)), 120000))
    config["max_search_results"] = max(1, min(int(config.get("max_search_results", 10)), 30))
    if config.get("search_provider") != "duckduckgo_rendered_browser":
        raise ValueError("当前版本仅支持精确 search_provider='duckduckgo_rendered_browser'")
    return config


def save_config(values: dict[str, Any]) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                config.update({k: current[k] for k in DEFAULT_CONFIG if k in current})
        except Exception:
            pass
    for key in DEFAULT_CONFIG:
        if key in values:
            config[key] = values[key]
    config["proxy_url"] = _validate_proxy_url(str(config.get("proxy_url") or ""))
    config["browser_cdp_url"] = _validate_cdp_url(config.get("browser_cdp_url", ""))
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, CONFIG_PATH)
    return load_config()
