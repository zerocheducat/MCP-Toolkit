# MemoryHub

Evidence-aware personal memory over MCP stdio. This component stores user-provided memory, evidence, revisions, and audit events in a local SQLite database.

## Install and run

From the repository root, install into the Python interpreter selected for the MCP host:

```bash
python -m pip install -e ./MemoryHub
python MemoryHub/run_memoryhub.py test
python -I MemoryHub/run_memoryhub.py serve
```

Set `MEMORYHUB_DB_PATH` to an absolute path to move the database from the default `MemoryHub/data/memoryhub.db`. `MemoryHub/run_memoryhub.py connect` is a Jan-specific integration helper; it is not needed by generic MCP hosts. `memory_manage` soft-deletion does not erase previous evidence or audit events. Do not commit memory databases, backups, or conversation evidence.

See [the main README](../README.md) for further details and deployment limitations.
