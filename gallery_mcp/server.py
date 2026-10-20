"""Minimal Pixiv MCP server, using a Chrome profile owned by this application.

No default Chrome profile access, no process killing, no remote debugging listener.
Only read-only Pixiv AJAX API endpoints and authorized artwork file downloads.
"""

import asyncio
import html
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from mcp.server import MCPServer
from playwright.async_api import Error as PlaywrightError, async_playwright



@asynccontextmanager
async def server_lifespan(_server):
    try:
        yield
    finally:
        await session.close()


mcp = MCPServer("Pixiv Minimal", lifespan=server_lifespan)
PIXIV_ORIGIN = "https://www.pixiv.net"
PROJECT_ROOT = Path(__file__).resolve().parent
# This path is deliberately application-specific. It never points at Chrome's
# normal 'User Data' directory and cannot be replaced by MCP tool arguments.
DATA_ROOT = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share")) / "PixivMCP-Minimal"
CHROME_PROFILE = DATA_ROOT / "chrome-profile"
DOWNLOAD_ROOT = Path(os.environ.get("PIXIV_DOWNLOAD_DIR") or
                     (PROJECT_ROOT / "data" / "downloads" / "pixiv")).expanduser()
WEBSEARCH_CONFIG = PROJECT_ROOT.parent / "WebSearch" / "webmcp_config.json"
JS_GET_JSON = """async ({path}) => {
    let response;
    try {
        response = await fetch(path, {
            method: 'GET',
            credentials: 'same-origin',
            headers: {'Accept': 'application/json'}
        });
    } catch (error) {
        return {status: null, network_error: String(error)};
    }
    let data = null;
    try { data = await response.json(); } catch (_) {}
    return {status: response.status, data: data};
}"""


class PixivError(Exception):
    pass


def _transient_network_failure(error: Exception) -> bool:
    message = str(error)
    return any(marker in message for marker in (
        "ERR_CONNECTION_RESET", "ERR_CONNECTION_TIMED_OUT",
        "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
        "ERR_NETWORK_CHANGED", "ERR_INTERNET_DISCONNECTED",
        "Failed to fetch",
    ))


def pixiv_proxy() -> str:
    """Use an explicit Pixiv proxy, or the sibling WebSearch network route."""
    configured = os.environ.get("PIXIV_PROXY")
    if configured is not None:
        return configured.strip()
    try:
        config = json.loads(WEBSEARCH_CONFIG.read_text(encoding="utf-8"))
        return str(config.get("proxy_url") or "").strip() if isinstance(config, dict) else ""
    except (OSError, ValueError):
        return ""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _fetch_pixiv_image(url: str, proxy: str) -> bytes:
    """Fetch from the already-validated Pixiv CDN without following redirects."""
    if proxy and urlsplit(proxy).scheme not in {"http", "https"}:
        raise PixivError("Pixiv 原图下载代理目前仅支持 HTTP/HTTPS 地址")
    routes = {"http": proxy, "https": proxy} if proxy else {}
    opener = build_opener(ProxyHandler(routes), _NoRedirect())
    request = Request(url, headers={"Referer": PIXIV_ORIGIN + "/"})
    with opener.open(request, timeout=60) as response:
        if response.status != 200:
            raise PixivError(f"图片下载 HTTP {response.status}")
        maximum_bytes = 100 * 1024 * 1024
        declared_length = response.headers.get("content-length")
        if declared_length and int(declared_length) > maximum_bytes:
            raise PixivError("文件超过单文件 100 MiB 限制")
        content_type = response.headers.get("content-type", "").lower()
        if not (content_type.startswith("image/") or
                content_type.startswith("application/zip") or
                content_type.startswith("application/octet-stream")):
            raise PixivError(f"下载响应不是图片或动图 ZIP: {content_type}")
        blob = response.read(maximum_bytes + 1)
        if not blob or len(blob) > maximum_bytes:
            raise PixivError("下载文件为空或超出 100 MiB 限制")
        return blob


def require_id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise PixivError("ID 必须为正整数")
    return value


def plain_text(s: object, max_length: int = 1000) -> str:
    text = re.sub(r"<[^>]+>", " ", str(s or ""))
    return html.unescape(text).strip()[:max_length]


def work_info(item: dict) -> dict:
    # Exact fields from Pixiv AJAX ArtworkInfo (not heuristic key matching).
    keys = ("id", "title", "userId", "userName", "illustType", "pageCount",
            "createDate", "tags", "url", "aiType", "xRestrict")
    result = {key: item.get(key) for key in keys if key in item}
    if "id" in result:
        result["pageUrl"] = f"{PIXIV_ORIGIN}/artworks/{result['id']}"
    return result


class BrowserSession:
    def __init__(self):
        self._start_lock = asyncio.Lock()
        self._api_lock = asyncio.Lock()
        self._playwright = None
        self._context = None
        self._context_closed = False
        self._helper_page = None
        self._login_page = None

    async def close(self) -> dict:
        """Release the app-owned Chrome profile so later MCP runs can use it."""
        async with self._start_lock:
            async with self._api_lock:
                context, playwright = self._context, self._playwright
                self._context = None
                self._context_closed = True
                self._playwright = None
                self._helper_page = None
                self._login_page = None
                errors = []
                if context is not None:
                    try:
                        await context.close()
                    except Exception as exc:
                        errors.append(f"context: {type(exc).__name__}")
                if playwright is not None:
                    try:
                        await playwright.stop()
                    except Exception as exc:
                        errors.append(f"playwright: {type(exc).__name__}")
                if errors:
                    raise PixivError("关闭 Pixiv 专用浏览器失败：" + "; ".join(errors))
                return {"closed": True, "browser_closed": True}

    async def start(self):
        async with self._start_lock:
            if self._context is not None and not self._context_closed:
                return
            if self._context is not None or self._playwright is not None:
                # Chrome can be closed by the user while this long-lived MCP
                # process remains alive. Drop the stale Playwright handles so
                # the next tool call starts a fresh context on the same profile.
                stale_playwright = self._playwright
                self._context = None
                self._context_closed = True
                self._playwright = None
                self._helper_page = None
                self._login_page = None
                if stale_playwright is not None:
                    try:
                        await stale_playwright.stop()
                    except Exception:
                        pass
            CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            options = {
                "user_data_dir": str(CHROME_PROFILE),
                "channel": "chrome",  # Uses installed Google Chrome, not Edge.
                "headless": False,
                "accept_downloads": False,
                "args": ["--no-first-run"],
            }
            proxy = pixiv_proxy()
            if proxy:
                options["proxy"] = {"server": proxy}
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(**options)
            except Exception as exc:
                await self._playwright.stop()
                self._playwright = None
                # No attach, process termination or access to existing profiles.
                raise PixivError(
                    "独立 Chrome 启动失败。请确认已安装 Google Chrome，"
                    "且没有第二个 MCP 进程占用专用 profile。原有 Chrome 不受影响。"
                    f" 详细信息：{exc}"
                ) from exc
            context = self._context
            self._context_closed = False
            context.on("close", lambda *_: self._mark_context_closed(context))
            try:
                self._helper_page = await self._context.new_page()
                await self._helper_page.goto(PIXIV_ORIGIN + "/", wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                # Keep ownership of the browser context so later retry can use it.
                if isinstance(exc, PlaywrightError) and "Target page, context or browser has been closed" in str(exc):
                    self._context_closed = True
                    raise
                raise PixivError(f"专用 Chrome 已打开，但 Pixiv 首页访问失败：{exc}") from exc

    def _mark_context_closed(self, context):
        if self._context is context:
            self._context_closed = True

    async def open_login(self) -> dict:
        await self.start()
        # Opens at most one dedicated login tab; never modifies personal Chrome.
        if self._login_page is None or self._login_page.is_closed():
            self._login_page = await self._context.new_page()
            await self._login_page.goto(PIXIV_ORIGIN + "/", wait_until="domcontentloaded", timeout=30000)
        return {
            "ok": True,
            "message": "请在新打开的 PixivMCP 专用 Chrome 窗口登录 Pixiv。"
                       "登录后无需复制 Cookie，也不要使用个人 Chrome 的 profile。",
            "profile": str(CHROME_PROFILE),
            "url": PIXIV_ORIGIN + "/",
        }

    async def ajax(self, path: str):
        if not path.startswith("/ajax/") or path.startswith("//"):
            raise PixivError("不允许请求非 Pixiv AJAX API 路径")
        for attempt in range(3):
            try:
                await self.start()
                # close() takes these locks in the same order. Hold them for
                # the AJAX operation so it cannot discard the context between
                # start() and new_page()/evaluate().
                async with self._start_lock:
                    if self._context is None or self._context_closed:
                        continue
                    async with self._api_lock:
                        page = self._helper_page
                        if page is None or page.is_closed() or urlsplit(page.url).hostname != "www.pixiv.net":
                            # Do not navigate or close a page the user may now be using.
                            page = await self._context.new_page()
                            await page.goto(PIXIV_ORIGIN + "/", wait_until="domcontentloaded", timeout=30000)
                            self._helper_page = page
                        reply = await page.evaluate(JS_GET_JSON, {"path": path})
                if reply.get("network_error"):
                    if attempt < 2:
                        await asyncio.sleep(0.8 * (attempt + 1))
                        continue
                    raise PixivError(
                        "Pixiv 专用 Chrome 请求 AJAX 接口失败（浏览器未收到 HTTP 响应）。"
                        "请检查专用 Chrome 的 Pixiv 页面是否可访问、登录状态及当前代理设置。"
                        f"浏览器错误：{reply['network_error']}"
                    )
                break
            except PixivError as exc:
                if attempt < 2 and _transient_network_failure(exc):
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                raise
            except PlaywrightError as exc:
                if attempt < 2 and "Target page, context or browser has been closed" in str(exc):
                    # The user closed the dedicated window during this call.
                    self._context_closed = True
                    continue
                if attempt < 2 and _transient_network_failure(exc):
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                raise
        else:
            raise PixivError("Pixiv 专用浏览器已关闭，重建失败")
        if reply["status"] != 200:
            raise PixivError(f"Pixiv API HTTP {reply['status']}，请检查登录状态或网络")
        value = reply.get("data")
        if not isinstance(value, dict):
            raise PixivError("Pixiv 未返回 JSON，可能遇到登录跳转或访问限制")
        if value.get("error") is not False:
            raise PixivError("Pixiv API 返回错误: " + str(value.get("message", "未提供原因")))
        if "body" not in value:
            raise PixivError("Pixiv API 响应缺少文档规定的 body 字段")
        return value["body"]

    async def download_file(self, url: str, filename: Path) -> dict:
        # Never request arbitrary URLs returned by models or compromised pages.
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "i.pximg.net":
            raise PixivError("图片来源并非 Pixiv 图片 CDN，已拒绝下载")
        if parsed.path.endswith("/") or not parsed.path:
            raise PixivError("图片 URL 没有文件名")
        if filename.exists() and filename.stat().st_size > 0:
            return {"file": str(filename), "status": "existing"}
        for attempt in range(3):
            try:
                blob = await asyncio.to_thread(_fetch_pixiv_image, url, pixiv_proxy())
                break
            except HTTPError:
                raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
                await asyncio.sleep(attempt + 1)
        filename.parent.mkdir(parents=True, exist_ok=True)
        staging = filename.with_name(filename.name + ".part")
        staging.write_bytes(blob)
        staging.replace(filename)
        return {"file": str(filename), "status": "downloaded", "bytes": len(blob)}


session = BrowserSession()


@mcp.tool()
async def pixiv_open_login() -> dict:
    """打开专用 Chrome 窗口供用户手动登录 Pixiv，不触碰现有 Chrome。"""
    try:
        return await session.open_login()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def pixiv_session_status() -> dict:
    """检查专用 Chrome 是否具有 Pixiv 会话 Cookie；不返回 Cookie 的值。"""
    try:
        await session.start()
        cookies = await session._context.cookies([PIXIV_ORIGIN])
        present = any(x.get("name") == "PHPSESSID" for x in cookies)
        login_page_open = session._login_page is not None and not session._login_page.is_closed()
        return {"ok": True, "session_cookie_present": present, "login_page_open": login_page_open,
                "note": "仅表明 Cookie 存在，不能保证会话仍有效。"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def pixiv_close_browser() -> dict:
    """关闭 PixivMCP 专用 Chrome 并释放 profile 锁；已登录 Cookie 会保存在专用 profile 中。"""
    try:
        return await session.close()
    except Exception as exc:
        return {"ok": False, "closed": False, "error": str(exc)}


@mcp.tool()
async def pixiv_search(keyword: str, page: int = 1, match: str = "tag_partial",
                       category: str = "artworks", limit: int = 20) -> dict:
    """检索 Pixiv 作品。match: tag_partial/tag_exact/title_caption；category: artworks/illustrations/manga。"""
    try:
        if not keyword.strip() or len(keyword) > 120:
            raise PixivError("搜索词不能为空或超过 120 字符")
        if not 1 <= page <= 1000 or not 1 <= limit <= 60:
            raise PixivError("page 必须为 1~1000，limit 必须为 1~60")
        matches = {"tag_partial": "s_tag", "tag_exact": "s_tag_full", "title_caption": "s_tc"}
        if match not in matches or category not in ("artworks", "illustrations", "manga"):
            raise PixivError("match 或 category 不在文档支持的取值内")
        params = urlencode({"word": keyword, "p": page, "s_mode": matches[match],
                            "order": "date_d", "mode": "all"})
        body = await session.ajax(f"/ajax/search/{category}/{quote(keyword, safe='')}?{params}")
        field = {"artworks": "illustManga", "illustrations": "illust", "manga": "manga"}[category]
        items = body[field]["data"]
        total = body[field]["total"]
        return {"ok": True, "page": page, "total": total,
                "items": [work_info(x) for x in items if isinstance(x, dict) and "id" in x][:limit]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def pixiv_artwork(illust_id: int) -> dict:
    """按作品 ID 查看标题、作者、标签、尺寸、热度和原图地址（只读）。"""
    try:
        illust_id = require_id(illust_id)
        body = await session.ajax(f"/ajax/illust/{illust_id}?full=1")
        tags = body.get("tags", {}).get("tags", [])
        return {"ok": True, "id": body.get("id"), "title": body.get("title"),
                "author": {"id": body.get("userId"), "name": body.get("userName")},
                "description": plain_text(body.get("description")),
                "tags": [x.get("tag") for x in tags if isinstance(x, dict)],
                "illustType": body.get("illustType"), "pageCount": body.get("pageCount"),
                "width": body.get("width"), "height": body.get("height"),
                "bookmarkCount": body.get("bookmarkCount"), "likeCount": body.get("likeCount"),
                "viewCount": body.get("viewCount"), "createDate": body.get("createDate"),
                "urls": body.get("urls"), "pageUrl": f"{PIXIV_ORIGIN}/artworks/{illust_id}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def pixiv_user(user_id: int) -> dict:
    """按用户 ID 获取 Pixiv 作者资料（只读）。"""
    try:
        user_id = require_id(user_id)
        body = await session.ajax(f"/ajax/user/{user_id}?full=1")
        return {"ok": True, "userId": body.get("userId"), "name": body.get("name"),
                "comment": plain_text(body.get("comment")), "image": body.get("image"),
                "following": body.get("following"), "region": body.get("region"),
                "pageUrl": f"{PIXIV_ORIGIN}/users/{user_id}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def pixiv_user_works(user_id: int, offset: int = 0, limit: int = 30) -> dict:
    """返回作者作品 ID 列表的一段；返回顺序由 Pixiv API 决定，不保证按时间排序。"""
    try:
        user_id = require_id(user_id)
        if offset < 0 or not 1 <= limit <= 60:
            raise PixivError("offset 必须 >= 0，limit 必须为 1~60")
        body = await session.ajax(f"/ajax/user/{user_id}/profile/all")
        illusts = body["illusts"]
        manga = body["manga"]
        if not isinstance(illusts, dict) or not isinstance(manga, dict):
            raise PixivError("用户作品响应结构与公开文档不符")
        all_works = ([{"id": k, "category": "illust"} for k in illusts] +
                     [{"id": k, "category": "manga"} for k in manga])
        return {"ok": True, "userId": user_id, "total": len(all_works),
                "offset": offset, "items": all_works[offset:offset + limit],
                "note": "Pixiv 接口不保证作品 ID 列表的排序"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def pixiv_download(illust_id: int, max_pages: int = 10) -> dict:
    """下载作品原图（最多 30 页），或 Ugoira 原始 ZIP 和帧延迟表，不覆盖已有文件。"""
    try:
        illust_id = require_id(illust_id)
        if not 1 <= max_pages <= 30:
            raise PixivError("max_pages 必须为 1~30")
        detail = await session.ajax(f"/ajax/illust/{illust_id}?full=1")
        out_dir = DOWNLOAD_ROOT / str(illust_id) / "original"
        urls = []
        metadata = None
        if detail.get("illustType") == 2:
            metadata = await session.ajax(f"/ajax/illust/{illust_id}/ugoira_meta")
            url = metadata["originalSrc"]
            urls.append((url, out_dir / f"{illust_id}_ugoira.zip"))
        else:
            pages = await session.ajax(f"/ajax/illust/{illust_id}/pages")
            if not isinstance(pages, list):
                raise PixivError("作品页数组结构异常")
            for index, item in enumerate(pages[:max_pages]):
                url = item["urls"]["original"]
                suffix = Path(urlsplit(url).path).suffix.lower()
                if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"):
                    raise PixivError(f"下载地址返回无法确认的文件类型: {suffix}")
                urls.append((url, out_dir / f"{illust_id}_p{index}{suffix}"))
        if not urls:
            raise PixivError("Pixiv 没有返回可下载的作品页")
        results = []
        for url, target in urls:
            try:
                results.append(await session.download_file(url, target))
            except Exception as exc:
                results.append({"file": str(target), "status": "error",
                                "error": str(exc).splitlines()[0]})
            await asyncio.sleep(0.3)
        if metadata and any(x["status"] in ("downloaded", "existing") for x in results):
            out_dir.mkdir(parents=True, exist_ok=True)
            frames_path = out_dir / f"{illust_id}_ugoira_frames.json"
            if not frames_path.exists():
                frames_path.write_text(json.dumps(metadata.get("frames"), ensure_ascii=False, indent=2), encoding="utf-8")
            results.append({"file": str(frames_path), "status": "metadata"})
        return {"ok": all(item["status"] != "error" for item in results),
                "id": illust_id, "directory": str(out_dir), "results": results,
                "note": "Ugoira 保存为 ZIP + 帧延迟 JSON，未自动转码。" if metadata else "保存 Pixiv 返回的原图。"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    # MCP stdio: never print banner/debug output to stdout.
    mcp.run(transport="stdio")
