"""Isolated JMComic MCP provider. No site request occurs during import/discovery."""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jmcomic
from mcp.server import MCPServer
from PIL import Image, ImageOps


mcp = MCPServer("JMComic")
jmcomic.disable_jm_log()
ROOT = Path(__file__).resolve().parent
DATA_ROOT = (ROOT / "data").resolve()
DOWNLOAD_ROOT = DATA_ROOT / "downloads"
COVER_ROOT = DATA_ROOT / "covers"
MAX_IMAGES = 300
MAX_BYTES = 2 * 1024 * 1024 * 1024
WEBSEARCH_CONFIG = ROOT.parent / "WebSearch" / "webmcp_config.json"
_ID = re.compile(r"(?:JM\s*)?(\d{1,12})\Z", re.I)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _album_id(value: str) -> str:
    match = _ID.fullmatch(str(value).strip())
    if not match:
        raise ValueError("album_id must be a JM number")
    return match.group(1)


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _safe_error(exc: Exception) -> dict[str, Any]:
    description = str(exc).splitlines()[0][:180]
    lowered = description.casefold()
    category = ("access_restricted" if any(word in lowered for word in ("403", "429", "captcha", "验证码", "cloudflare"))
                else "authentication" if any(word in lowered for word in ("login", "cookie", "登录"))
                else "network" if any(word in lowered for word in ("timeout", "connection", "network", "连接", "超时"))
                else "upstream")
    message = {
        "access_restricted": "site rejected or limited access",
        "authentication": "authentication failed",
        "network": "network request failed",
        "upstream": "upstream operation failed",
    }[category]
    return {"ok": False, "provider": "JMComic", "error": {"category": category, "message": message}}


def _online_disabled() -> dict[str, Any] | None:
    if os.environ.get("JMCOMIC_ONLINE_ENABLED") == "1":
        return None
    return {"ok": False, "provider": "JMComic", "error": {
        "category": "online_disabled", "message": "JMComic online access is awaiting explicit authorization"}}


def _proxy() -> str | None:
    """Explicit override, then the shared local network route; None uses system settings."""
    configured = os.environ.get("JMCOMIC_PROXY")
    if configured is not None:
        return configured.strip()
    try:
        config = json.loads(WEBSEARCH_CONFIG.read_text(encoding="utf-8"))
        if isinstance(config, dict):
            return str(config.get("proxy_url") or "").strip()
    except (OSError, ValueError):
        pass
    return None


def _option():
    config = os.environ.get("JMCOMIC_CONFIG_FILE", "").strip()
    if config:
        source = Path(config).expanduser().resolve(strict=True)
        option = jmcomic.create_option_by_file(str(source))
    else:
        option = jmcomic.JmOption.default()
    jmcomic.disable_jm_log()
    # Never let an optional local option file redirect writes outside this service.
    option.dir_rule = jmcomic.DirRule("Bd / Aid / Pindex", base_dir=str(DOWNLOAD_ROOT))
    proxy = _proxy()
    if proxy is not None:
        if proxy and (urlsplit(proxy).scheme not in {"http", "https"} or not urlsplit(proxy).hostname):
            raise ValueError("JMCOMIC_PROXY must be an HTTP or HTTPS proxy URL")
        option.client.postman.meta_data.proxies = {"http": proxy, "https": proxy} if proxy else {}
    option.client.retry_times = 2
    option.download.threading.image = 2
    option.download.threading.photo = 1
    return option


def _client():
    return _option().new_jm_client()


def _quiet(function, *args, **kwargs):
    return function(*args, **kwargs)


def _search(query: str, page: int, limit: int) -> dict[str, Any]:
    if not query.strip() or not 1 <= page <= 100 or not 1 <= limit <= 30:
        raise ValueError("query, page or limit is outside the allowed range")
    response = _quiet(_client().search_site, search_query=query.strip(), page=page)
    items = [{"album_id": str(album_id), "title": str(title), "source": "JMComic search_site"}
             for album_id, title in response][:limit]
    total = getattr(response, "total", None)
    return {"ok": True, "provider": "JMComic", "items": items, "page": page,
            "total": total if type(total) is int and total >= 0 else None}


def _detail(album_id: str) -> dict[str, Any]:
    album = _quiet(_client().get_album_detail, album_id)
    chapters = []
    for photo in album:
        chapters.append({"photo_id": str(getattr(photo, "photo_id", getattr(photo, "id", ""))),
                         "title": str(getattr(photo, "title", ""))})
    return {"ok": True, "provider": "JMComic", "album_id": str(getattr(album, "id", album_id)),
            "title": str(getattr(album, "title", "")), "chapters": chapters,
            "tags": list(getattr(album, "tags", []) or [])}


def _cover(album_id: str) -> dict[str, Any]:
    COVER_ROOT.mkdir(parents=True, exist_ok=True)
    target = COVER_ROOT / f"{album_id}.png"
    _quiet(_client().download_album_cover, album_id, str(target))
    if not _inside(target, COVER_ROOT) or not target.is_file() or target.stat().st_size == 0:
        raise ValueError("cover was not saved")
    return {"ok": True, "provider": "JMComic", "album_id": album_id,
            "artifact": {"path": str(target), "size_bytes": target.stat().st_size}}


def _pdf_filename(title: str, album_id: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]', "＿", title).strip(" .")[:160].rstrip(" .")
    if not safe or safe.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                    *(f"LPT{i}" for i in range(1, 10))}:
        safe = f"JM{album_id}"
    return safe + ".pdf"


def _ordered_images(files: list[str], directory: Path) -> list[Path]:
    def part_key(part: str):
        return tuple((0, int(token)) if token.isdecimal() else (1, token.casefold())
                     for token in re.split(r"(\d+)", part) if token)

    return sorted((Path(raw) for raw in files),
                  key=lambda path: (tuple(part_key(part) for part in path.relative_to(directory).parts), str(path)))


def _create_pdf(files: list[str], directory: Path, title: str, album_id: str) -> dict[str, Any]:
    """Write one JPEG-backed PDF page at a time to keep memory bounded."""
    ordered = _ordered_images(files, directory)
    target = directory / _pdf_filename(title, album_id)
    if not _inside(target, directory):
        raise ValueError("PDF path escaped the album directory")
    fd, staging = tempfile.mkstemp(prefix="jmcomic-pdf-", suffix=".part", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output:
            offsets = [0] * (4 + 3 * len(ordered))

            def begin_object(number: int):
                offsets[number] = output.tell()
                output.write(f"{number} 0 obj\n".encode("ascii"))

            def end_object():
                output.write(b"\nendobj\n")

            output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            begin_object(1)
            output.write(b"<< /Type /Catalog /Pages 2 0 R >>")
            end_object()
            begin_object(2)
            kids = " ".join(f"{3 + 3 * i} 0 R" for i in range(len(ordered)))
            output.write(f"<< /Type /Pages /Kids [{kids}] /Count {len(ordered)} >>".encode("ascii"))
            end_object()

            for index, path in enumerate(ordered):
                with Image.open(path) as opened:
                    if opened.width * opened.height > 100_000_000:
                        raise ValueError("image pixel count exceeds the PDF limit")
                    oriented = ImageOps.exif_transpose(opened)
                    if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                        rgba = oriented.convert("RGBA")
                        image = Image.new("RGB", rgba.size, "white")
                        image.paste(rgba, mask=rgba.getchannel("A"))
                    else:
                        image = oriented.convert("RGB")
                    width, height = image.size
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=92)
                    jpeg = buffer.getvalue()
                    image.close()
                page_id = 3 + 3 * index
                content_id = page_id + 1
                image_id = page_id + 2
                width_pt, height_pt = width * 0.72, height * 0.72
                begin_object(page_id)
                output.write((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width_pt:.2f} {height_pt:.2f}] "
                              f"/Resources << /XObject << /Im0 {image_id} 0 R >> >> "
                              f"/Contents {content_id} 0 R >>").encode("ascii"))
                end_object()
                content = f"q\n{width_pt:.2f} 0 0 {height_pt:.2f} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
                begin_object(content_id)
                output.write(f"<< /Length {len(content)} >>\nstream\n".encode("ascii"))
                output.write(content)
                output.write(b"endstream")
                end_object()
                begin_object(image_id)
                output.write((f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                              f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                              f"/Length {len(jpeg)} >>\nstream\n").encode("ascii"))
                output.write(jpeg)
                output.write(b"\nendstream")
                end_object()

            info_id = 3 + 3 * len(ordered)
            begin_object(info_id)
            title_hex = (b"\xfe\xff" + (title or f"JM{album_id}").encode("utf-16-be")).hex().upper()
            output.write(f"<< /Title <{title_hex}> /Producer (JMComic MCP) >>".encode("ascii"))
            end_object()
            xref = output.tell()
            output.write(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
            for offset in offsets[1:]:
                output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
            output.write((f"trailer\n<< /Size {len(offsets)} /Root 1 0 R /Info {info_id} 0 R >>\n"
                          f"startxref\n{xref}\n%%EOF\n").encode("ascii"))
        os.replace(staging, target)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)
    return {"path": str(target), "pages": len(ordered), "size_bytes": target.stat().st_size}


def _download(album_id: str) -> dict[str, Any]:
    import jmcomic

    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    album = _quiet(_client().get_album_detail, album_id)
    page_count = getattr(album, "page_count", None)
    if type(page_count) is not int or not 1 <= page_count <= MAX_IMAGES:
        raise ValueError("album image count is unavailable or exceeds the limit")
    existing = DOWNLOAD_ROOT / album_id
    if existing.is_dir() and sum(path.stat().st_size for path in existing.rglob("*")
                                 if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS) > MAX_BYTES:
        raise ValueError("existing album files exceed the byte limit")
    for attempt in range(3):
        try:
            result = _quiet(jmcomic.download_album, album_id, _option(), check_exception=True)
            break
        except jmcomic.PartialDownloadFailedException as exc:
            downloader = exc.downloader
            failed_images = [str(getattr(item, "filename", getattr(item, "id", "unknown")))
                             for item, _ in downloader.download_failed_image]
            failed_chapters = [str(getattr(item, "photo_id", getattr(item, "id", "unknown")))
                               for item, _ in downloader.download_failed_photo]
            failures = [error for _, error in
                        [*downloader.download_failed_image, *downloader.download_failed_photo]]
            restricted = any(_safe_error(error)["error"]["category"] in {"access_restricted", "authentication"}
                             for error in failures)
            if attempt < 2 and not restricted:
                # The downloader caches completed files, so a second pass resumes missing images.
                time.sleep(attempt + 1)
                continue
            directory = DOWNLOAD_ROOT / album_id
            verified = [path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in
                        _IMAGE_EXTENSIONS and path.stat().st_size > 0] if directory.is_dir() else []
            return {"ok": False, "provider": "JMComic", "album_id": album_id, "complete": False,
                    "expected_images": page_count, "verified_images": len(verified),
                    "directory": str(directory) if directory.is_dir() else None,
                    "failed_images": failed_images, "failed_chapters": failed_chapters,
                    "error": {"category": "partial_download", "message": "some images or chapters failed after retry"}}
    detail = result.detail
    directory = Path(detail.save_path).resolve()
    if not _inside(directory, DOWNLOAD_ROOT) or not directory.is_dir():
        raise ValueError("download directory escaped the authorized root or is missing")
    expected = list(result.manifest.image_filepath_list)
    failures = []
    verified = []
    total_size = 0
    for raw in expected:
        path = Path(raw).resolve()
        if not _inside(path, directory) or not path.is_file() or path.stat().st_size <= 0:
            failures.append(str(raw))
            continue
        total_size += path.stat().st_size
        verified.append(str(path))
    images_complete = (bool(expected) and not failures and len(expected) == page_count
                       and len(expected) <= MAX_IMAGES and total_size <= MAX_BYTES)
    manifest = directory / "jmcomic-manifest.json"
    title = str(getattr(album, "title", "") or "").strip()
    receipt = {"ok": images_complete, "provider": "JMComic", "album_id": album_id,
               "title": title, "complete": images_complete, "images_complete": images_complete,
               "expected_images": page_count, "verified_images": len(verified),
               "directory": str(directory), "manifest": str(manifest), "files": verified,
               "failed_files": failures, "pdf": None}
    if images_complete:
        try:
            receipt["pdf"] = _create_pdf(verified, directory, title, album_id)
        except Exception as exc:
            receipt.update(ok=False, complete=False)
            receipt["error"] = {"category": "pdf_generation", "message": f"PDF generation failed: {type(exc).__name__}"}
    else:
        receipt.update(ok=False, complete=False)
        receipt["error"] = {"category": "partial_download", "message": "image receipt did not verify or exceeded limits"}
    manifest.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


@mcp.tool()
async def jmcomic_search(query: str | int, page: int = 1, limit: int = 10) -> dict[str, Any]:
    if (blocked := _online_disabled()) is not None:
        return blocked
    try:
        text = str(query).strip()
        if text.isdecimal():
            if not 1 <= page <= 100 or not 1 <= limit <= 30:
                raise ValueError("page or limit is outside the allowed range")
            if page > 1:
                return {"ok": True, "provider": "JMComic", "items": [], "page": page, "total": 1}
            album = await asyncio.to_thread(_detail, _album_id(text))
            return {"ok": True, "provider": "JMComic", "items": [{
                "album_id": album["album_id"], "title": album["title"], "source": "JMComic get_album_detail"}],
                "page": page, "total": 1}
        return await asyncio.to_thread(_search, text, page, limit)
    except Exception as exc:
        return _safe_error(exc)


@mcp.tool()
async def jmcomic_album_detail(album_id: str | int) -> dict[str, Any]:
    if (blocked := _online_disabled()) is not None:
        return blocked
    try:
        return await asyncio.to_thread(_detail, _album_id(album_id))
    except Exception as exc:
        return _safe_error(exc)


@mcp.tool()
async def jmcomic_cover(album_id: str | int) -> dict[str, Any]:
    if (blocked := _online_disabled()) is not None:
        return blocked
    try:
        return await asyncio.to_thread(_cover, _album_id(album_id))
    except Exception as exc:
        return _safe_error(exc)


@mcp.tool()
async def jmcomic_download_album(album_id: str | int) -> dict[str, Any]:
    if (blocked := _online_disabled()) is not None:
        return {**blocked, "complete": False}
    try:
        return await asyncio.to_thread(_download, _album_id(album_id))
    except Exception as exc:
        return {**_safe_error(exc), "complete": False}


if __name__ == "__main__":
    mcp.run(transport="stdio")
