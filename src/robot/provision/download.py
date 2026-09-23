"""Resumable downloads with checksum verification and archive extraction."""

from __future__ import annotations

import hashlib
import json
import logging
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from robot.core import paths
from robot.provision.manifest import ModelFile

log = logging.getLogger(__name__)

Progress = Callable[[str, int, int | None], None]


def _no_progress(_name: str, _done: int, _total: int | None) -> None:
    return None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LockFile:
    """Records the hash of every file we downloaded without a published checksum."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except ValueError:
                self.data = {}

    def get(self, url: str) -> str | None:
        return self.data.get(url)

    def set(self, url: str, digest: str) -> None:
        self.data[url] = digest
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))


class Provisioner:
    def __init__(
        self, models_dir: Path | None = None, *, progress: Progress | None = None, timeout: float = 60.0
    ) -> None:
        self.models_dir = models_dir or paths.models_dir()
        self.lock = LockFile(self.models_dir / "models.lock.json")
        self.progress = progress or _no_progress
        self.client = httpx.Client(follow_redirects=True, timeout=httpx.Timeout(timeout, connect=15.0))

    # -- status -------------------------------------------------------------------------------
    def target(self, m: ModelFile) -> Path:
        return self.models_dir / m.dest

    def is_present(self, m: ModelFile) -> bool:
        t = self.target(m)
        if m.extract:
            return (t / _archive_dirname(m.url)).is_dir()
        return t.exists()

    def verify(self, m: ModelFile) -> tuple[bool, str]:
        if m.extract:
            return (True, "extracted") if self.is_present(m) else (False, "missing")
        t = self.target(m)
        if not t.exists():
            return False, "missing"
        expected = m.sha256 or self.lock.get(m.url)
        if expected is None:
            return True, "present (no checksum on record)"
        digest = sha256_of(t)
        return (
            digest == expected,
            "ok" if digest == expected else f"checksum mismatch: {digest[:12]}... != {expected[:12]}...",
        )

    # -- download ---------------------------------------------------------------------------
    def fetch(self, m: ModelFile, *, force: bool = False) -> Path:
        target = self.target(m)
        if m.extract:
            archive = self.models_dir / "downloads" / Path(m.url).name
            if not force and self.is_present(m):
                return target / _archive_dirname(m.url)
            self._download(m, archive)
            target.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive) as tf:
                _safe_extract(tf, target)
            archive.unlink(missing_ok=True)
            return target / _archive_dirname(m.url)
        if not force and target.exists():
            ok, why = self.verify(m)
            if ok:
                return target
            log.warning("%s: %s; re-downloading", target.name, why)
            target.unlink()
        self._download(m, target)
        return target

    def _download(self, m: ModelFile, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(target.suffix + ".part")
        for attempt in range(1, 6):
            try:
                self._stream(m, part)
                break
            except (httpx.HTTPError, OSError) as exc:
                wait = min(30, 2**attempt)
                log.warning("%s: %s (attempt %d); retrying in %ds", m.name, exc, attempt, wait)
                time.sleep(wait)
        else:
            raise RuntimeError(f"giving up on {m.name}")
        digest = sha256_of(part)
        if m.sha256 and digest != m.sha256:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{m.name}: checksum mismatch ({digest} != {m.sha256}); download discarded")
        recorded = self.lock.get(m.url)
        if recorded is None:
            self.lock.set(m.url, digest)
        elif recorded != digest:
            log.warning(
                "%s: hash changed since first download (%s -> %s). Upstream republished the file; recording the new hash.",
                m.name,
                recorded[:12],
                digest[:12],
            )
            self.lock.set(m.url, digest)
        part.replace(target)

    def _stream(self, m: ModelFile, part: Path) -> None:
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with self.client.stream("GET", m.url, headers=headers) as r:
            if r.status_code == 416:  # already complete
                return
            if have and r.status_code != 206:
                have = 0  # server ignored the range: start over
            r.raise_for_status()
            total = None
            if "content-range" in r.headers:
                total = int(r.headers["content-range"].split("/")[-1])
            elif "content-length" in r.headers:
                total = have + int(r.headers["content-length"])
            mode = "ab" if have else "wb"
            done = have
            with part.open(mode) as fh:
                for chunk in r.iter_bytes(1 << 18):
                    fh.write(chunk)
                    done += len(chunk)
                    self.progress(m.name, done, total or m.size)

    def close(self) -> None:
        self.client.close()


def _archive_dirname(url: str) -> str:
    name = Path(url).name
    for suffix in (".tar.bz2", ".tar.gz", ".tgz", ".tar"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest_resolved)):
            raise RuntimeError(f"archive member escapes destination: {member.name}")
    try:
        tf.extractall(dest, filter="data")
    except TypeError:  # Python < 3.12 without the filter argument
        tf.extractall(dest)
