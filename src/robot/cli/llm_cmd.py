"""``robot llm``: install the local language model server (llama.cpp) and try it.

``install`` fetches the prebuilt ``llama-server`` for Linux arm64 from llama.cpp's releases,
checks it runs on this machine, and points the brain at it. ``--build`` compiles from source
instead (10 to 15 minutes on a Pi 5) when the prebuilt one will not run. ``test`` starts the
server exactly as the brain service does, asks one question and reports speed and memory.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import httpx
import typer

from robot.cli.common import get_ctx
from robot.core import paths

llm_app = typer.Typer(help="Local language model: install llama.cpp, test a conversation")

RELEASES = "https://github.com/ggml-org/llama.cpp/releases"
SOURCE = "https://github.com/ggml-org/llama.cpp.git"
_TAG_RE = re.compile(r"/tag/([^/\s]+)$")


def install_dir() -> Path:
    return paths.data_dir() / "llama"


def asset_name(tag: str, arch: str = platform.machine()) -> str:
    """The CPU build's file name in a release: ``llama-<tag>-bin-ubuntu-<arm64|x64>.tar.gz``."""
    cpu = "arm64" if arch in ("aarch64", "arm64") else "x64"
    return f"llama-{tag}-bin-ubuntu-{cpu}.tar.gz"


def tag_from_location(location: str) -> str | None:
    m = _TAG_RE.search(location.strip())
    return m.group(1) if m else None


def resolve_nightly_tag(client: httpx.Client) -> str:
    """llama.cpp's ``/releases/latest`` is a versioned release without binaries; it carries a
    ``nightly-tag.txt`` naming the build tag (``b11146``) whose release has them."""
    r = client.head(f"{RELEASES}/latest", follow_redirects=False)
    latest = tag_from_location(r.headers.get("location", "")) if r.status_code in (301, 302) else None
    if latest is None:
        raise RuntimeError(f"could not resolve the latest release (HTTP {r.status_code})")
    if latest.startswith("b") and latest[1:].isdigit():
        return latest
    r2 = client.get(f"{RELEASES}/download/{latest}/nightly-tag.txt", follow_redirects=True)
    if r2.status_code == 200 and r2.text.strip().startswith("b"):
        return r2.text.strip()
    raise RuntimeError(f"release {latest} has no nightly-tag.txt; pass --tag bNNNNN (see {RELEASES})")


def find_binary(root: Path, name: str = "llama-server") -> Path | None:
    hits = sorted(p for p in root.rglob(name) if p.is_file())
    return hits[0] if hits else None


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    if sys.version_info >= (3, 12):
        tar.extractall(dest, filter="data")
        return
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest.resolve())):
            raise RuntimeError(f"unsafe path in archive: {member.name}")
    tar.extractall(dest)


def _runs(binary: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out.splitlines()[-1] if out else f"exit {r.returncode}"


def _point_brain_at(ctx: typer.Context, binary: Path) -> None:
    from robot.core.config import write_local_setting

    c = get_ctx(ctx)
    cdir = c.config_dir or paths.config_dir()
    write_local_setting("brain.llama_server.binary", str(binary), config_dir=cdir)
    write_local_setting("brain.managed", "llama_server", config_dir=cdir)
    write_local_setting("brain.backend", "auto", config_dir=cdir)
    typer.secho(f"brain.managed = llama_server, binary {binary}", fg=typer.colors.GREEN)


@llm_app.command("install")
def llm_install(
    ctx: typer.Context,
    tag: str | None = typer.Option(None, "--tag", help="llama.cpp build tag, e.g. b11146; default: newest"),
    build: bool = typer.Option(False, "--build", help="Compile from source instead of downloading a build"),
    dest: Path | None = typer.Option(None, "--dir", help=f"Install directory (default {install_dir()})"),
) -> None:
    """Install llama-server (prebuilt, or --build from source) and point the brain at it."""
    target = dest or install_dir()
    target.mkdir(parents=True, exist_ok=True)
    if build:
        binary = _build_from_source(target)
    else:
        binary = _download_prebuilt(target, tag)
        ok, detail = _runs(binary)
        if not ok:
            typer.secho(f"{binary.name} does not run here: {detail}", fg=typer.colors.RED)
            typer.echo("Compiling from source instead (10 to 15 minutes on a Pi 5)...")
            binary = _build_from_source(target)
    ok, detail = _runs(binary)
    if not ok:
        typer.secho(f"llama-server still fails: {detail}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"llama-server runs: {detail}")
    _point_brain_at(ctx, binary)
    model = paths.models_dir() / "llm" / get_ctx(ctx).config().brain.model_file
    if model.exists():
        typer.echo(f"model present: {model.name}. Try: uv run robot llm test")
    else:
        typer.echo("Now download the model (about 0.8 GB): uv run robot provision --group llm")
        typer.echo("Then: uv run robot llm test")


def _download_prebuilt(target: Path, tag: str | None) -> Path:
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), headers={"User-Agent": "desktop-buddy"}) as client:
        if tag is None:
            typer.echo("finding the newest llama.cpp build...")
            tag = resolve_nightly_tag(client)
        name = asset_name(tag)
        url = f"{RELEASES}/download/{tag}/{name}"
        archive = target / name
        typer.echo(f"downloading {url}")
        with client.stream("GET", url, follow_redirects=True) as r:
            if r.status_code != 200:
                raise typer.BadParameter(f"HTTP {r.status_code} for {url}; check the tag at {RELEASES}")
            total = int(r.headers.get("content-length", "0") or 0)
            done = 0
            with archive.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        typer.echo(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", nl=False)
            typer.echo("")
    extract_to = target / tag
    if extract_to.exists():
        shutil.rmtree(extract_to)
    extract_to.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        _safe_extract(tar, extract_to)
    archive.unlink(missing_ok=True)
    binary = find_binary(extract_to)
    if binary is None:
        raise typer.BadParameter(f"no llama-server inside {name}")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    typer.echo(f"extracted to {extract_to}")
    return binary


def _build_from_source(target: Path) -> Path:
    for tool in ("git", "cmake", "c++"):
        if shutil.which(tool) is None:
            raise typer.BadParameter(f"{tool} missing: sudo apt install -y git cmake build-essential")
    src = target / "src"
    if not (src / "CMakeLists.txt").exists():
        typer.echo("cloning llama.cpp (shallow)...")
        subprocess.run(["git", "clone", "--depth", "1", SOURCE, str(src)], check=True)
    else:
        subprocess.run(["git", "-C", str(src), "pull", "--ff-only"], check=False)
    build_dir = src / "build"
    typer.echo("configuring...")
    subprocess.run(
        [
            "cmake",
            "-S",
            str(src),
            "-B",
            str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DLLAMA_CURL=OFF",
            "-DGGML_NATIVE=ON",
        ],
        check=True,
    )
    typer.echo("building llama-server (this is the slow part)...")
    jobs = str(max(1, (os.cpu_count() or 2) - 1))
    subprocess.run(["cmake", "--build", str(build_dir), "--target", "llama-server", "-j", jobs], check=True)
    binary = find_binary(build_dir)
    if binary is None:
        raise typer.BadParameter("build finished but no llama-server binary found")
    return binary


@llm_app.command("test")
def llm_test(
    ctx: typer.Context,
    prompt: str = typer.Option("Hi Buddy, what can you do?", "--prompt"),
    person: str | None = typer.Option(None, "--person", help="Pretend this person is in front of the camera"),
) -> None:
    """Start the model server the way the brain does, ask one question, report speed and memory."""
    from robot.brain.client import ChatClient
    from robot.brain.llama_server import LlamaServerManager
    from robot.brain.prompt import parse_reply, system_prompt

    cfg = get_ctx(ctx).config()
    b = cfg.brain
    manager: LlamaServerManager | None = None
    if b.managed == "llama_server":
        model = paths.models_dir() / "llm" / b.model_file
        if not model.exists():
            typer.secho(f"model missing: {model}. Run: uv run robot provision --group llm", fg=typer.colors.RED)
            raise typer.Exit(1)
        manager = LlamaServerManager(b, model)
        typer.echo(f"starting llama-server with {model.name} ({model.stat().st_size / 1e9:.2f} GB)...")
        t0 = time.monotonic()
        try:
            manager.start()
        except Exception as exc:
            typer.secho(f"cannot start llama-server: {exc}", fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        typer.echo(f"ready in {time.monotonic() - t0:.1f}s at {manager.endpoint}")
        endpoint = manager.endpoint
    else:
        endpoint = b.endpoint
        typer.echo(f"using the model server at {endpoint} (brain.managed is {b.managed})")
    client = ChatClient(endpoint, b.model, b.api_key, b.timeout_s)
    try:
        if not client.healthy():
            typer.secho("server not reachable", fg=typer.colors.RED)
            raise typer.Exit(1)
        messages = [
            {"role": "system", "content": system_prompt(b.persona, person, [])},
            {"role": "user", "content": prompt},
        ]
        typer.echo(f"you: {prompt}")
        res = client.chat(messages, max_tokens=b.max_tokens, temperature=b.temperature)
        reply = parse_reply(res.text)
        typer.secho(f"buddy: {reply.say}", fg=typer.colors.GREEN)
        typer.echo(
            f"expression: {reply.expression}  gesture: {reply.gesture or '-'}  remember: {reply.remember or '-'}"
        )
        rate = f"{res.completion_tokens / res.latency_s:.1f} tokens/s" if res.completion_tokens else "n/a"
        typer.echo(
            f"latency {res.latency_s:.1f}s, prompt {res.prompt_tokens or '?'} tokens, reply {res.completion_tokens or '?'} tokens, {rate}"
        )
        if manager is not None:
            rss = manager.rss_mb()
            if rss is not None:
                typer.echo(f"llama-server memory: {rss:.0f} MB (budget {b.rss_budget_mb} MB)")
        if res.latency_s > 10:
            typer.secho("slow: expect a long pause before answers. A smaller max_tokens helps.", fg=typer.colors.YELLOW)
    finally:
        client.close()
        if manager is not None:
            manager.stop()
