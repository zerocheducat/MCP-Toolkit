from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .service import service


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="semantic-search")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve-mcp")
    api = commands.add_parser("serve-api")
    api.add_argument("--port", type=int, default=8765)
    commands.add_parser("status")
    commands.add_parser("connection")
    index = commands.add_parser("index")
    index.add_argument("paths", nargs="+")
    index.add_argument("--prune", action="store_true")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--kind", choices=("text", "code"), default="text")
    search.add_argument("--limit", type=int, default=10)
    memoryhub = commands.add_parser("sync-memoryhub")
    memoryhub.add_argument("db_path")
    args = parser.parse_args(argv)
    if args.command == "serve-mcp":
        from .mcp_server import main as serve
        serve()
        return
    if args.command == "serve-api":
        if not os.environ.get("SEMANTIC_SEARCH_API_TOKEN"):
            parser.error("SEMANTIC_SEARCH_API_TOKEN is required for serve-api")
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        import uvicorn
        uvicorn.run("semantic_search.api:app", host="127.0.0.1", port=args.port)
        return
    if args.command == "connection":
        entry = Path(__file__).resolve().parents[2] / "run_semantic_search.py"
        forwarded = {key: os.environ[key] for key in (
            "SEMANTIC_SEARCH_ALLOWED_ROOTS", "SEMANTIC_SEARCH_DB_PATH", "SEMANTIC_SEARCH_MODEL_PATH",
            "SEMANTIC_SEARCH_DIMENSION", "SEMANTIC_SEARCH_MAX_FILE_BYTES") if key in os.environ}
        print(json.dumps({"name": "SemanticSearch", "transport": "stdio", "command": sys.executable,
                          "args": ["-I", str(entry), "serve-mcp"], "env": forwarded,
                          "cwd": str(entry.parent), "enabled": True}, ensure_ascii=False, indent=2))
        return
    store = service()
    if args.command == "status":
        output = store.status()
    elif args.command == "index":
        output = store.index_files(args.paths, args.prune)
    elif args.command == "sync-memoryhub":
        output = store.sync_memoryhub(args.db_path)
    else:
        output = store.search(args.query, args.kind, args.limit)
    print(json.dumps(output, ensure_ascii=False, indent=2))
