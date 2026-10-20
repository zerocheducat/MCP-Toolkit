from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

MODEL_ID = "google/embeddinggemma-2"
MODEL_REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
DIMENSIONS = (128, 256, 512, 768)


@dataclass(frozen=True)
class Config:
    db_path: Path
    allowed_roots: tuple[Path, ...]
    model_id: str = MODEL_ID
    model_path: Path | None = None
    dimension: int = 768
    max_file_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSIONS:
            raise ValueError(f"dimension must be one of {DIMENSIONS}")
        if self.max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        if not self.db_path.is_absolute():
            raise ValueError("database path must be absolute")
        if self.model_path is not None and not self.model_path.is_absolute():
            raise ValueError("model path must be absolute")


def from_env() -> Config:
    root = Path(__file__).resolve().parents[2]
    db_path = Path(os.environ.get("SEMANTIC_SEARCH_DB_PATH", str(root / "data" / "semantic_search.db"))).expanduser()
    model_value = os.environ.get("SEMANTIC_SEARCH_MODEL_PATH", "").strip()
    model_path = Path(model_value).expanduser().resolve() if model_value else None
    separator = os.pathsep
    roots = tuple(Path(p).expanduser().resolve() for p in os.environ.get("SEMANTIC_SEARCH_ALLOWED_ROOTS", "").split(separator) if p.strip())
    return Config(db_path=db_path, allowed_roots=roots, model_path=model_path,
                  dimension=int(os.environ.get("SEMANTIC_SEARCH_DIMENSION", "768")),
                  max_file_bytes=int(os.environ.get("SEMANTIC_SEARCH_MAX_FILE_BYTES", "2000000")))
