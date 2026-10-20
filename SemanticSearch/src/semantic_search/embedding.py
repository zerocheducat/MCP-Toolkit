from __future__ import annotations

from typing import Protocol, Sequence
from threading import RLock
import numpy as np

from .config import Config, MODEL_REVISION


class EmbeddingBackend(Protocol):
    def encode_documents(self, texts: Sequence[str]) -> np.ndarray: ...
    def encode_query(self, text: str, kind: str = "text") -> np.ndarray: ...
    def encode_media(self, modality: str, source: str) -> np.ndarray: ...


def normalized(vectors: np.ndarray, dimension: int) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.ndim != 2 or array.shape[1] != dimension or not np.isfinite(array).all():
        raise ValueError(f"embedding must be finite and have {dimension} dimensions")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if (norms <= 0).any():
        raise ValueError("embedding must be nonzero")
    return np.ascontiguousarray(array / norms)


class EmbeddingGemma2:
    """Lazy text/code adapter; future media adapters can implement the same protocol."""

    def __init__(self, config: Config):
        self.config = config
        self._model = None
        self._lock = RLock()

    def _load(self):
        with self._lock:
            if self._model is None:
                try:
                    import torch
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise RuntimeError("Install the [model] extra to run EmbeddingGemma 2") from exc
                # Google warns that float16 degrades this model. CPU uses float32.
                dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float32
                source = str(self.config.model_path) if self.config.model_path else self.config.model_id
                if self.config.model_path and not (self.config.model_path / "model.safetensors").is_file():
                    raise RuntimeError(f"EmbeddingGemma 2 checkpoint is incomplete: {self.config.model_path}")
                location = {"local_files_only": True} if self.config.model_path else {"revision": MODEL_REVISION}
                self._model = SentenceTransformer(
                    source, **location,
                    config_kwargs={"vision_config": None, "audio_config": None},
                    model_kwargs={"torch_dtype": dtype},
                )
        return self._model

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.config.dimension), dtype=np.float32)
        # The model card prescribes this exact document prefix. Titles are kept
        # in source metadata so identical content can share one embedding.
        values = [f"title: none | text: {text}" for text in texts]
        result = self._load().encode(values, batch_size=8, convert_to_numpy=True,
                                     truncate_dim=self.config.dimension, normalize_embeddings=True)
        return normalized(result, self.config.dimension)

    def encode_query(self, text: str, kind: str = "text") -> np.ndarray:
        if kind not in {"text", "code"}:
            raise ValueError("kind must be text or code")
        prefix = "task: code retrieval" if kind == "code" else "task: search result"
        result = self._load().encode(f"{prefix} | query: {text}", convert_to_numpy=True,
                                     truncate_dim=self.config.dimension, normalize_embeddings=True)
        return normalized(result, self.config.dimension)[0]

    def encode_media(self, modality: str, source: str) -> np.ndarray:
        if modality not in {"image", "audio", "video"}:
            raise ValueError("unsupported modality")
        raise NotImplementedError(f"{modality} embedding is reserved for a later phase")
