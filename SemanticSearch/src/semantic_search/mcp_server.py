import sqlite3
from typing import List, Optional
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .service import service

mcp = MCPServer("Aicy Semantic Search", version="0.1.0", log_level="WARNING")


def _checked(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except (ValueError, RuntimeError, OSError, sqlite3.Error, ImportError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def semantic_status() -> dict:
    """Show exact database, model, allowed roots, counts and implemented modalities."""
    return _checked(service().status)


@mcp.tool()
def semantic_index_files(paths: List[str], prune: bool = False) -> dict:
    """Incrementally index explicit UTF-8 text/code files or directories under configured allowed roots. prune removes missing files only below supplied directories."""
    return _checked(service().index_files, paths, prune)


@mcp.tool()
def semantic_sync_memoryhub(db_path: str) -> dict:
    """Read active records from an existing MemoryHub SQLite database without changing it. Synchronize to this service's separate index."""
    return _checked(service().sync_memoryhub, db_path)


@mcp.tool()
def semantic_search(query: str, kind: str = "text", limit: int = 10, source_kind: Optional[str] = None) -> dict:
    """Hybrid EmbeddingGemma 2 + SQLite FTS5 + FAISS search, fused and reranked with exact source paths, lines, and MemoryHub IDs."""
    return _checked(service().search, query, kind, limit, source_kind)


def main() -> None:
    service()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
