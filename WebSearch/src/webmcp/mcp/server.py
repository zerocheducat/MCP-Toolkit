from __future__ import annotations

import asyncio
from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from webmcp.browser import browser_manager, browser_runtime_info
from webmcp.clash import status as clash_status
from webmcp.search import fetch_web, search_web

INSTRUCTIONS = """WebMCP operates rendered Playwright pages. web_search and web_fetch close their temporary pages after extracting results; closing the last owned page also closes the browser process. web_search opens DuckDuckGo, clicks its visible search input, types the query, submits the page, and reads rendered links. web_fetch opens and reads the exact URL through the browser. A 403/429 or detected human-verification challenge is returned as access_limited and that hostname is cooled down for five minutes; the challenge page stays open for manual handling, while unrelated hostnames remain usable. Do not repeatedly reopen or vary URLs on a limited hostname, and never bypass CAPTCHA, paywalls, login requirements, or browser warnings. Pixiv queries return official pixiv.net results only. Never invent selectors, page IDs, coordinates, URLs, or tool arguments. A successful browser_screenshot completes a screenshot request; Aicy will deliver the PNG directly, so stop calling browser tools after success."""

mcp = MCPServer("Aicy Web MCP", version="0.1.2", instructions=INSTRUCTIONS, log_level="WARNING")


def tool_error(exc: Exception) -> ToolError:
    return ToolError(f"{type(exc).__name__}: {exc}")


@mcp.tool()
async def web_status() -> dict:
    """Check the Clash route and report which browser executable WebMCP will use."""
    result = await clash_status(force=True)
    return {**result, "browser": browser_runtime_info()}


@mcp.tool()
async def web_search(query: str, limit: int = 8) -> dict:
    """Discover public web pages by operating the rendered search page in the browser."""
    try:
        status = await clash_status()
        if not status.get("ok"):
            raise RuntimeError(status.get("message") or "Clash proxy is unavailable")
        return await search_web(query, limit)
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def web_fetch(url: str, max_chars: int = 16000) -> dict:
    """Read one exact URL through the rendered browser page and return visible text and links."""
    try:
        status = await clash_status()
        if not status.get("ok"):
            raise RuntimeError(status.get("message") or "Clash proxy is unavailable")
        return await fetch_web(url, max_chars)
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_open(url: str, visible: bool = False) -> dict:
    """Open one exact URL; set visible=true only for user-directed login or manual browser interaction."""
    try:
        return await browser_manager.open(url, visible=bool(visible))
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_pages() -> dict:
    """List currently open persistent browser pages and their exact page_id values."""
    return await browser_manager.pages()


@mcp.tool()
async def browser_click(page_id: str, selector: str | None = None, text: str | None = None,
                        x: float | None = None, y: float | None = None,
                        button: Literal["left", "right", "middle"] = "left", click_count: int = 1) -> dict:
    """Click one page target. Provide exactly one targeting mode: CSS selector, visible text, or both x/y viewport coordinates."""
    try:
        return await browser_manager.click(page_id, selector=selector, text=text, x=x, y=y,
                                           button=button, click_count=max(1, min(int(click_count), 3)))
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_type(page_id: str, text: str, selector: str | None = None,
                       clear: bool = True, press_enter: bool = False) -> dict:
    """Type text into an exact selector, or into the currently focused element when selector is omitted."""
    try:
        return await browser_manager.type_text(page_id, text, selector=selector, clear=clear, press_enter=press_enter)
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_scroll(page_id: str, delta_y: float = 720, delta_x: float = 0) -> dict:
    """Scroll a persistent page by exact pixel deltas."""
    try:
        return await browser_manager.scroll(page_id, float(delta_x), float(delta_y))
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_back(page_id: str) -> dict:
    """Navigate one persistent page back once."""
    try:
        return await browser_manager.back(page_id)
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_extract(page_id: str, selector: str | None = None, max_chars: int = 12000,
                          include_links: bool = True) -> dict:
    """Extract visible text from the whole page or one exact CSS selector, optionally including exact links."""
    try:
        return await browser_manager.extract(page_id, selector=selector, max_chars=max_chars, include_links=include_links)
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_screenshot(page_id: str, filename: str | None = None, full_page: bool = False) -> dict:
    """Save a PNG screenshot and return its exact local path. A successful result completes a screenshot request; do not fetch the same page again unless the user asks for more research."""
    try:
        return await browser_manager.screenshot(page_id, filename=filename, full_page=full_page)
    except Exception as exc:
        raise tool_error(exc) from exc


@mcp.tool()
async def browser_close(page_id: str) -> dict:
    """Close one persistent browser page by exact page_id."""
    try:
        return await browser_manager.close(page_id)
    except Exception as exc:
        raise tool_error(exc) from exc


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
