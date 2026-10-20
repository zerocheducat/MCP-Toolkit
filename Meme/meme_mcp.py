from __future__ import annotations

import json
import os
import random
import re
import threading
from collections import deque
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import TextContent

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(
    os.environ.get("AICY_MEME_CONFIG", str(APP_DIR / "meme_config.json"))
).expanduser().resolve()

mcp = MCPServer(
    "AICY Meme MCP",
    instructions=(
        "Provides local reaction images from a configured folder. "
        "For casual, non-sensitive conversation, the host/model may call "
        "maybe_send_meme. The returned metadata field 'action' is one of "
        "'none', 'append', or 'image_only'."
    ),
)

_LOCK = threading.RLock()
_RECENT: deque[str] = deque()
_ELIGIBLE_CALLS_SINCE_MEME = 10**9


def _load_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(
            f"Config file does not exist: {CONFIG_PATH}. "
            "Copy meme_config.example.json to meme_config.json and set meme_dir."
        )

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    if not isinstance(cfg, dict):
        raise RuntimeError("meme_config.json root must be a JSON object.")

    raw_dir = cfg.get("meme_dir")
    if not isinstance(raw_dir, str) or not raw_dir.strip():
        raise RuntimeError(
            "meme_config.json field 'meme_dir' must be a non-empty string."
        )

    meme_dir = Path(raw_dir).expanduser()
    if not meme_dir.is_absolute():
        meme_dir = (CONFIG_PATH.parent / meme_dir).resolve()
    else:
        meme_dir = meme_dir.resolve()

    if not meme_dir.is_dir():
        raise RuntimeError(f"Configured meme_dir is not a directory: {meme_dir}")

    supported = cfg.get(
        "extensions", [".png", ".jpg", ".jpeg", ".webp", ".gif"]
    )
    if not isinstance(supported, list) or not all(
        isinstance(x, str) and x.startswith(".") for x in supported
    ):
        raise RuntimeError(
            "meme_config.json field 'extensions' must be an array like "
            '[".png", ".jpg", ".jpeg", ".webp", ".gif"].'
        )

    cfg["_meme_dir_resolved"] = meme_dir
    cfg["_extensions_normalized"] = {x.lower() for x in supported}

    aliases = cfg.get("tag_aliases", {})
    if not isinstance(aliases, dict):
        raise RuntimeError("meme_config.json field 'tag_aliases' must be an object.")
    normalized_aliases: dict[str, set[str]] = {}
    for raw_key, raw_values in aliases.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise RuntimeError("tag_aliases keys must be non-empty strings.")
        if not isinstance(raw_values, list) or not all(isinstance(x, str) and x.strip() for x in raw_values):
            raise RuntimeError("tag_aliases values must be arrays of non-empty strings.")
        normalized_aliases[raw_key.strip().lower()] = {
            token
            for value in raw_values
            for token in _tokenize(value)
        }
    cfg["_tag_aliases_normalized"] = normalized_aliases

    for key in ("append_probability", "image_only_probability"):
        value = cfg.get(key, 0.0)
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
            raise RuntimeError(f"{key} must be a number in [0, 1].")

    total = float(cfg.get("append_probability", 0.0)) + float(
        cfg.get("image_only_probability", 0.0)
    )
    if total > 1.0:
        raise RuntimeError(
            "append_probability + image_only_probability must be <= 1.0."
        )

    for key in ("cooldown_eligible_calls", "recent_history_size"):
        value = cfg.get(key, 0)
        if not isinstance(value, int) or value < 0:
            raise RuntimeError(f"{key} must be a non-negative integer.")

    return cfg


def _tokenize(text: str) -> set[str]:
    # Chinese/Japanese strings are kept as contiguous chunks; Latin text is
    # lower-cased and split on separators. This is only local filename/folder
    # matching; it is not semantic inference.
    return {
        token.lower()
        for token in re.findall(r"[\w\u3400-\u9fff\u3040-\u30ff]+", text, re.UNICODE)
        if token
    }


def _scan_memes(cfg: dict) -> list[dict]:
    root: Path = cfg["_meme_dir_resolved"]
    extensions: set[str] = cfg["_extensions_normalized"]
    recursive = bool(cfg.get("recursive", True))

    iterator = root.rglob("*") if recursive else root.glob("*")
    items: list[dict] = []

    for path in iterator:
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue

        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            # Do not expose anything that resolves outside meme_dir.
            continue

        tags: set[str] = set()
        for part in relative.parts[:-1]:
            tags.update(_tokenize(part))
        stem_tokens = _tokenize(relative.stem)
        tags.update(stem_tokens)
        aliases: dict[str, set[str]] = cfg.get("_tag_aliases_normalized", {})
        for stem_token in stem_tokens:
            tags.update(aliases.get(stem_token, set()))

        items.append(
            {
                "path": resolved,
                "relative": relative.as_posix(),
                "tags": tags,
            }
        )

    return items


def _choose_item(items: list[dict], query: str, recent_size: int) -> dict:
    global _RECENT

    if not items:
        raise RuntimeError("No supported image files were found in meme_dir.")

    query_tokens = _tokenize(query)

    if query_tokens:
        scored: list[tuple[int, dict]] = []
        for item in items:
            score = len(query_tokens & item["tags"])
            if score > 0:
                scored.append((score, item))
        if scored:
            best_score = max(score for score, _ in scored)
            pool = [item for score, item in scored if score == best_score]
        else:
            pool = items[:]
    else:
        pool = items[:]

    recent = set(_RECENT)
    not_recent = [item for item in pool if item["relative"] not in recent]
    if not_recent:
        pool = not_recent

    selected = random.SystemRandom().choice(pool)

    if recent_size > 0:
        if _RECENT.maxlen != recent_size:
            _RECENT = deque(_RECENT, maxlen=recent_size)
        _RECENT.append(selected["relative"])
    else:
        _RECENT.clear()

    return selected


def _meta_text(action: str, item: dict | None = None) -> TextContent:
    payload = {
        "type": "aicy_meme",
        "action": action,
        "terminal": action == "image_only",
    }
    if item is not None:
        payload["relative_path"] = item["relative"]

    return TextContent(
        type="text",
        text="AICY_MEME_META:" + json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ),
    )


@mcp.tool()
def list_meme_library() -> dict[str, Any]:
    """Return a safe summary of the configured local meme library.

    This never returns absolute paths. It reports the image count, known
    filename/folder tags, supported extensions, and the configured behavior.
    """
    cfg = _load_config()
    items = _scan_memes(cfg)

    tags = sorted({tag for item in items for tag in item["tags"]})
    return {
        "count": len(items),
        "tags": tags,
        "extensions": sorted(cfg["_extensions_normalized"]),
        "recursive": bool(cfg.get("recursive", True)),
        "append_probability": float(cfg.get("append_probability", 0.0)),
        "image_only_probability": float(
            cfg.get("image_only_probability", 0.0)
        ),
        "cooldown_eligible_calls": int(cfg.get("cooldown_eligible_calls", 0)),
        "recent_history_size": int(cfg.get("recent_history_size", 0)),
        "tag_aliases": {
            key: sorted(values)
            for key, values in sorted(cfg.get("_tag_aliases_normalized", {}).items())
        },
    }


@mcp.tool()
def get_meme(
    query: str = "",
    mode: Literal["append", "image_only"] = "append",
) -> list[TextContent | Image]:
    """Select and return one local meme image.

    query:
        Optional literal filename/folder-tag hint such as "开心", "无语",
        "laugh", or "cat". Matching uses only the configured local path names.
    mode:
        "append" means the host may display the image after normal assistant
        text. "image_only" means the host should treat this tool result as the
        complete assistant turn and not request additional assistant text.

    The first returned content block is AICY_MEME_META JSON and the second
    block is the actual MCP image content.
    """
    cfg = _load_config()
    items = _scan_memes(cfg)

    with _LOCK:
        item = _choose_item(
            items,
            query=query,
            recent_size=int(cfg.get("recent_history_size", 20)),
        )

    return [_meta_text(mode, item), Image(path=item["path"])]


@mcp.tool()
def maybe_send_meme(
    query: str = "",
    allow_image_only: bool = True,
) -> list[TextContent | Image]:
    """Occasionally choose a meme for an eligible casual conversation turn.

    Call this only when a meme would be socially appropriate. Do not call it
    for emergencies, sensitive/high-stakes topics, code execution results,
    or when the user explicitly requests a text-only answer.

    Server-side probabilities decide among:
      - none: continue with a normal text reply;
      - append: answer normally, then display the returned image;
      - image_only: display only the returned image and end the assistant turn.

    The host should honor AICY_MEME_META. In particular, when terminal=true,
    it should stop the model/tool loop after rendering the image.
    """
    global _ELIGIBLE_CALLS_SINCE_MEME

    cfg = _load_config()
    cooldown = int(cfg.get("cooldown_eligible_calls", 0))

    with _LOCK:
        if _ELIGIBLE_CALLS_SINCE_MEME < cooldown:
            _ELIGIBLE_CALLS_SINCE_MEME += 1
            return [_meta_text("none")]

        p_append = float(cfg.get("append_probability", 0.0))
        p_only = float(cfg.get("image_only_probability", 0.0))
        if not allow_image_only:
            p_only = 0.0

        roll = random.SystemRandom().random()

        if roll < p_only:
            action = "image_only"
        elif roll < p_only + p_append:
            action = "append"
        else:
            _ELIGIBLE_CALLS_SINCE_MEME += 1
            return [_meta_text("none")]

        items = _scan_memes(cfg)
        item = _choose_item(
            items,
            query=query,
            recent_size=int(cfg.get("recent_history_size", 20)),
        )
        _ELIGIBLE_CALLS_SINCE_MEME = 0

    return [_meta_text(action, item), Image(path=item["path"])]


if __name__ == "__main__":
    # Local MCP server: stdio is the default and is the right transport for
    # a desktop/local host that launches this process.
    mcp.run()
