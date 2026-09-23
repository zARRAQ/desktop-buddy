"""SQLite-backed memory.

People, their face embeddings (per perception backend, since SFace and ArcFace vectors are
not comparable), free-text facts, and an event log. Small enough that identity matching
is a brute-force cosine search in numpy.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created REAL NOT NULL,
    last_seen REAL,
    seen_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    backend TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS embeddings_backend ON embeddings(backend);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""


@dataclass
class Person:
    id: int
    name: str
    created: float
    last_seen: float | None
    seen_count: int


class Memory:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL") if str(self.path) != ":memory:" else None
        self._conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()

    # -- people --------------------------------------------------------------------------
    def add_person(self, name: str) -> Person:
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO persons(name, created) VALUES (?, ?)", (name, time.time()))
        person = self.get_person(name)
        assert person is not None
        return person

    def get_person(self, name: str) -> Person | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, created, last_seen, seen_count FROM persons WHERE name = ?", (name.strip(),)
            ).fetchone()
        return Person(*row) if row else None

    def list_people(self) -> list[Person]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, created, last_seen, seen_count FROM persons ORDER BY name"
            ).fetchall()
        return [Person(*r) for r in rows]

    def touch_seen(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE persons SET last_seen = ?, seen_count = seen_count + 1 WHERE name = ?",
                (time.time(), name),
            )

    def forget(self, name: str) -> bool:
        """Hard delete: person, embeddings and facts."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM persons WHERE name = ?", (name.strip(),))
        return cur.rowcount > 0

    def forget_all(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM persons")
            self._conn.execute("DELETE FROM facts WHERE person_id IS NULL")
        return cur.rowcount

    # -- embeddings ----------------------------------------------------------------------
    def add_embedding(self, name: str, vec: np.ndarray, backend: str) -> None:
        person = self.add_person(name)
        v = np.asarray(vec, dtype=np.float32).ravel()
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v = v / norm
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO embeddings(person_id, backend, dim, vec, created) VALUES (?, ?, ?, ?, ?)",
                (person.id, backend, int(v.size), v.tobytes(), time.time()),
            )

    def embeddings(self, backend: str) -> tuple[list[str], np.ndarray]:
        """All stored vectors for one backend: (names, N x dim float32, L2-normalised)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.name, e.dim, e.vec FROM embeddings e JOIN persons p ON p.id = e.person_id "
                "WHERE e.backend = ? ORDER BY e.id",
                (backend,),
            ).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        dim = rows[0][1]
        names = [r[0] for r in rows]
        mat = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows if r[1] == dim])
        return names, mat

    def embedding_count(self, name: str, backend: str | None = None) -> int:
        with self._lock:
            if backend is None:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM embeddings e JOIN persons p ON p.id = e.person_id WHERE p.name = ?",
                    (name,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM embeddings e JOIN persons p ON p.id = e.person_id "
                    "WHERE p.name = ? AND e.backend = ?",
                    (name, backend),
                ).fetchone()
        return int(row[0])

    # -- facts ---------------------------------------------------------------------------
    def add_fact(self, text: str, person: str | None = None, *, limit: int = 50) -> None:
        text = text.strip()
        if not text:
            return
        pid = None
        if person:
            pid = self.add_person(person).id
        with self._lock, self._conn:
            exists = self._conn.execute("SELECT 1 FROM facts WHERE text = ? AND person_id IS ?", (text, pid)).fetchone()
            if exists:
                return
            self._conn.execute("INSERT INTO facts(person_id, text, created) VALUES (?, ?, ?)", (pid, text, time.time()))
            # keep the newest `limit` facts per person
            self._conn.execute(
                "DELETE FROM facts WHERE person_id IS ? AND id NOT IN "
                "(SELECT id FROM facts WHERE person_id IS ? ORDER BY id DESC LIMIT ?)",
                (pid, pid, limit),
            )

    def facts(self, person: str | None = None, limit: int = 20) -> list[str]:
        with self._lock:
            if person is None:
                rows = self._conn.execute(
                    "SELECT text FROM facts WHERE person_id IS NULL ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT f.text FROM facts f JOIN persons p ON p.id = f.person_id "
                    "WHERE p.name = ? ORDER BY f.id DESC LIMIT ?",
                    (person, limit),
                ).fetchall()
        return [r[0] for r in rows]

    # -- events --------------------------------------------------------------------------
    def log_event(self, kind: str, detail: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO events(ts, kind, detail) VALUES (?, ?, ?)", (time.time(), kind, detail))
            self._conn.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 5000)")

    def recent_events(self, limit: int = 20) -> list[tuple[float, str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, detail FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [(float(r[0]), str(r[1]), str(r[2] or "")) for r in rows]
