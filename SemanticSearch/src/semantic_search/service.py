from functools import lru_cache

from .config import from_env
from .embedding import EmbeddingGemma2
from .store import SearchStore


@lru_cache(maxsize=1)
def service() -> SearchStore:
    config = from_env()
    return SearchStore(config, EmbeddingGemma2(config))
