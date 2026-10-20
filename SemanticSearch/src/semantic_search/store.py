from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Iterable

import numpy as np

from .config import Config, MODEL_REVISION
from .embedding import EmbeddingBackend, normalized

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 source_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, origin TEXT NOT NULL,
 source_ref TEXT NOT NULL, fingerprint TEXT NOT NULL, modified_ns INTEGER,
 title TEXT, metadata_json TEXT NOT NULL, indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sources_origin ON sources(source_kind, origin);
CREATE TABLE IF NOT EXISTS chunks(
 id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
 content TEXT NOT NULL, modality TEXT NOT NULL DEFAULT 'text', vector BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS occurrences(
 source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, chunk_id INTEGER NOT NULL REFERENCES chunks(id),
 line_start INTEGER NOT NULL, line_end INTEGER NOT NULL,
 PRIMARY KEY(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS occurrences_chunk ON occurrences(chunk_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, tokenize='unicode61');
"""

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".xml", ".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".py",
    ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".ps1", ".sql",
})
SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "target", "dist", "build", "__pycache__", ".pytest_cache"})


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pieces(text: str, width: int = 1500, overlap: int = 180) -> list[tuple[str, int, int]]:
    if not text.strip():
        return []
    # Fixed character windows bound model input even for minified/long lines.
    line_breaks = [-1] + [i for i, char in enumerate(text) if char == "\n"]
    result = []
    start = 0
    while start < len(text):
        end = min(start + width, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + width // 2, end)
            if boundary > start:
                end = boundary + 1
        content = text[start:end]
        if content.strip():
            first = bisect_right(line_breaks, start)
            last = bisect_right(line_breaks, max(start, end - 1))
            result.append((content, first, last))
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return result


class SearchStore:
    def __init__(self, config: Config, embedder: EmbeddingBackend):
        self.config = config
        self.embedder = embedder
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._faiss_revision = None
        self._faiss_index = None
        with self._connect() as db:
            db.executescript(SCHEMA)
            identity = json.dumps({"model_id": config.model_id, "model_revision": MODEL_REVISION,
                                   "dimension": config.dimension, "format": 1}, sort_keys=True)
            row = db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
            if row and row[0] != identity:
                raise ValueError("index model/dimension differs; choose another database or rebuild explicitly")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('identity', ?)", (identity,))
            db.execute("INSERT OR IGNORE INTO meta VALUES ('revision', '0')")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.config.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _revision(db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    @staticmethod
    def _bump(db: sqlite3.Connection) -> None:
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def _ensure_root(self, path: Path) -> Path:
        resolved = path.resolve(strict=True)
        if not self.config.allowed_roots or not any(resolved.is_relative_to(root) for root in self.config.allowed_roots):
            raise ValueError(f"path is outside SEMANTIC_SEARCH_ALLOWED_ROOTS: {resolved}")
        return resolved

    def _files(self, paths: Iterable[str]) -> list[Path]:
        selected: set[Path] = set()
        for value in paths:
            path = self._ensure_root(Path(value))
            if path.is_file():
                selected.add(path)
            elif path.is_dir():
                for base, dirs, names in os.walk(path, followlinks=False):
                    dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
                    for name in names:
                        child = Path(base) / name
                        if child.suffix.lower() in TEXT_EXTENSIONS and child.is_file():
                            resolved = child.resolve()
                            if any(resolved.is_relative_to(root) for root in self.config.allowed_roots):
                                selected.add(resolved)
            else:
                raise ValueError(f"not a file or directory: {path}")
        return sorted(selected)

    def _upsert(self, source_id: str, source_kind: str, origin: str, source_ref: str,
                text: str, fingerprint: str, modified_ns: int | None, title: str,
                metadata: dict) -> str:
        chunks = _pieces(text)
        if not chunks:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT 1 FROM sources WHERE source_id=?", (source_id,)).fetchone()
                if old:
                    db.execute("DELETE FROM sources WHERE source_id=?", (source_id,))
                    self._collect_orphans(db)
                    self._bump(db)
            return "removed" if old else "empty"
        with self._connect() as db:
            old = db.execute("SELECT fingerprint FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if old and old[0] == fingerprint:
                return "unchanged"
            hashes = [_sha(part.encode("utf-8")) for part, _, _ in chunks]
            known = {}
            for content_hash in set(hashes):
                row = db.execute("SELECT vector FROM chunks WHERE content_hash=?", (content_hash,)).fetchone()
                if row:
                    known[content_hash] = row[0]
        missing = {}
        for content_hash, (part, _, _) in zip(hashes, chunks):
            if content_hash not in known and content_hash not in missing:
                missing[content_hash] = part
        vectors = {}
        if missing:
            values = normalized(self.embedder.encode_documents(list(missing.values())), self.config.dimension)
            if len(values) != len(missing):
                raise ValueError("embedder returned the wrong number of vectors")
            vectors = {key: vector.tobytes() for key, vector in zip(missing, values)}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT fingerprint FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if old and old[0] == fingerprint:
                return "unchanged"
            db.execute("DELETE FROM occurrences WHERE source_id=?", (source_id,))
            db.execute("""INSERT INTO sources(source_id,source_kind,origin,source_ref,fingerprint,modified_ns,title,metadata_json,indexed_at)
                          VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                          fingerprint=excluded.fingerprint,modified_ns=excluded.modified_ns,title=excluded.title,
                          metadata_json=excluded.metadata_json,indexed_at=excluded.indexed_at""",
                       (source_id, source_kind, origin, source_ref, fingerprint, modified_ns, title,
                        json.dumps(metadata, ensure_ascii=False), _now()))
            for ordinal, ((part, first, last), content_hash) in enumerate(zip(chunks, hashes)):
                row = db.execute("SELECT id FROM chunks WHERE content_hash=?", (content_hash,)).fetchone()
                if row:
                    chunk_id = row[0]
                else:
                    cursor = db.execute("INSERT INTO chunks(content_hash,content,vector) VALUES (?,?,?)",
                                        (content_hash, part, vectors.get(content_hash, known.get(content_hash))))
                    chunk_id = cursor.lastrowid
                    db.execute("INSERT INTO chunks_fts(rowid,content) VALUES (?,?)", (chunk_id, part))
                db.execute("INSERT INTO occurrences VALUES (?,?,?,?,?)", (source_id, ordinal, chunk_id, first, last))
            self._collect_orphans(db)
            self._bump(db)
        return "updated" if old else "added"

    @staticmethod
    def _collect_orphans(db: sqlite3.Connection) -> None:
        ids = [r[0] for r in db.execute("SELECT id FROM chunks WHERE NOT EXISTS (SELECT 1 FROM occurrences WHERE chunk_id=chunks.id)")]
        db.executemany("DELETE FROM chunks_fts WHERE rowid=?", ((i,) for i in ids))
        db.executemany("DELETE FROM chunks WHERE id=?", ((i,) for i in ids))

    def _remove_source(self, source_id: str) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            deleted = db.execute("DELETE FROM sources WHERE source_id=?", (source_id,)).rowcount
            if deleted:
                self._collect_orphans(db)
                self._bump(db)
            return bool(deleted)

    def index_files(self, paths: list[str], prune: bool = False) -> dict:
        if not paths:
            raise ValueError("paths must contain at least one file or directory")
        files = self._files(paths)
        counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "removed": 0}
        errors = []
        for path in files:
            try:
                if path.suffix.lower() not in TEXT_EXTENSIONS or path.stat().st_size > self.config.max_file_bytes:
                    counts["removed" if self._remove_source(str(path)) else "skipped"] += 1
                    continue
                raw = path.read_bytes()
                if b"\x00" in raw:
                    counts["removed" if self._remove_source(str(path)) else "skipped"] += 1
                    continue
                try:
                    content = raw.decode("utf-8-sig")
                except UnicodeError:
                    counts["removed" if self._remove_source(str(path)) else "skipped"] += 1
                    continue
                fingerprint = _sha(raw)
                result = self._upsert(str(path), "file", str(path), str(path), content,
                                      fingerprint, path.stat().st_mtime_ns, path.name, {})
                counts[result if result in counts else "skipped"] += 1
            except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
                errors.append({"path": str(path), "error": str(exc)})
        if prune and not errors:
            # Only prune files under explicitly supplied directories; a single
            # file index request must never remove its siblings.
            directories = [self._ensure_root(Path(p)) for p in paths if Path(p).is_dir()]
            keep = {str(path) for path in files}
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for row in db.execute("SELECT source_id FROM sources WHERE source_kind='file'").fetchall():
                    source = Path(row[0])
                    if any(source.is_relative_to(directory) for directory in directories) and row[0] not in keep:
                        db.execute("DELETE FROM sources WHERE source_id=?", (row[0],))
                        counts["removed"] += 1
                if counts["removed"]:
                    self._collect_orphans(db)
                    self._bump(db)
        return {"counts": counts, "errors": errors, "files_seen": len(files)}

    def sync_memoryhub(self, db_path: str) -> dict:
        path = self._ensure_root(Path(db_path))
        if not path.is_file():
            raise ValueError("MemoryHub database path must be a file")
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30) as source:
            source.row_factory = sqlite3.Row
            rows = [dict(r) for r in source.execute("""SELECT id,namespace,topic,content,revision,valid_from,valid_to,expires_at
                       FROM mh_memories WHERE status='active' ORDER BY id""")]
        current = datetime.now(timezone.utc)
        def current_record(row: dict) -> bool:
            for field, future in (("valid_from", True), ("valid_to", False), ("expires_at", False)):
                value = row.get(field)
                if value:
                    when = datetime.fromisoformat(value)
                    if when.tzinfo is None:
                        raise ValueError(f"MemoryHub {field} lacks timezone for {row['id']}")
                    outside = when > current if future else when <= current
                    if outside:
                        return False
            return True
        rows = [row for row in rows if current_record(row)]
        prefix = f"memoryhub:{path}:"
        counts = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0}
        live = set()
        for row in rows:
            source_id = prefix + row["id"]
            live.add(source_id)
            metadata = {key: row[key] for key in ("namespace", "topic", "revision", "valid_from", "valid_to", "expires_at")}
            result = self._upsert(source_id, "memoryhub", str(path), row["id"], row["content"],
                                  _sha(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")), None,
                                  row["topic"], metadata)
            if result in counts:
                counts[result] += 1
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT source_id FROM sources WHERE source_kind='memoryhub' AND origin=?", (str(path),)).fetchall():
                if row[0] not in live:
                    db.execute("DELETE FROM sources WHERE source_id=?", (row[0],))
                    counts["removed"] += 1
            if counts["removed"]:
                self._collect_orphans(db)
                self._bump(db)
        return {"counts": counts, "memoryhub_db_path": str(path)}

    def _vector_index(self, db: sqlite3.Connection):
        import faiss
        revision = self._revision(db)
        with self._lock:
            if revision != self._faiss_revision:
                index = faiss.IndexIDMap2(faiss.IndexFlatIP(self.config.dimension))
                rows = db.execute("SELECT id,vector FROM chunks ORDER BY id").fetchall()
                if rows:
                    vectors = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                    if vectors.shape[1] != self.config.dimension:
                        raise ValueError("stored vector dimension mismatch")
                    index.add_with_ids(np.ascontiguousarray(vectors), np.asarray([r[0] for r in rows], dtype=np.int64))
                self._faiss_index = index
                self._faiss_revision = revision
            return self._faiss_index

    def search(self, query: str, kind: str = "text", limit: int = 10, source_kind: str | None = None) -> dict:
        query = query.strip()
        if not query or len(query) > 4000:
            raise ValueError("query must contain 1 to 4000 characters")
        if kind not in {"text", "code"} or source_kind not in {None, "file", "memoryhub"}:
            raise ValueError("unsupported kind or source_kind")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        candidate_limit = min(max(40, limit * 8), 800)
        query_vector = normalized(self.embedder.encode_query(query, kind), self.config.dimension)
        with self._connect() as db:
            index = self._vector_index(db)
            vector_rank = {}
            if index.ntotal:
                vector_k = index.ntotal if source_kind else min(candidate_limit, index.ntotal)
                scores, ids = index.search(query_vector, vector_k)
                vector_rank = {int(chunk_id): (rank, float(scores[0][rank - 1]))
                               for rank, chunk_id in enumerate(ids[0], 1) if chunk_id >= 0}
            terms = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))[:16]
            lexical_rank = {}
            if terms:
                match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                lexical_limit = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] if source_kind else candidate_limit
                rows = db.execute("SELECT rowid,bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY 2 LIMIT ?",
                                  (match, lexical_limit)).fetchall()
                lexical_rank = {int(row[0]): rank for rank, row in enumerate(rows, 1)}
            candidates = set(vector_rank) | set(lexical_rank)
            results = []
            for chunk_id in candidates:
                row = db.execute("SELECT content,modality,content_hash FROM chunks WHERE id=?", (chunk_id,)).fetchone()
                source_rows = db.execute("""SELECT s.source_kind,s.origin,s.source_ref,s.title,s.metadata_json,
                         o.line_start,o.line_end FROM occurrences o JOIN sources s ON s.source_id=o.source_id
                         WHERE o.chunk_id=? ORDER BY s.source_kind,s.source_ref,o.ordinal""", (chunk_id,)).fetchall()
                sources = [{"source_kind": r[0], "origin": r[1], "source_ref": r[2], "title": r[3],
                            "metadata": json.loads(r[4]), "line_start": r[5], "line_end": r[6]} for r in source_rows
                           if source_kind is None or r[0] == source_kind]
                if not sources:
                    continue
                vr, cosine = vector_rank.get(chunk_id, (None, None))
                lr = lexical_rank.get(chunk_id)
                # Reciprocal-rank fusion followed by an exact phrase boost.
                score = (1 / (60 + vr) if vr is not None else 0) + (1 / (60 + lr) if lr is not None else 0)
                if query.casefold() in row[0].casefold():
                    score += 0.01
                results.append({"chunk_id": chunk_id, "content_hash": row[2], "content": row[0],
                                "modality": row[1], "score": round(score, 8), "cosine": cosine,
                                "vector_rank": vr, "lexical_rank": lr, "sources": sources})
            results.sort(key=lambda r: (-r["score"], r["chunk_id"]))
            return {"query": query, "kind": kind, "items": results[:limit], "total_candidates": len(results),
                    "model_id": self.config.model_id, "model_revision": MODEL_REVISION,
                    "dimension": self.config.dimension}

    def status(self) -> dict:
        with self._connect() as db:
            return {"ok": True, "db_path": str(self.config.db_path), "model_id": self.config.model_id,
                    "model_revision": MODEL_REVISION, "model_path": str(self.config.model_path) if self.config.model_path else None,
                    "dimension": self.config.dimension, "allowed_roots": [str(p) for p in self.config.allowed_roots],
                    "model_runtime_available": importlib.util.find_spec("sentence_transformers") is not None,
                    "faiss_available": importlib.util.find_spec("faiss") is not None,
                    "sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                    "chunks": db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                    "revision": self._revision(db),
                    "modalities": {"text": "implemented", "code": "implemented", "image": "reserved", "audio": "reserved", "video": "reserved"}}
