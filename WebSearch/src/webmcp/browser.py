from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .clash import playwright_proxy_settings, status as clash_status
from .config import SCREENSHOT_DIR, load_config

def _windows_default_browser() -> str:
    """Read Windows' actual HTTPS default-browser association.

    Only Chromium-family executables are returned because this browser manager
    is driven through Playwright's Chromium protocol.
    """
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
            0,
            winreg.KEY_READ,
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
        prog_id = str(prog_id or "").strip()
        if not prog_id:
            return ""
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"{prog_id}\shell\open\command",
            0,
            winreg.KEY_READ,
        ) as key:
            command, _ = winreg.QueryValueEx(key, None)
        command = str(command or "").strip()
    except OSError:
        return ""

    match = re.match(r'^\s*"([^"]+\.exe)"', command, flags=re.IGNORECASE)
    if match:
        executable = match.group(1)
    else:
        match = re.match(r"^\s*([^\s]+\.exe)", command, flags=re.IGNORECASE)
        executable = match.group(1) if match else ""
    if not executable or not os.path.isfile(executable):
        return ""
    basename = Path(executable).name.lower()
    chromium_names = {
        "chrome.exe",
        "msedge.exe",
        "brave.exe",
        "chromium.exe",
        "vivaldi.exe",
    }
    if basename not in chromium_names:
        return ""
    return str(Path(executable).resolve())


def resolve_system_browser() -> tuple[str, str]:
    """Resolve a real installed Chromium-family browser and its source."""
    # Pixiv's MCP uses the installed Chrome channel. Prefer the same browser
    # here as well, even when Windows' default browser happens to be Edge.
    # Each MCP still launches an isolated Playwright profile.
    if os.name == "nt":
        chrome = _find_chrome_installation()
        if chrome:
            return chrome, "preferred-chrome"
    default_browser = _windows_default_browser()
    if default_browser:
        return default_browser, "windows-default-browser"

    names = ("msedge.exe", "chrome.exe") if os.name == "nt" else (
        "microsoft-edge", "google-chrome", "chromium", "chromium-browser"
    )
    for name in names:
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return str(Path(found).resolve()), "path"

    if os.name == "nt":
        try:
            import winreg

            registry_names = ("msedge.exe", "chrome.exe")
            roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
            views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
            for exe_name in registry_names:
                subkey = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
                for hive in roots:
                    for view in views:
                        try:
                            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                                value, _ = winreg.QueryValueEx(key, None)
                            value = str(value or "").strip().strip('"')
                            if value and os.path.isfile(value):
                                return str(Path(value).resolve()), "windows-app-paths"
                        except OSError:
                            continue
        except Exception:
            pass

        candidates: list[Path] = []
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.getenv(env_name)
            if not base:
                continue
            base_path = Path(base)
            candidates.extend([
                base_path / "Microsoft/Edge/Application/msedge.exe",
                base_path / "Google/Chrome/Application/chrome.exe",
            ])
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve()), "known-install-root"
    return "", ""


def _find_chrome_installation() -> str:
    found = shutil.which("chrome.exe") if os.name == "nt" else shutil.which("google-chrome")
    if found and os.path.isfile(found):
        return str(Path(found).resolve())
    if os.name == "nt":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
                    try:
                        with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                            value, _ = winreg.QueryValueEx(key, None)
                        path = str(value or "").strip().strip('"')
                        if os.path.isfile(path):
                            return str(Path(path).resolve())
                    except OSError:
                        continue
        except Exception:
            pass
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.getenv(env_name)
            if base:
                path = Path(base) / "Google/Chrome/Application/chrome.exe"
                if path.is_file():
                    return str(path.resolve())
    return ""


def detect_system_browser() -> str:
    return resolve_system_browser()[0]


def browser_runtime_info() -> dict[str, Any]:
    config = load_config()
    cdp_url = str(config.get("browser_cdp_url") or "").strip()
    configured = str(config.get("browser_executable") or "").strip()
    if configured:
        resolved = configured if os.path.isfile(configured) else ""
        source = "configured" if resolved else "configured-missing"
    else:
        resolved, source = resolve_system_browser()
        if not resolved:
            source = "system-browser-not-found"
    return {
        "configured_executable": configured,
        "resolved_executable": resolved,
        "source": source,
        "headless": bool(config.get("browser_headless", True)),
        "mode": "attached-existing-browser" if cdp_url else "launch-local-browser",
        "cdp_configured": bool(cdp_url),
    }


class BrowserManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._page_lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._owns_context = False
        self._owns_browser = False
        self._pages: dict[str, Page] = {}
        self._status_codes: dict[str, int | None] = {}
        self._shutdown_errors: list[str] = []
        # A challenge response is a signal to stop automation on that host for
        # a short period. It must not poison navigation to unrelated hosts.
        self._limited_hosts: dict[str, float] = {}

    def _check_host_cooldown(self, url: str) -> str:
        host = (urlparse(url).hostname or "").casefold()
        until = self._limited_hosts.get(host, 0.0)
        if until <= time.monotonic():
            self._limited_hosts.pop(host, None)
            return host
        raise RuntimeError(
            f"{host} 刚返回了风控/访问限制页面，已暂停自动重开；可继续操作现有页面，或稍后重试。"
        )

    @staticmethod
    async def _challenge_info(page: Page, status: int | None) -> tuple[bool, str]:
        if status in {401, 403, 429}:
            return True, f"HTTP {status}"
        try:
            text = ((await page.title()) + " " + (await page.locator("body").inner_text(timeout=2500)))[:12000].casefold()
        except Exception:
            text = (await page.title()).casefold()
        markers = (
            "captcha", "verify you are human", "unusual traffic", "access denied",
            "checking your browser", "challenge-platform", "人机验证", "访问过于频繁", "安全验证",
            "很遗憾，机器人也使用 duckduckgo", "选择所有包含鸭子的正方形",
        )
        marker = next((item for item in markers if item in text), None)
        return (True, f"页面包含验证标记：{marker}") if marker else (False, "")

    async def _ensure(self, *, headless: bool | None = None) -> None:
        if self._browser is not None and self._browser.is_connected() and self._context is not None:
            return
        async with self._lock:
            if self._browser is not None and self._browser.is_connected() and self._context is not None:
                return
            config = load_config()
            network = await clash_status(config=config)
            if not network.get("ok"):
                raise RuntimeError(str(network.get("message") or "Clash 不可用"))
            cdp_url = str(config.get("browser_cdp_url") or "").strip()
            self._playwright = await async_playwright().start()
            if cdp_url:
                try:
                    self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
                    contexts = self._browser.contexts
                    if contexts:
                        self._context = contexts[0]
                        self._owns_context = False
                    else:
                        self._context = await self._browser.new_context(
                            viewport={"width": 1440, "height": 900},
                            locale="zh-CN",
                        )
                        self._owns_context = True
                    self._context.set_default_timeout(int(config["browser_timeout_ms"]))
                    self._owns_browser = False
                    return
                except BaseException as exc:
                    await self.shutdown()
                    if not isinstance(exc, Exception):
                        raise
                    raise RuntimeError(
                        f"无法连接现有 Edge/Chrome：{type(exc).__name__}: {exc}。"
                        "请用 --remote-debugging-port 启动浏览器，或清空 browser_cdp_url 让 WebSearch 启动独立窗口。"
                    ) from exc

            launch_kwargs: dict[str, Any] = {
                "headless": bool(config["browser_headless"] if headless is None else headless),
            }
            configured_executable = str(config.get("browser_executable") or "").strip()
            if configured_executable and not os.path.isfile(configured_executable):
                raise RuntimeError(f"browser_executable 不存在：{configured_executable}")
            executable = configured_executable or detect_system_browser()
            if os.name == "nt" and not executable:
                raise RuntimeError(
                    "未解析到可由 Playwright 驱动的系统 Chromium 浏览器。"
                    "为避免再次启动受 Windows 防火墙策略拦截的 chrome-headless-shell.exe，"
                    "WebMCP 已停止自动回退。请先运行 status 查看 browser.source，"
                    "或在 webmcp_config.json 中设置 browser_executable 为本机实际 Chrome/Edge 路径。"
                )
            if executable:
                launch_kwargs["executable_path"] = executable
            proxy = playwright_proxy_settings(str(config.get("proxy_url") or ""))
            if proxy:
                launch_kwargs["proxy"] = proxy
            try:
                self._browser = await self._playwright.chromium.launch(**launch_kwargs)
                self._context = await self._browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    locale="zh-CN",
                )
                self._context.set_default_timeout(int(config["browser_timeout_ms"]))
                self._owns_context = True
                self._owns_browser = True
            except BaseException as exc:
                await self.shutdown()
                if not isinstance(exc, Exception):
                    raise
                source = "系统 Chromium 浏览器" if executable else "浏览器"
                raise RuntimeError(f"无法启动{source}：{type(exc).__name__}: {exc}") from exc

    async def _new_page(self, *, headless: bool | None = None) -> tuple[str, Page]:
        # Reserve before a concurrent close can shut down the context.
        async with self._page_lock:
            await self._ensure(headless=headless)
            assert self._context is not None
            try:
                page = await self._context.new_page()
            except BaseException:
                if not self._pages:
                    await self.shutdown()
                raise
            page_id = uuid.uuid4().hex[:12]
            self._pages[page_id] = page
            return page_id, page

    async def open(self, url: str, *, visible: bool | None = None) -> dict[str, Any]:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url 必须是完整的 http:// 或 https:// 地址")
        host = self._check_host_cooldown(url)
        page_id, page = await self._new_page(headless=None if visible is None else not visible)
        try:
            response = await page.goto(url, wait_until="domcontentloaded")
            status = response.status if response is not None else None
            self._status_codes[page_id] = status if type(status) is int else None
            challenge, reason = await self._challenge_info(page, status)
            if challenge:
                self._limited_hosts[host] = time.monotonic() + 300
            info = await self.info(page_id)
            return {**info, "access_limited": challenge,
                    "login_required": status == 401,
                    "http_error": status if type(status) is int and status >= 400 else None,
                    "limit_reason": reason or ("需要登录" if status == 401 else "")}
        except BaseException:
            try:
                await self.close(page_id)
            except Exception:
                pass
            raise

    def _page(self, page_id: str) -> Page:
        page = self._pages.get(str(page_id))
        if page is None or page.is_closed():
            raise ValueError(f"page_id 不存在或页面已关闭：{page_id}")
        return page

    async def info(self, page_id: str) -> dict[str, Any]:
        page = self._page(page_id)
        return {"page_id": page_id, "url": page.url, "title": await page.title(),
                "status_code": self._status_codes.get(page_id)}

    async def submit_search(self, home_url: str, query: str, *, input_selectors: tuple[str, ...],
                            max_chars: int = 16000) -> dict[str, Any]:
        """Use a rendered search form: open, click, type, submit, then read visible results."""
        query = str(query or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        parsed = urlparse(str(home_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("home_url 必须是完整的 http:// 或 https:// 地址")
        host = self._check_host_cooldown(home_url)
        page_id, page = await self._new_page(headless=True)
        try:
            await page.goto(home_url, wait_until="domcontentloaded")
            field = None
            # DuckDuckGo has changed the home-page markup: the visible input
            # may currently be an unnamed plain text input instead of q,
            # searchbox_input, or type=search. Keep the caller's precise
            # selectors first, then use conservative visible fallbacks.
            selectors = (*input_selectors, "input[type='text']", "input:not([type])", "textarea")
            for selector in selectors:
                candidates = page.locator(selector)
                try:
                    count = await candidates.count()
                except Exception:
                    continue
                for index in range(count):
                    candidate = candidates.nth(index)
                    try:
                        if not await candidate.is_visible():
                            continue
                        if (await candidate.get_attribute("aria-hidden")) == "true":
                            continue
                        if (await candidate.get_attribute("tabindex")) == "-1":
                            continue
                        field = candidate
                        break
                    except Exception:
                        continue
                if field is not None:
                    break
            if field is None:
                raise RuntimeError("页面未找到可见搜索输入框")
            await field.click()
            await field.fill("")
            await field.type(query, delay=15)
            # The current DuckDuckGo form is sometimes covered by its search
            # shell while hydration is finishing. Pressing Enter on the real
            # field submits the same visible form without waiting for a
            # covered submit button's actionability timeout.
            try:
                await field.press("Enter")
            except Exception:
                submit = None
                for selector in ("button[type='submit']", "input[type='submit']", "form button"):
                    candidates = page.locator(selector)
                    try:
                        count = await candidates.count()
                    except Exception:
                        continue
                    for index in range(count):
                        candidate = candidates.nth(index)
                        try:
                            if await candidate.is_visible() and (await candidate.get_attribute("aria-hidden")) != "true":
                                submit = candidate
                                break
                        except Exception:
                            continue
                    if submit is not None:
                        break
                if submit is None:
                    raise RuntimeError("页面没有可用的搜索提交控件")
                await submit.click(no_wait_after=True)
            await page.wait_for_timeout(700)
            result = await self.extract(page_id, selector=None, max_chars=max_chars, include_links=True)
            challenge, _ = await self._challenge_info(page, self._status_codes.get(page_id))
            if challenge:
                self._limited_hosts[host] = time.monotonic() + 300
            return result
        finally:
            # Search owns this page. A cleanup failure must reach the tool result.
            await self.close(page_id)

    async def render(self, url: str, *, max_chars: int = 16000) -> dict[str, Any]:
        """Read a URL through the rendered browser and close the temporary page."""
        page_info = await self.open(url, visible=False)
        page_id = str(page_info["page_id"])
        if page_info.get("access_limited") or page_info.get("login_required"):
            return {**page_info, "text": "", "links": [],
                    "note": "页面要求登录或触发访问限制；页面已保留供用户手动处理。"}
        try:
            return await self.extract(page_id, selector=None, max_chars=max_chars, include_links=True)
        finally:
            await self.close(page_id)

    async def pages(self) -> dict[str, Any]:
        items = []
        for page_id in list(self._pages):
            try:
                items.append(await self.info(page_id))
            except Exception:
                self._pages.pop(page_id, None)
        return {"count": len(items), "pages": items}

    async def click(self, page_id: str, *, selector: str | None, text: str | None,
                    x: float | None, y: float | None, button: str, click_count: int) -> dict[str, Any]:
        page = self._page(page_id)
        modes = int(bool(selector)) + int(bool(text)) + int(x is not None or y is not None)
        if modes != 1:
            raise ValueError("selector、text、坐标三种定位方式必须且只能提供一种")
        if (x is None) != (y is None):
            raise ValueError("坐标点击必须同时提供 x 和 y")
        if selector:
            await page.locator(selector).first.click(button=button, click_count=click_count)
        elif text:
            await page.get_by_text(text, exact=False).first.click(button=button, click_count=click_count)
        else:
            await page.mouse.click(float(x), float(y), button=button, click_count=click_count)
        await page.wait_for_timeout(250)
        return await self.info(page_id)

    async def type_text(self, page_id: str, text: str, *, selector: str | None, clear: bool, press_enter: bool) -> dict[str, Any]:
        page = self._page(page_id)
        if selector:
            locator = page.locator(selector).first
            if clear:
                await locator.fill("")
            await locator.type(text)
            if press_enter:
                await locator.press("Enter")
        else:
            await page.keyboard.type(text)
            if press_enter:
                await page.keyboard.press("Enter")
        await page.wait_for_timeout(250)
        return await self.info(page_id)

    async def scroll(self, page_id: str, delta_x: float, delta_y: float) -> dict[str, Any]:
        page = self._page(page_id)
        await page.mouse.wheel(delta_x, delta_y)
        await page.wait_for_timeout(250)
        return await self.info(page_id)

    async def back(self, page_id: str) -> dict[str, Any]:
        page = self._page(page_id)
        await page.go_back(wait_until="domcontentloaded")
        return await self.info(page_id)

    async def extract(self, page_id: str, *, selector: str | None, max_chars: int, include_links: bool) -> dict[str, Any]:
        page = self._page(page_id)
        max_chars = max(500, min(int(max_chars), 50000))
        target = page.locator(selector).first if selector else page.locator("body")
        text = (await target.inner_text()).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[…truncated…]"
        links = []
        if include_links:
            for item in await page.locator("a").evaluate_all(
                "els => els.filter(a => a.getClientRects().length).slice(0, 500).map(a => ({text:(a.innerText||'').trim(), url:a.href||'', context:(a.closest('article')||a.closest('li'))?.innerText?.slice(0,1000)||''}))"
            ):
                if item.get("url"):
                    links.append(item)
        info = await self.info(page_id)
        return {**info, "text": text, "links": links}

    async def screenshot(self, page_id: str, *, filename: str | None, full_page: bool) -> dict[str, Any]:
        page = self._page(page_id)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        if filename:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
            if not safe.lower().endswith(".png"):
                safe += ".png"
        else:
            safe = f"shot_{page_id}_{uuid.uuid4().hex[:8]}.png"
        path = SCREENSHOT_DIR / safe
        await page.screenshot(path=str(path), full_page=bool(full_page))
        size = await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio})")
        return {**(await self.info(page_id)), "path": str(path.resolve()), "full_page": bool(full_page), "viewport": size}

    async def close(self, page_id: str) -> dict[str, Any]:
        async with self._page_lock:
            page = self._pages.get(str(page_id))
            if page is None:
                raise ValueError(f"page_id 不存在：{page_id}")
            try:
                await page.close()
            finally:
                self._pages.pop(str(page_id), None)
                self._status_codes.pop(str(page_id), None)
                if not self._pages:
                    await self.shutdown()
                    if self._shutdown_errors:
                        raise RuntimeError("浏览器资源关闭失败：" + "; ".join(self._shutdown_errors))
            return {"closed": True, "page_id": page_id, "browser_closed": self._browser is None}

    async def shutdown(self) -> None:
        self._pages.clear()
        self._status_codes.clear()
        self._shutdown_errors = []
        resources = []
        if self._owns_context:
            resources.append((self._context, "close"))
        if self._owns_browser:
            resources.append((self._browser, "close"))
        resources.append((self._playwright, "stop"))
        self._context = None
        self._browser = None
        self._playwright = None
        self._owns_context = False
        self._owns_browser = False
        for resource, operation in resources:
            if resource is not None:
                try:
                    await getattr(resource, operation)()
                except Exception as exc:
                    self._shutdown_errors.append(f"{type(resource).__name__}.{operation}: {type(exc).__name__}: {exc}")


browser_manager = BrowserManager()
