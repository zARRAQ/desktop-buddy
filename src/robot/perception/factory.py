"""Choose and open a perception backend: hailo on the robot, cpu elsewhere, null when neither."""

from __future__ import annotations

import logging
from pathlib import Path

from robot.core.config import PerceptionConfig
from robot.perception.base import PerceptionBackend

log = logging.getLogger(__name__)


def open_backend(cfg: PerceptionConfig, models_dir: Path | None = None) -> PerceptionBackend | None:
    want = cfg.backend
    if want in ("auto", "hailo"):
        try:
            from robot.perception.hailo import open_hailo_backend

            return open_hailo_backend(cfg.hailo_arch, models_dir, score_threshold=cfg.score_threshold)
        except ImportError:
            if want == "hailo":
                raise
            log.info("HailoRT not importable; trying the CPU backend")
        except (RuntimeError, FileNotFoundError, OSError) as exc:
            if want == "hailo":
                raise
            log.info("hailo backend unavailable (%s); trying the CPU backend", exc)
    if want in ("auto", "cpu"):
        try:
            from robot.perception.cpu import open_cpu_backend

            return open_cpu_backend(models_dir, score_threshold=cfg.score_threshold)
        except FileNotFoundError as exc:
            if want == "cpu":
                raise
            log.warning("%s; perception disabled", exc)
    return None
