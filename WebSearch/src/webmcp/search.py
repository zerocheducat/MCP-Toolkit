from __future__ import annotations

import base64
from urllib.parse import urlparse, parse_qs

from .browser import browser_manager
from .config import load_config


def _is_pixiv_query(query: str) -> bool:
    lowered = query.casefold()
    return "pixiv" in lowered or "ピクシブ" in query


def _is_official_pixiv_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "pixiv.net" or host.endswith(".pixiv.net")


def _is_search_result_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and bool(host)
        and host not in {"duckduckgo.com", "bing.com"}
        and not host.endswith((".duckduckgo.com", ".bing.com"))
    )


def _decode_search_redirect(url: str) -> str:
    """Turn rendered search-engine redirect links into their real targets."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"duckduckgo.com", "www.duckduckgo.com"} and parsed.path.rstrip("/") == "/l":
        return parse_qs(parsed.query).get("uddg", [""])[0] or url
    if host in {"bing.com", "www.bing.com"} and parsed.path.rstrip("/") == "/ck/a":
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                payload = encoded[2:]
                payload += "=" * ((4 - len(payload) % 4) % 4)
                target = base64.urlsafe_b64decode(payload).decode("utf-8")
                if urlparse(target).scheme in {"http", "https"}:
                    return target
            except (ValueError, UnicodeError):
                pass
    return url


def _blocked_search_page(text: str) -> bool:
    lowered = str(text or "").casefold()
    return any(marker in lowered for marker in (
        "很遗憾，机器人也使用 duckduckgo",
        "选择所有包含鸭子的正方形",
        "captcha",
        "verify you are human",
        "unusual traffic",
    ))


def _collect_results(rendered: dict, *, pixiv_official_only: bool, limit: int) -> list[dict]:
    results = []
    seen: set[str] = set()
    for link in rendered.get("links") or []:
        href = _decode_search_redirect(str(link.get("url") or "").strip())
        title = str(link.get("text") or "").strip()
        if not title or not href or href in seen or not _is_search_result_url(href):
            continue
        if pixiv_official_only and not _is_official_pixiv_url(href):
            continue
        seen.add(href)
        results.append({
            "title": title,
            "url": href,
            "snippet": str(link.get("context") or "")[:1000],
        })
        if len(results) >= limit:
            break
    return results


async def search_web(query: str, limit: int = 8) -> dict:
    """Search through rendered browser pages, with a visible-form fallback."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    config = load_config()
    limit = max(1, min(int(limit), int(config["max_search_results"])))
    pixiv_official_only = _is_pixiv_query(query)
    effective_query = query
    if pixiv_official_only and "site:pixiv.net" not in query.casefold():
        effective_query = f"site:pixiv.net {query}"

    rendered = None
    provider = "duckduckgo_rendered_browser"
    first_error = None
    try:
        rendered = await browser_manager.submit_search(
            "https://duckduckgo.com/",
            effective_query,
            input_selectors=("input[name='q']", "input#searchbox_input", "input[type='search']"),
            max_chars=30000,
        )
        results = _collect_results(rendered, pixiv_official_only=pixiv_official_only, limit=limit)
    except Exception as exc:
        first_error = exc
        results = []

    # DuckDuckGo can return a rendered CAPTCHA page to a real browser session.
    # Use another visible search form rather than retrying the blocked page.
    if not results:
        try:
            bing_rendered = await browser_manager.submit_search(
                "https://www.bing.com/",
                effective_query,
                input_selectors=("textarea[name='q']", "input[name='q']", "input[type='search']"),
                max_chars=30000,
            )
            bing_results = _collect_results(
                bing_rendered,
                pixiv_official_only=pixiv_official_only,
                limit=limit,
            )
            if bing_results or rendered is None or _blocked_search_page(rendered.get("text")):
                rendered, results, provider = bing_rendered, bing_results, "bing_rendered_browser"
        except Exception:
            if rendered is None and first_error is not None:
                raise first_error
    if rendered is None:
        raise RuntimeError("浏览器搜索没有返回页面结果。")
    return {
        "query": query,
        "effective_query": effective_query,
        "provider": provider,
        "source_policy": "pixiv_official_only" if pixiv_official_only else "general",
        "count": len(results),
        "results": results,
        "page_title": rendered.get("title") or "",
        "page_url": rendered.get("url") or "",
        "access_mode": "rendered_browser_ui",
        "visible_text": str(rendered.get("text") or "")[:8000],
        "note": (
            "Pixiv query: only official pixiv.net results are returned. Results are read from the visible page."
            if pixiv_official_only
            else "Results are read from the visible rendered search page."
        ),
    }


async def fetch_web(url: str, max_chars: int = 16000) -> dict:
    """Open and read one exact URL through the rendered browser."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url 必须是完整的 http:// 或 https:// 地址")
    max_chars = max(500, min(int(max_chars), 50000))
    rendered = await browser_manager.render(str(url), max_chars=max_chars)
    return {
        "url": rendered.get("url") or str(url),
        "status_code": rendered.get("status_code"),
        "content_type": "text/html; rendered=playwright",
        "title": rendered.get("title") or "",
        "text": rendered.get("text") or "",
        "links": rendered.get("links") or [],
        "fetch_mode": "browser_ui",
        "access_mode": "rendered_browser_ui",
        "note": "内容来自浏览器渲染页面的可见文本与链接。",
    }
