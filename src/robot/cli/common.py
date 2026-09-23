"""Shared CLI state: configuration loading from global options."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import typer

from robot.core.config import RobotConfig, load_config
from robot.core.log import setup_logging


@dataclass
class Ctx:
    config_dir: Path | None = None
    overrides: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    layout: str | None = None  # example actuator layout to merge in (simulator only)
    _config: RobotConfig | None = None

    def config(self) -> RobotConfig:
        if self._config is None:
            try:
                self._config = load_config(config_dir=self.config_dir, overrides=self.overrides, layout=self.layout)
            except ValueError as exc:
                typer.secho(f"configuration error: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(2) from exc
        return self._config


def get_ctx(ctx: typer.Context) -> Ctx:
    obj = ctx.find_root().obj
    if not isinstance(obj, Ctx):
        obj = Ctx()
        ctx.find_root().obj = obj
    setup_logging(obj.log_level)
    return obj
