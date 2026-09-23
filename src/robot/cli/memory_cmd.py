"""``robot memory ...`` and ``robot enroll``."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer

from robot.cli.common import get_ctx
from robot.memory import Memory

memory_app = typer.Typer(no_args_is_help=True)


@memory_app.command("list")
def memory_list(ctx: typer.Context) -> None:
    """People the robot knows, with sample counts and last-seen times."""
    cfg = get_ctx(ctx).config()
    m = Memory(cfg.memory_path())
    people = m.list_people()
    if not people:
        typer.echo("nobody enrolled yet (robot enroll --name ...)")
    for p in people:
        seen = dt.datetime.fromtimestamp(p.last_seen).strftime("%Y-%m-%d %H:%M") if p.last_seen else "never"
        typer.echo(
            f"{p.name:20s} samples={m.embedding_count(p.name):3d} seen={p.seen_count:4d} last={seen} facts={len(m.facts(p.name, 100))}"
        )
    m.close()


@memory_app.command("facts")
def memory_facts(ctx: typer.Context, name: str | None = typer.Option(None, "--name")) -> None:
    """Remembered facts, for one person or general."""
    cfg = get_ctx(ctx).config()
    m = Memory(cfg.memory_path())
    for f in m.facts(name, 100):
        typer.echo(f"- {f}")
    m.close()


@memory_app.command("forget")
def memory_forget(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="Hard delete one person"),
    all_: bool = typer.Option(False, "--all", help="Hard delete everyone"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
) -> None:
    """Delete stored faces and facts. This cannot be undone."""
    cfg = get_ctx(ctx).config()
    if not name and not all_:
        raise typer.BadParameter("give --name or --all")
    if not yes:
        what = "EVERYONE" if all_ else name
        typer.confirm(f"Permanently forget {what}?", abort=True)
    m = Memory(cfg.memory_path())
    if all_:
        n = m.forget_all()
        typer.echo(f"forgot {n} people")
    else:
        assert name is not None
        typer.echo("forgotten" if m.forget(name) else f"no one called {name!r}")
    m.log_event("forget", "all" if all_ else str(name))
    m.close()


def enroll_command(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Who this is"),
    samples: int = typer.Option(5, "--samples", help="Embeddings to capture from the camera"),
    images: Path | None = typer.Option(None, "--images", help="Directory of photos instead of the camera"),
) -> None:
    """Teach the robot a face: five captures while you turn your head, or a folder of photos."""
    cfg = get_ctx(ctx).config()
    from robot.perception.factory import open_backend

    backend = open_backend(cfg.perception)
    if backend is None:
        typer.secho(
            "no perception backend available (run `robot provision --group vision-cpu` or fix HailoRT)",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    m = Memory(cfg.memory_path())
    try:
        if images is not None:
            from robot.perception.enroll import enroll_from_images

            files = sorted(p for p in images.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
            n = enroll_from_images(files, backend, m, name)
        else:
            from robot.hal.camera.factory import open_camera
            from robot.perception.enroll import enroll_from_camera

            cam = open_camera(cfg.camera)
            if cam.info.backend == "null":
                typer.secho("no camera found", fg=typer.colors.RED)
                raise typer.Exit(1)
            try:
                n = enroll_from_camera(cam, backend, m, name, samples=samples, prompt=typer.echo)
            finally:
                cam.close()
        color = typer.colors.GREEN if n else typer.colors.RED
        typer.secho(f"stored {n} embeddings for {name} ({backend.name})", fg=color)
        raise typer.Exit(0 if n else 1)
    finally:
        m.close()
        backend.close()
