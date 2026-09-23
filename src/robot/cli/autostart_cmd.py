"""``robot autostart``: start the robot when the Pi's desktop logs in.

Two ways to start at boot exist and they exclude each other:

* **Console boot** (the finished robot): ``sudo deploy/install.sh`` installs systemd units that
  draw the face straight to HDMI/DSI. Needs the Pi set to boot to the console.
* **Desktop boot** (while building, on the Desktop edition of Raspberry Pi OS): this command
  writes a freedesktop autostart entry into ``~/.config/autostart`` so ``robot run all`` opens
  as a fullscreen window when the desktop session starts. No root, no systemd.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from robot.cli.common import get_ctx
from robot.core import paths
from robot.core.config import write_local_setting

autostart_app = typer.Typer(help="Start the robot automatically when the desktop logs in")

ENTRY_NAME = "desktop-buddy.desktop"


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def entry_path() -> Path:
    return autostart_dir() / ENTRY_NAME


def robot_binary() -> Path:
    """The ``robot`` launcher next to the interpreter running this command."""
    candidate = Path(sys.executable).with_name("robot")
    if candidate.exists():
        return candidate
    found = shutil.which("robot")
    if found:
        return Path(found)
    raise typer.BadParameter("cannot find the `robot` launcher; run this through `uv run robot autostart install`")


def desktop_session() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def systemd_units_enabled() -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-enabled", "robot.target"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out == "enabled"


def render_entry(binary: Path, config_dir: Path, log_file: Path) -> str:
    # `sh -c` so the log redirect works; Exec has no shell of its own.
    cmd = f"{binary} --config-dir {config_dir} run all >> {log_file} 2>&1"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Desktop Buddy\n"
        "Comment=Companion robot: face, perception, voice\n"
        f"Exec=sh -c '{cmd}'\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "StartupNotify=false\n"
    )


@autostart_app.command("install")
def autostart_install(
    ctx: typer.Context,
    windowed: bool = typer.Option(False, "--windowed", help="Open a normal window instead of covering the screen"),
) -> None:
    """Register the robot to start as a fullscreen window when this user's desktop logs in."""
    c = get_ctx(ctx)
    if systemd_units_enabled():
        typer.secho(
            "robot.target (the console-boot systemd units) is enabled. Both starting at once would fight over the "
            "screen; disable it first: sudo systemctl disable --now robot.target",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if not desktop_session():
        typer.secho(
            "no desktop session here. If this Pi boots to the desktop, run this from a terminal on that desktop; "
            "if it boots to the console, use `sudo deploy/install.sh` instead.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    config_dir = c.config_dir or paths.config_dir()
    write_local_setting("display.fullscreen", not windowed, config_dir=config_dir)
    log_file = paths.data_dir() / "autostart.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    path = entry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_entry(robot_binary(), config_dir, log_file))
    typer.secho(f"installed {path}", fg=typer.colors.GREEN)
    typer.echo(f"log: {log_file}")
    typer.echo("The robot starts at the next desktop login. Make sure the Pi auto-logs in to the desktop:")
    typer.echo("  sudo raspi-config  ->  System Options  ->  Boot / Auto Login  ->  Desktop Autologin")
    typer.echo("Stop a running robot with `robot autostart stop`; remove with `robot autostart remove`.")


@autostart_app.command("remove")
def autostart_remove(ctx: typer.Context) -> None:
    """Stop starting the robot at desktop login (does not stop a running robot)."""
    c = get_ctx(ctx)
    path = entry_path()
    if path.exists():
        path.unlink()
        typer.secho(f"removed {path}", fg=typer.colors.GREEN)
    else:
        typer.echo("no autostart entry installed")
    write_local_setting("display.fullscreen", False, config_dir=c.config_dir or paths.config_dir())


@autostart_app.command("status")
def autostart_status() -> None:
    """Say whether desktop autostart or the systemd units are active."""
    typer.echo(f"desktop autostart : {'installed' if entry_path().exists() else 'not installed'} ({entry_path()})")
    typer.echo(f"systemd units     : {'enabled' if systemd_units_enabled() else 'not enabled'} (robot.target)")
    running = _robot_pids()
    typer.echo(f"running           : {len(running)} robot process(es)" + (f" pids {running}" if running else ""))


@autostart_app.command("stop")
def autostart_stop() -> None:
    """Stop a robot started by autostart (or any `robot run` in this session)."""
    pids = _robot_pids()
    if not pids:
        typer.echo("no robot processes running")
        return
    for pid in pids:
        with __import__("contextlib").suppress(ProcessLookupError):
            os.kill(pid, 15)
    typer.secho(f"sent SIGTERM to {len(pids)} process(es)", fg=typer.colors.GREEN)


def _robot_pids() -> list[int]:
    """PIDs of `robot run ...` processes owned by this user, via /proc (no psutil dependency)."""
    me = os.getuid()
    out: list[int] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != me:
                continue
            cmd = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        text = [c.decode(errors="ignore") for c in cmd]
        if "run" in text and any(t.endswith("robot") or t.endswith("robot.cli.main") for t in text):
            out.append(int(proc.name))
    return sorted(out)
