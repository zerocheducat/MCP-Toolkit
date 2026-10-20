"""Paths are explicit and independent of the host's working directory."""
import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def db_path() -> Path:
    value = os.environ.get("MEMORYHUB_DB_PATH")
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("MEMORYHUB_DB_PATH must be an absolute path")
        return path
    return project_root() / "data" / "memoryhub.db"
