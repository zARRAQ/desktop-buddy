"""Optional: run llama.cpp's ``llama-server`` as a child process of the brain service."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from robot.core.config import BrainConfig

log = logging.getLogger(__name__)


class LlamaServerManager:
    def __init__(self, cfg: BrainConfig, model_path: Path) -> None:
        self.cfg = cfg
        self.model_path = model_path
        self.proc: subprocess.Popen[bytes] | None = None

    @property
    def endpoint(self) -> str:
        return f"http://{self.cfg.llama_server.host}:{self.cfg.llama_server.port}/v1"

    def command(self) -> list[str]:
        ls = self.cfg.llama_server
        return [
            ls.binary,
            "-m",
            str(self.model_path),
            "--host",
            ls.host,
            "--port",
            str(ls.port),
            "-t",
            str(ls.threads),
            "-c",
            str(ls.ctx_size),
            "--jinja",  # proper chat templates, needed for tool/JSON behaviour on recent models
            "--no-warmup",
            *ls.extra_args,
        ]

    def start(self, wait_s: float = 120.0) -> None:
        if shutil.which(self.cfg.llama_server.binary) is None:
            raise FileNotFoundError(
                f"{self.cfg.llama_server.binary} not on PATH; install llama.cpp or set brain.managed: none"
            )
        if not self.model_path.exists():
            raise FileNotFoundError(f"model {self.model_path} missing; run `robot provision --group llm`")
        log.info("starting %s", " ".join(self.command()))
        self.proc = subprocess.Popen(self.command(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with {self.proc.returncode}")
            try:
                r = httpx.get(f"http://{self.cfg.llama_server.host}:{self.cfg.llama_server.port}/health", timeout=1.0)
                if r.status_code == 200:
                    log.info("llama-server ready")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become healthy")

    def rss_mb(self) -> float | None:
        if self.proc is None:
            return None
        try:
            with open(f"/proc/{self.proc.pid}/statm", encoding="ascii") as fh:  # noqa: PTH123
                pages = int(fh.read().split()[1])
            import os

            return pages * os.sysconf("SC_PAGE_SIZE") / 1e6
        except (OSError, ValueError, IndexError):
            return None

    def stop(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
