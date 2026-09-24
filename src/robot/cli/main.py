"""``robot``: one entry point for running, testing, provisioning and calibrating the robot."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer

from robot import __version__
from robot.cli.audio_cmd import audio_app
from robot.cli.autostart_cmd import autostart_app
from robot.cli.common import Ctx, get_ctx
from robot.cli.hardware import calibrate_app, camera_app, display_app, safety_app
from robot.cli.llm_cmd import llm_app
from robot.cli.memory_cmd import enroll_command, memory_app
from robot.cli.openmv_cmd import openmv_app
from robot.cli.provision_cmd import provision_command
from robot.cli.status_cmd import status_command

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Desktop companion robot: simulator, services, hardware tests, provisioning.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(display_app, name="display", help="Detect and test the display")
app.add_typer(camera_app, name="camera", help="Detect and test the camera")
app.add_typer(calibrate_app, name="calibrate", help="Calibrate servos, drive and cliff sensors")
app.add_typer(safety_app, name="safety", help="Prove the motor enable interlock")
app.add_typer(memory_app, name="memory", help="List and forget people and facts")
app.add_typer(autostart_app, name="autostart", help="Start the robot when the desktop logs in")
app.add_typer(openmv_app, name="openmv", help="OpenMV camera board: flash the bridge script, probe the link")
app.add_typer(audio_app, name="audio", help="List sound devices; test microphone, speaker and speech engines")
app.add_typer(llm_app, name="llm", help="Local language model: install llama.cpp, test a conversation")
app.command("provision")(provision_command)
app.command("status")(status_command)
app.command("enroll")(enroll_command)

SERVICES = ("broker", "safety", "motion", "face", "perception", "voice", "brain", "orchestrator", "power", "openmv")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config_dir: Path | None = typer.Option(
        None, "--config-dir", help="Directory with hardware.yaml / local.yaml", envvar="ROBOT_CONFIG_DIR"
    ),
    set_: list[str] = typer.Option([], "--set", help="Override a value: --set display.backend=null"),
    log_level: str = typer.Option("INFO", "--log-level", envvar="ROBOT_LOG_LEVEL"),
    version: bool = typer.Option(False, "--version", is_eager=True),
) -> None:
    if version:
        typer.echo(f"desktop-buddy {__version__}")
        raise typer.Exit()
    ctx.obj = Ctx(config_dir=config_dir, overrides=list(set_), log_level=log_level)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def sim(
    ctx: typer.Context,
    headless: bool = typer.Option(
        False, "--headless", help="No window; run a scripted scenario and print bus statistics"
    ),
    seconds: float | None = typer.Option(None, "--seconds", help="Stop after this many seconds"),
    brain_endpoint: str | None = typer.Option(
        None, "--brain-endpoint", help="Use a real language model at this OpenAI-compatible URL"
    ),
    shape: str = typer.Option("rect", "--shape", help="Simulated panel shape: rect | round"),
    layout: str = typer.Option("A", "--layout", help="Simulated actuators: A | B | C | none"),
) -> None:
    """Run the whole stack on this machine with a simulated desk, person and hardware."""
    c = get_ctx(ctx)
    if layout != "none":
        c.layout = layout
    c.overrides += [
        "display.backend=null",
        f"display.shape={shape}",
        "camera.backend=null",
        "voice.wake.engine=fake",
        "voice.stt.engine=fake",
        "voice.tts.engine=fake",
    ]
    if brain_endpoint:
        c.overrides += [f"brain.endpoint={brain_endpoint}", "brain.backend=openai_compat"]
    elif not any(o.startswith("brain.") for o in c.overrides):
        c.overrides.append("brain.backend=scripted")
    if headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from robot.sim.app import run_simulator

    raise typer.Exit(run_simulator(c.config(), headless=headless, seconds=seconds))


@app.command()
def run(
    ctx: typer.Context,
    service: str = typer.Argument(..., help="One of: " + ", ".join(SERVICES) + ", all"),
    mock: bool = typer.Option(False, "--mock", help="Force mock GPIO/I2C (develop motion on a laptop)"),
    enable_mode: str | None = typer.Option(
        None, "--enable-mode", help="Override safety.enable_mode: level | pulse (see HARDWARE.md 5.3)"
    ),
) -> None:
    """Run one service process (what the systemd units call), or `all` for a quick full start."""
    c = get_ctx(ctx)
    cfg = c.config()
    if service == "all":
        raise typer.Exit(_run_all(cfg, c, mock=mock, enable_mode=enable_mode))
    if service == "broker":
        from robot.core.broker import Broker

        broker = Broker(cfg.bus.xsub, cfg.bus.xpub)
        signal.signal(signal.SIGTERM, lambda *_: broker.stop())
        signal.signal(signal.SIGINT, lambda *_: broker.stop())
        broker.serve_forever()
        return
    from robot.core.bus import ZmqBusClient
    from robot.core.service import Service, install_signal_handlers

    bus = ZmqBusClient(service, cfg.bus.xsub, cfg.bus.xpub)
    svc: Service
    if service == "face":
        from robot.face.service import FaceService

        svc = FaceService(cfg, bus)
    elif service == "perception":
        from robot.perception.service import PerceptionService

        svc = PerceptionService(cfg, bus)
    elif service == "voice":
        from robot.voice.service import VoiceService

        svc = VoiceService(cfg, bus)
    elif service == "brain":
        from robot.brain.service import BrainService

        svc = BrainService(cfg, bus)
    elif service == "motion":
        from robot.motion.service import MotionService

        svc = MotionService(cfg, bus, mock=True if mock else None)
    elif service == "safety":
        from robot.hal.gpio.mock import MockGpioBackend
        from robot.safety.service import SafetyService

        svc = SafetyService(cfg, bus, gpio=MockGpioBackend() if mock else None, enable_mode=enable_mode)
    elif service == "orchestrator":
        from robot.orchestrator.service import OrchestratorService

        svc = OrchestratorService(cfg, bus)
    elif service == "power":
        from robot.cli.power_service import PowerService

        svc = PowerService(cfg, bus)
    elif service == "openmv":
        from robot.openmv.service import OpenMvService

        svc = OpenMvService(cfg, bus)
    else:
        typer.secho(f"unknown service {service!r}; choose from {', '.join(SERVICES)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    install_signal_handlers(svc)
    svc.run()


def child_argv(c: Ctx, service: str, *, mock: bool = False, enable_mode: str | None = None) -> list[str]:
    """Command line for one service process, rebuilt from the parsed context rather than
    copied from sys.argv so every ``--set key=value`` keeps its value."""
    argv = [sys.executable, "-m", "robot.cli.main", "--log-level", c.log_level]
    if c.config_dir is not None:
        argv += ["--config-dir", str(c.config_dir)]
    for override in c.overrides:
        argv += ["--set", override]
    argv += ["run", service]
    if mock:
        argv.append("--mock")
    if enable_mode:
        argv += ["--enable-mode", enable_mode]
    return argv


def _run_all(cfg: object, c: Ctx, *, mock: bool = False, enable_mode: str | None = None) -> int:
    """Spawn broker + every service as child processes; forward Ctrl-C."""
    procs: list[subprocess.Popen[bytes]] = []
    names: dict[int, str] = {}
    for name in SERVICES:
        if name in ("power", "openmv") and not getattr(getattr(cfg, name, None), "enabled", False):
            continue
        proc = subprocess.Popen(child_argv(c, name, mock=mock, enable_mode=enable_mode))
        names[proc.pid] = name
        procs.append(proc)
        time.sleep(0.3 if name == "broker" else 0.05)
    typer.echo(f"started {len(procs)} processes; Ctrl-C to stop")
    reported: set[int] = set()
    try:
        while True:
            for p in procs:
                if p.poll() is not None and p.pid not in reported:
                    reported.add(p.pid)
                    typer.secho(f"service {names[p.pid]} exited with {p.returncode}", fg=typer.colors.YELLOW)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check Python, config, models, HailoRT, camera, audio, I2C, GPIO, display, thermals."""
    from robot.doctor import FAIL, PASS, WARN, run_all

    cfg = get_ctx(ctx).config()
    results = run_all(cfg)
    colors = {PASS: typer.colors.GREEN, WARN: typer.colors.YELLOW, FAIL: typer.colors.RED}
    width = max(len(r.name) for r in results)
    for r in results:
        status = typer.style(f"{r.status:4s}", fg=colors.get(r.status, typer.colors.WHITE), bold=True)
        typer.echo(f"{status}  {r.name:{width}s}  {r.detail}")
    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    typer.echo(f"\n{fails} failed, {warns} warnings")
    raise typer.Exit(1 if fails else 0)


config_app = typer.Typer(help="Inspect the merged configuration")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show(ctx: typer.Context, section: str | None = typer.Argument(None)) -> None:
    """Print the effective configuration (defaults + hardware.yaml + local.yaml + env + --set)."""
    import yaml

    cfg = get_ctx(ctx).config()
    data = cfg.model_dump(mode="json")
    if section:
        data = data.get(section, {})
    typer.echo(yaml.safe_dump(data, sort_keys=False))


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dotted setting, e.g. openmv.enabled or camera.backend"),
    value: str = typer.Argument(..., help="New value: true, 320, bus, '#ff0000' ..."),
) -> None:
    """Write one setting into config/local.yaml (validated; a bad value is rolled back)."""
    from robot.core import paths
    from robot.core.config import _coerce_scalar, load_config, write_local_setting

    c = get_ctx(ctx)
    cdir = c.config_dir or paths.config_dir()
    local = cdir / "local.yaml"
    before = local.read_text() if local.exists() else None
    path = write_local_setting(key, _coerce_scalar(value), config_dir=cdir)
    try:
        cfg = load_config(config_dir=cdir, use_env=False)
    except ValueError as exc:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before)
        typer.secho(f"rejected: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    section, _, rest = key.partition(".")
    node: object = getattr(cfg, section, None)
    for part in rest.split(".") if rest else []:
        node = getattr(node, part, None) if node is not None else None
    typer.secho(f"{key} = {node!r}  (written to {path})", fg=typer.colors.GREEN)


@config_app.command("path")
def config_path(ctx: typer.Context) -> None:
    """Show where configuration, data and models live."""
    from robot.core import paths

    get_ctx(ctx)
    typer.echo(f"config dir : {paths.config_dir()}")
    typer.echo(f"data dir   : {paths.data_dir()}")
    typer.echo(f"models dir : {paths.models_dir()}")
    typer.echo(f"runtime dir: {paths.runtime_dir()}")


@app.command()
def say(ctx: typer.Context, text: str = typer.Argument(...)) -> None:
    """Publish voice.say on the bus of a running robot."""
    _publish(ctx, "voice.say", {"text": text, "request_id": "cli"})


@app.command()
def expression(ctx: typer.Context, name: str = typer.Argument(..., help="e.g. happiness, surprise, asleep")) -> None:
    """Publish face.expression on the bus of a running robot."""
    from robot.face import expressions

    try:
        expressions.resolve(name)
    except KeyError:
        typer.secho(f"unknown expression; choose from: {', '.join(expressions.names())}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None
    _publish(ctx, "face.expression", {"name": name})


def _publish(ctx: typer.Context, topic: str, data: dict[str, object]) -> None:
    from robot.core.bus import ZmqBusClient

    cfg = get_ctx(ctx).config()
    bus = ZmqBusClient("cli", cfg.bus.xsub, cfg.bus.xpub)
    time.sleep(0.2)  # let the PUB socket connect to the broker
    bus.publish(topic, data)
    time.sleep(0.2)
    bus.close()


@app.command()
def bench(ctx: typer.Context, what: str = typer.Argument(..., help="face | llm | camera")) -> None:
    """Measure frame rate of the face renderer, tokens/s of the language model, or camera fps."""
    cfg = get_ctx(ctx).config()
    if what == "face":
        from robot.cli.hardware import bench_face

        bench_face(cfg)
    elif what == "llm":
        from robot.brain.client import ChatClient

        c = ChatClient(cfg.brain.endpoint, cfg.brain.model, cfg.brain.api_key, cfg.brain.timeout_s)
        if not c.healthy():
            typer.secho(f"{cfg.brain.endpoint} unreachable", fg=typer.colors.RED)
            raise typer.Exit(1)
        res = c.chat(
            [{"role": "user", "content": "Describe a desk lamp in three sentences."}], max_tokens=96, json_mode=False
        )
        toks = res.completion_tokens or max(1, len(res.text.split()))
        typer.echo(f"{toks} tokens in {res.latency_s:.1f}s = {toks / res.latency_s:.1f} tok/s (model {res.model})")
        typer.echo(res.text.strip())
    elif what == "camera":
        from robot.cli.hardware import camera_bench

        camera_bench(ctx, seconds=5.0)
    else:
        raise typer.BadParameter("choose face, llm or camera")


if __name__ == "__main__":
    app()
