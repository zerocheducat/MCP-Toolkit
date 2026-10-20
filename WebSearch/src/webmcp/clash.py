from __future__ import annotations

import time
import asyncio
import copy
import json
from typing import Any
from urllib.parse import urlparse, urlsplit

import httpx

from .config import load_config



def url_for_display(value: str) -> str:
    """Present only the scheme and network endpoint; never expose URL credentials or query parameters."""
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if not hostname:
            return "[configured URL]"
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{hostname}{port}"
    except (ValueError, AttributeError):
        return "[configured URL]"


def _proxy_for_httpx(proxy_url: str) -> str | None:
    return proxy_url or None


def _controller_headers(config: dict[str, Any]) -> dict[str, str]:
    secret = str(config.get("clash_controller_secret") or "").strip()
    return {"Authorization": f"Bearer {secret}"} if secret else {}


_status_lock = asyncio.Lock()
_cached_status = None


async def status(*, force: bool = False, config=None) -> dict[str, Any]:
    """Reuse a recent probe, keyed by the exact effective configuration."""
    global _cached_status
    config = dict(config) if config is not None else load_config()
    key = json.dumps(config, sort_keys=True, ensure_ascii=False)
    async with _status_lock:
        if not force and _cached_status is not None:
            previous_key, until, result = _cached_status
            if previous_key == key and time.monotonic() < until:
                return copy.deepcopy(result)
        result = await _probe_status(config)
        _cached_status = (key, time.monotonic() + (15.0 if result.get("ok") else 2.0), copy.deepcopy(result))
        return result


async def _probe_status(config) -> dict[str, Any]:
    proxy_url = str(config.get("proxy_url") or "")
    if config["require_proxy"] and not proxy_url:
        return {
            "ok": False,
            "message": "Clash 代理未配置：webmcp_config.json 的 proxy_url 为空。",
            "proxy": {"configured": False, "ok": False, "url": ""},
            "controller": {"configured": bool(config.get("clash_controller_url")), "ok": None},
        }

    proxy_result: dict[str, Any] = {
        "configured": bool(proxy_url),
        "ok": False if proxy_url else None,
        "url": url_for_display(proxy_url),
        "latency_ms": None,
        "probe_url": config["probe_url"],
        "status_code": None,
        "error": "",
    }
    if proxy_url:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                proxy=_proxy_for_httpx(proxy_url),
                timeout=float(config["probe_timeout_seconds"]),
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = await client.get(str(config["probe_url"]), headers={"User-Agent": "Aicy-WebMCP/0.1"})
            proxy_result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            proxy_result["status_code"] = response.status_code
            proxy_result["ok"] = 200 <= response.status_code < 400
            if not proxy_result["ok"]:
                proxy_result["error"] = f"probe HTTP {response.status_code}"
        except Exception as exc:
            proxy_result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            proxy_result["error"] = f"{type(exc).__name__}: connection failed"
            proxy_result["ok"] = False

    controller_url = str(config.get("clash_controller_url") or "").rstrip("/")
    controller_result: dict[str, Any] = {
        "configured": bool(controller_url),
        "ok": None,
        "url": url_for_display(controller_url),
        "version": None,
        "error": "",
    }
    if controller_url:
        try:
            async with httpx.AsyncClient(timeout=float(config["probe_timeout_seconds"]), trust_env=False) as client:
                response = await client.get(f"{controller_url}/version", headers=_controller_headers(config))
            payload = response.json() if "json" in response.headers.get("content-type", "") else {}
            controller_result["ok"] = response.status_code == 200
            controller_result["version"] = payload.get("version") if isinstance(payload, dict) else None
            if response.status_code != 200:
                controller_result["error"] = f"HTTP {response.status_code}"
        except Exception as exc:
            controller_result["ok"] = False
            controller_result["error"] = f"{type(exc).__name__}: connection failed"

    proxy_ok = True if not config["require_proxy"] else bool(proxy_result["ok"])
    controller_ok = True
    if config["clash_controller_required"]:
        controller_ok = bool(controller_result["configured"] and controller_result["ok"])
    ok = bool(proxy_ok and controller_ok)
    if ok:
        message = "Clash 网络链路正常"
    elif config["require_proxy"] and not proxy_result["ok"]:
        message = f"Clash 代理不可用：{proxy_result['error'] or '探测失败'}"
    else:
        message = f"Clash Controller 不可用：{controller_result['error'] or '探测失败'}"
    return {"ok": ok, "message": message, "proxy": proxy_result, "controller": controller_result}


def playwright_proxy_settings(proxy_url: str) -> dict[str, str] | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    host = parsed.hostname or ""
    port = parsed.port
    scheme = parsed.scheme
    if scheme == "socks5h":
        scheme = "socks5"
    settings: dict[str, str] = {"server": f"{scheme}://{host}:{port}"}
    if parsed.username:
        settings["username"] = parsed.username
    if parsed.password:
        settings["password"] = parsed.password
    return settings
