"""Logging setup shared by every process."""

from __future__ import annotations

import logging
import os
import sys


def setup_logging(level: str | int | None = None, *, name: str | None = None) -> None:
    """Configure root logging once. journald gets plain lines, terminals get timestamps."""
    if level is None:
        level = os.environ.get("ROBOT_LOG_LEVEL", "INFO")
    under_systemd = "JOURNAL_STREAM" in os.environ or "INVOCATION_ID" in os.environ
    prefix = f"{name} " if name else ""
    fmt = f"{prefix}%(levelname)s %(name)s: %(message)s"
    if not under_systemd:
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
