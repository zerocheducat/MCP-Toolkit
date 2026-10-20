from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from memoryhub.core.models import timestamp
from memoryhub.core.retrieval import INDEX_VERSION, index_memory

SCHEMA = """
CREATE TABLE IF NOT EXISTS mh_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mh_memories (
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, topic TEXT NOT NULL,
 content TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
 valid_from TEXT NOT NULL, valid_to TEXT, expires_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_confirmed TEXT NOT NULL,
 strength REAL NOT NULL DEFAULT 1, pinned INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1, verified INTEGER NOT NULL DEFAULT 0, supersedes TEXT, conflict_with TEXT,
 CHECK(status IN ('active','pending','superseded','expired','forgotten','archived'))
);
CREATE INDEX IF NOT EXISTS mh_topic ON mh_memories(namespace, topic, status);
CREATE UNIQUE INDEX IF NOT EXISTS mh_one_current ON mh_memories(namespace, topic) WHERE status='active';
CREATE TABLE IF NOT EXISTS mh_evidence (
 id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL REFERENCES mh_memories(id),
 fingerprint TEXT NOT NULL, source_text TEXT NOT NULL, quote TEXT NOT NULL,
 source_role TEXT NOT NULL, source_ref TEXT, observed_at TEXT NOT NULL,
 UNIQUE(memory_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS mh_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT, action TEXT NOT NULL,
 reason TEXT NOT NULL, snapshot_json TEXT, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mh_requests (
 fingerprint TEXT PRIMARY KEY, memory_id TEXT, action TEXT NOT NULL, reason TEXT NOT NULL
);
"""

SEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS mh_search_docs (
 memory_id TEXT PRIMARY KEY REFERENCES mh_memories(id),
 namespace TEXT NOT NULL, term_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mh_search_namespace ON mh_search_docs(namespace);
CREATE TABLE IF NOT EXISTS mh_search_terms (
 term TEXT NOT NULL, memory_id TEXT NOT NULL REFERENCES mh_search_docs(memory_id),
 PRIMARY KEY(term,memory_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS mh_search_memory ON mh_search_terms(memory_id);
CREATE INDEX IF NOT EXISTS mh_evidence_recent ON mh_evidence(memory_id,id DESC);
CREATE INDEX IF NOT EXISTS mh_recall_recent ON mh_memories(namespace,valid_from DESC,id DESC);
CREATE INDEX IF NOT EXISTS mh_expiring ON mh_memories(expires_at)
 WHERE status IN ('active','pending') AND expires_at IS NOT NULL;
"""


class SQLiteMemoryStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.create_function('mh_timestamp', 1, lambda value: timestamp(value) if value is not None else None,
                             deterministic=True)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self, now: str) -> None:
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
        with self.connect(write=True) as c:
            version = c.execute("SELECT value FROM mh_meta WHERE key='schema_version'").fetchone()
            if version and version[0] != '2':
                raise ValueError("Unsupported MemoryHub schema version; do not downgrade this database")
            legacy = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'").fetchone() if not version else None
            if legacy:
                expected = {'id','content','namespace','memory_type','importance','status','source','created_at','updated_at','deleted_at'}
                actual = {r['name'] for r in c.execute('PRAGMA table_info(memories)')}
                if not expected.issubset(actual):
                    raise ValueError('Existing memories table does not match the uploaded v0.1.2 schema')
                for row in c.execute('SELECT * FROM memories WHERE deleted_at IS NULL'):
                    created = timestamp(row['created_at'])
                    status = 'archived' if row['status'] == 'archived' else 'pending'
                    c.execute('''INSERT INTO mh_memories
                     (id,namespace,topic,content,kind,status,valid_from,created_at,updated_at,last_confirmed)
                     VALUES (?,?,?,?,?,?,?,?,?,?)''',
                     (row['id'], row['namespace'], 'legacy/' + row['id'], row['content'],
                      row['memory_type'], status, created, created, now, created))
                    self.event(c, row['id'], 'migrated', 'Legacy row preserved; source evidence requires confirmation', dict(row), now)
            if not version:
                c.execute("INSERT INTO mh_meta VALUES ('schema_version','2')")
        self._initialize_search(now)

    def _initialize_search(self, now: str) -> None:
        self.index_backup = None
        with self.connect() as c:
            version = c.execute("SELECT value FROM mh_meta WHERE key='search_index_version'").fetchone()
            if version and version[0] != INDEX_VERSION:
                raise ValueError('Unsupported search index version; do not replace it silently')
            populated = c.execute('SELECT 1 FROM mh_memories LIMIT 1').fetchone() is not None
        if not version and populated:
            from uuid import uuid4
            stamp = now.split('.')[0].replace(':', '').replace('+', '-')
            destination = self.db_path.parent / 'backups' / ('memoryhub-before-search-' + stamp + '-' + uuid4().hex + '.db')
            self.backup(destination)
            self.index_backup = str(destination)
        with self.connect() as c:
            c.executescript(SEARCH_SCHEMA)
        with self.connect(write=True) as c:
            # A streaming cursor bounds Python memory during first initialization
            # and adds records written by an older service after code rollback.
            rows = c.execute('SELECT m.id,m.namespace,m.content,m.topic FROM mh_memories m '
                             'WHERE NOT EXISTS (SELECT 1 FROM mh_search_docs d WHERE d.memory_id=m.id)')
            for row in rows:
                index_memory(c, row)
            c.execute("INSERT OR REPLACE INTO mh_meta(key,value) VALUES ('search_index_version',?)", (INDEX_VERSION,))

    @staticmethod
    def event(c, memory_id, action, reason, snapshot, now):
        c.execute('INSERT INTO mh_events(memory_id,action,reason,snapshot_json,recorded_at) VALUES (?,?,?,?,?)',
                  (memory_id, action, reason, json.dumps(snapshot, ensure_ascii=False) if snapshot else None, now))

    @staticmethod
    def record(c, memory_id: str) -> dict | None:
        row = c.execute('SELECT * FROM mh_memories WHERE id=?', (memory_id,)).fetchone()
        return dict(row) if row else None

    def backup(self, destination: Path) -> None:
        if destination.exists():
            raise ValueError('Backup destination already exists')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
