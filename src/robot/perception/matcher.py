"""Cosine identity matching against everything in memory for the active backend."""

from __future__ import annotations

import time

import numpy as np

from robot.memory import Memory


class IdentityIndex:
    def __init__(self, memory: Memory, backend: str, threshold: float, *, refresh_s: float = 5.0) -> None:
        self.memory = memory
        self.backend = backend
        self.threshold = threshold
        self.refresh_s = refresh_s
        self._names: list[str] = []
        self._mat = np.zeros((0, 0), dtype=np.float32)
        self._loaded = 0.0
        self.refresh(force=True)

    def refresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._loaded < self.refresh_s:
            return
        self._names, self._mat = self.memory.embeddings(self.backend)
        self._loaded = now

    @property
    def size(self) -> int:
        return len(self._names)

    def match(self, vec: np.ndarray) -> tuple[str | None, float]:
        """Best (name, cosine similarity); name is None below threshold or with an empty index."""
        self.refresh()
        if self._mat.size == 0:
            return None, 0.0
        q = np.asarray(vec, dtype=np.float32).ravel()
        n = float(np.linalg.norm(q))
        if n > 0:
            q = q / n
        sims = self._mat @ q
        # aggregate per person: best of their samples
        best: dict[str, float] = {}
        for name, s in zip(self._names, sims.tolist(), strict=True):
            if s > best.get(name, -2.0):
                best[name] = s
        name, score = max(best.items(), key=lambda kv: kv[1])
        if score >= self.threshold:
            return name, float(score)
        return None, float(score)
