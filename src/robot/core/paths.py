"""Filesystem locations. Everything the robot writes lives under one data directory."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Per-robot data: models, memory database, recordings.

    Honours ``ROBOT_DATA_DIR``, then ``XDG_DATA_HOME``, then ``~/.local/share/robot``.
    """
    env = os.environ.get("ROBOT_DATA_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "robot"


def models_dir() -> Path:
    return data_dir() / "models"


def config_dir() -> Path:
    """Directory holding ``hardware.yaml`` and ``local.yaml``.

    Honours ``ROBOT_CONFIG_DIR``; otherwise ``./config`` relative to the current working
    directory, which is the checkout on a development machine and ``WorkingDirectory=``
    for the systemd units on the robot.
    """
    env = os.environ.get("ROBOT_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / "config"


def runtime_dir() -> Path:
    """Sockets and pid files. ``/run/robot`` on the robot, a temp dir elsewhere."""
    env = os.environ.get("ROBOT_RUNTIME_DIR")
    if env:
        return Path(env)
    candidate = Path("/run/robot")
    if candidate.is_dir() and os.access(candidate, os.W_OK):
        return candidate
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "robot"
