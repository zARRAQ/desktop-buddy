"""Shared fixtures. Headless SDL so pygame works in CI and on a Pi with no X."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pytest

from robot.core.bus import LocalHub
from robot.core.config import RobotConfig, load_config


@pytest.fixture
def config(tmp_path) -> RobotConfig:
    """Package defaults plus example layout A (pan/tilt head and a drivetrain), so the motion
    and safety tests have actuators to exercise. No local.yaml, no environment overrides."""
    return load_config(config_dir=tmp_path / "config", use_env=False, layout="A")


@pytest.fixture
def bare_config(tmp_path) -> RobotConfig:
    """Package defaults only: what a fresh install without hardware.yaml runs with."""
    return load_config(config_dir=tmp_path / "config", use_env=False)


@pytest.fixture
def hub() -> LocalHub:
    return LocalHub(record=1000)
