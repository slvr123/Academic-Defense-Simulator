"""all-MiniLM-L6-v2 on ONNX Runtime, ported from the hosting spike (spike/render-onnx).

Reproduces SentenceTransformer.encode(normalize_embeddings=True) for this model:
truncate at 256 tokens, mean-pool over the attention mask, L2-normalise. One text
per forward pass (batch size 1 is what passed spike 2's memory gate).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

EMBEDDING_DIM = 384
# sentence-transformers' max_seq_length for all-MiniLM-L6-v2. The repo's
# tokenizer.json ships with truncation at 128; left as is, the spike measured a
# min cosine of 0.73 against sentence-transformers on our long chunks.
MAX_SEQ_LENGTH = 256

# One forward pass at a time per process: ingest runs on the worker thread, and
# from M2 query embedding will run from request handlers too.
_EMBED_LOCK = threading.Lock()


class Embedder(Protocol):
    def encode_one(self, text: str) -> np.ndarray:
        """A unit-length float32 vector of shape (EMBEDDING_DIM,)."""
        ...


@dataclass
class OnnxEmbedder:
    session: Any  # onnxruntime.InferenceSession
    tokenizer: Any  # tokenizers.Tokenizer
    input_names: list[str]

    @classmethod
    def load(cls, model_dir: Path, threads: int = 1) -> OnnxEmbedder:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
        tokenizer.no_padding()  # one text per pass, so padding would only add tokens
        options = ort.SessionOptions()
        # The free instance has a fraction of a CPU; more threads only contend.
        options.intra_op_num_threads = threads
        # The CPU arena keeps freed activation buffers; off cut ~125 MB in the spike.
        options.enable_cpu_mem_arena = False
        session = ort.InferenceSession(
            str(model_dir / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        return cls(session, tokenizer, [i.name for i in session.get_inputs()])

    def encode_one(self, text: str) -> np.ndarray:
        encoding = self.tokenizer.encode(text)
        ids = np.array([encoding.ids], dtype=np.int64)
        mask = np.array([encoding.attention_mask], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
        with _EMBED_LOCK:
            token_vectors = self.session.run(None, {n: feeds[n] for n in self.input_names})[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (token_vectors * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        vector = pooled[0] / np.linalg.norm(pooled[0])
        return vector.astype(np.float32)
