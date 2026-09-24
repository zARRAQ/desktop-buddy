"""``robot openmv``: put the bridge program on the board, then prove the link works."""

from __future__ import annotations

import os
import shutil
import statistics
import time
from pathlib import Path

import typer

from robot.cli.common import get_ctx

openmv_app = typer.Typer(help="OpenMV camera board over USB: flash the bridge script, probe the link")

SCRIPT = Path(__file__).resolve().parents[3] / "openmv" / "main.py"


def find_openmv_drive(roots: list[Path] | None = None) -> Path | None:
    """The OpenMV's flash drive as the desktop auto-mounts it: a small FAT volume holding
    ``main.py`` and OpenMV's ``README.txt``, usually under /media/<user>/."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    roots = roots or [Path("/media") / user, Path("/media"), Path("/run/media") / user, Path("/mnt")]
    for root in roots:
        if not root.is_dir():
            continue
        for cand in sorted(root.iterdir()):
            if not cand.is_dir():
                continue
            if "openmv" in cand.name.lower():
                return cand
            readme = cand / "README.txt"
            try:
                if (
                    (cand / "main.py").exists()
                    and readme.exists()
                    and "openmv" in readme.read_text(errors="ignore").lower()
                ):
                    return cand
            except OSError:
                continue
    return None


@openmv_app.command("flash")
def openmv_flash(
    drive: Path | None = typer.Option(None, "--drive", help="Mount point of the OpenMV drive; auto-detected"),
) -> None:
    """Copy openmv/main.py onto the board's flash drive (it runs at the board's next reset)."""
    if not SCRIPT.exists():
        typer.secho(f"bridge script missing at {SCRIPT}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    target = drive or find_openmv_drive()
    if target is None:
        typer.secho(
            "no OpenMV drive found. Plug the camera into the Pi over USB; a drive should appear on the desktop. "
            "If it is mounted somewhere unusual, pass --drive /path/to/it",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    dest = target / "main.py"
    if dest.exists():
        shutil.copy2(dest, target / "main.py.bak")
    shutil.copy2(SCRIPT, dest)
    os.sync()
    typer.secho(f"copied {SCRIPT.name} to {dest}", fg=typer.colors.GREEN)
    typer.echo("Now unplug the camera and plug it back in (or press its reset button). It should show two eyes.")
    typer.echo("Then: uv run robot openmv probe")


@openmv_app.command("probe")
def openmv_probe(
    ctx: typer.Context,
    seconds: float = typer.Option(5.0, "--seconds", help="How long to watch the camera stream"),
    port: str | None = typer.Option(None, "--port", help="/dev/ttyACM0 or similar; auto-detected"),
) -> None:
    """Open the link, ask the board who it is, count frames, and put a test face on its LCD."""
    import pygame

    from robot.core.config import FaceConfig
    from robot.face import expressions
    from robot.face.renderer import FaceRenderer, PanelGeometry
    from robot.hal.openmv import protocol as proto
    from robot.hal.openmv.link import OpenMvLink, find_port

    cfg = get_ctx(ctx).config()
    o = cfg.openmv
    dev = port or (o.port if o.port != "auto" else None) or find_port()
    if not dev:
        typer.secho(
            "no /dev/ttyACM* device. Is the camera plugged in? Did `robot openmv flash` run?", fg=typer.colors.RED
        )
        raise typer.Exit(1)
    sizes: list[int] = []
    pong: list[str] = []
    logs: list[str] = []
    link = OpenMvLink(
        dev,
        on_frame=lambda _w, _h, _s, j: sizes.append(len(j)),
        on_log=logs.append,
        on_pong=pong.append,
    )
    try:
        link.open()
    except Exception as exc:
        typer.secho(f"cannot open {dev}: {exc}", fg=typer.colors.RED)
        typer.echo("If it says permission denied: sudo usermod -aG dialout $USER, then log out and in.")
        raise typer.Exit(1) from exc
    typer.echo(f"link open on {dev}; asking for {o.width}x{o.height} q{o.jpeg_quality} @ {o.fps} fps")
    link.send_config(o.width, o.height, o.jpeg_quality, o.fps, o.leds)
    link.ping()
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        time.sleep(0.1)
    # a test face on the LCD, held for two seconds
    pygame.init()
    panel = PanelGeometry(o.lcd_width, o.lcd_height, color="mono1")
    surf = pygame.Surface((o.lcd_width, o.lcd_height))
    FaceRenderer(FaceConfig(), panel).render(expressions.get("happiness"), surf)
    mask = proto.surface_to_mask(surf)
    fg = proto.rgb565((0x3E, 0xE0, 0xE6))
    for _ in range(3):
        link.send_face(o.lcd_width, o.lcd_height, fg, 0, proto.pack_bitmap(mask))
        time.sleep(0.7)
    link.close()
    typer.echo(f"board      : {pong[0] if pong else 'no answer to ping (old script on the board?)'}")
    fps = len(sizes) / max(seconds, 1e-6)
    typer.echo(
        f"camera     : {len(sizes)} frames in {seconds:.0f}s = {fps:.1f} fps"
        + (f", median {statistics.median(sizes) / 1024:.0f} KB" if sizes else "")
    )
    typer.echo(
        f"link       : {link.bytes_in / 1024 / max(seconds, 1e-6):.0f} KB/s in, {link.dropped_out} writes dropped, {link.parser.resyncs} resyncs"
    )
    for line in logs[-5:]:
        typer.echo(f"board log  : {line}")
    typer.echo("LCD        : a happy face was sent three times; it should be on the screen now")
    if not sizes:
        typer.secho(
            "no frames: the board is not running the bridge script, or its camera failed. Run `robot openmv flash`, replug, retry.",
            fg=typer.colors.YELLOW,
        )
