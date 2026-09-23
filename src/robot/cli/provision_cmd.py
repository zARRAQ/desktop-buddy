"""``robot provision``."""

from __future__ import annotations

import typer

from robot.cli.common import get_ctx
from robot.provision.download import Provisioner
from robot.provision.manifest import GROUP_HELP, default_groups_for, groups, select


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def provision_command(
    ctx: typer.Context,
    list_: bool = typer.Option(False, "--list", help="Show what would be downloaded, with sizes"),
    verify: bool = typer.Option(False, "--verify", help="Checksum everything already on disk"),
    group: list[str] = typer.Option(  # noqa: B008
        [], "--group", help="Restrict to groups (repeatable). Default: what this machine needs"
    ),
    all_: bool = typer.Option(
        False, "--all", help="Every group, including all Hailo architectures and alternative LLMs"
    ),
    force: bool = typer.Option(False, "--force", help="Re-download even if present"),
) -> None:
    """Download models into the data directory. Resumable and idempotent."""
    cfg = get_ctx(ctx).config()
    if all_:
        chosen = groups()
    elif group:
        unknown = [g for g in group if g not in groups()]
        if unknown:
            typer.secho(f"unknown group(s) {unknown}; available: {', '.join(groups())}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        chosen = group
    else:
        arch = None
        if cfg.perception.backend in ("auto", "hailo"):
            from robot.perception.hailo import detect_arch

            arch = cfg.perception.hailo_arch if cfg.perception.hailo_arch != "auto" else detect_arch()
        chosen = default_groups_for(
            hailo_arch=arch, has_llm=cfg.brain.managed == "llama_server" or cfg.brain.backend != "scripted"
        )
    items = select(chosen)

    def progress(name: str, done: int, total: int | None) -> None:
        pct = f"{100 * done / total:5.1f}%" if total else ""
        typer.echo(f"\r  {name:28s} {_fmt_size(done):>9s} / {_fmt_size(total):>9s} {pct}", nl=False)

    prov = Provisioner(progress=progress)
    try:
        if list_:
            total = 0
            for g in chosen:
                typer.secho(f"\n[{g}] {GROUP_HELP.get(g, '')}", bold=True)
                for m in items:
                    if m.group != g:
                        continue
                    mark = "present" if prov.is_present(m) else "missing"
                    total += m.size or 0
                    typer.echo(f"  {m.name:28s} {_fmt_size(m.size):>9s}  {mark:8s} {m.license}  {m.note}")
            typer.echo(f"\ntotal listed: {_fmt_size(total)}  ->  {prov.models_dir}")
            return
        if verify:
            bad = 0
            for m in items:
                ok, why = prov.verify(m)
                color = typer.colors.GREEN if ok else typer.colors.RED
                typer.secho(f"  {m.name:28s} {why}", fg=color)
                bad += not ok
            raise typer.Exit(1 if bad else 0)
        for m in items:
            if not force and prov.is_present(m):
                typer.echo(f"  {m.name:28s} present")
                continue
            typer.echo(f"  {m.name:28s} downloading {m.url}")
            try:
                prov.fetch(m, force=force)
            except RuntimeError as exc:
                typer.secho(f"\n  {m.name}: {exc}", fg=typer.colors.RED)
                raise typer.Exit(1) from exc
            typer.echo("")
        typer.secho(f"done -> {prov.models_dir}", fg=typer.colors.GREEN)
    finally:
        prov.close()
