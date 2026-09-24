"""``robot display``, ``robot camera``, ``robot calibrate``, ``robot safety``."""

from __future__ import annotations

import os
import statistics
import time
from typing import TYPE_CHECKING

import typer

from robot.cli.common import get_ctx
from robot.core.config import DisplayConfig, RobotConfig, write_local_section, write_local_setting

if TYPE_CHECKING:
    from robot.hal.camera.base import Camera
    from robot.hal.openmv.link import OpenMvLink

display_app = typer.Typer(no_args_is_help=True)
camera_app = typer.Typer(no_args_is_help=True)
calibrate_app = typer.Typer(no_args_is_help=True)
safety_app = typer.Typer(no_args_is_help=True)


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------


@display_app.command("detect")
def display_detect(
    ctx: typer.Context, save: bool = typer.Option(False, "--save", help="Write the result into config/local.yaml")
) -> None:
    """Run the detection cascade and say what was found and why."""
    from robot.hal.display.detect import detect_display

    c = get_ctx(ctx)
    cfg = c.config()
    det = detect_display(cfg.display)
    typer.echo(f"backend    : {det.backend}")
    typer.echo(f"device     : {det.device or '-'}")
    typer.echo(f"size       : {det.width}x{det.height}" if det.width else "size       : unknown until opened")
    typer.echo(f"shape      : {det.shape}")
    typer.echo(f"controller : {det.controller}")
    for r in det.reasons:
        typer.echo(f"  because  : {r}")
    if save:
        path = write_local_section("display", det.as_local_config(), config_dir=c.config_dir)
        if det.session_bound:
            typer.secho(
                f"desktop window depends on this login session, so display.backend stays auto in {path}; "
                "the services will pick kms (HDMI/DSI) when they run without a desktop",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(f"saved to {path}", fg=typer.colors.GREEN)


@display_app.command("test")
def display_test(
    ctx: typer.Context,
    pattern: bool = typer.Option(
        False, "--pattern", help="Colour bars, grid, circle and orientation glyph instead of the face"
    ),
    seconds: float = typer.Option(10.0, "--seconds"),
) -> None:
    """Show all expressions (or a test pattern) on the real display and report fps."""
    import pygame

    from robot.face import expressions
    from robot.face.animator import FaceAnimator
    from robot.face.patterns import draw_pattern
    from robot.face.renderer import FaceRenderer, PanelGeometry
    from robot.hal.display.factory import open_display
    from robot.hal.display.pipeline import DisplayPipeline

    cfg = get_ctx(ctx).config()
    pygame.init()
    disp = open_display(cfg.display)
    pipe = DisplayPipeline(cfg.display, disp)
    typer.echo(
        f"display: {disp.info.backend} {disp.info.width}x{disp.info.height} shape={disp.info.shape} color={disp.info.color} rotation={cfg.display.rotation}"
    )
    times: list[float] = []
    t_end = time.monotonic() + seconds
    if pattern:
        while time.monotonic() < t_end:
            draw_pattern(pipe.surface, round_mask=disp.info.shape == "round")
            times.append(pipe.present())
            for ev in pipe.pump_events():
                if ev.type == pygame.QUIT:
                    t_end = 0
            time.sleep(0.05)
        typer.echo(
            "Check: bars white,yellow,cyan,green,magenta,red,blue,black left to right; squares red,green,blue; 'F' upright in the top-left."
        )
    else:
        shape = disp.info.shape if cfg.display.shape == "auto" else cfg.display.shape
        color = disp.info.color if cfg.display.color == "auto" else cfg.display.color
        renderer = FaceRenderer(
            cfg.face,
            PanelGeometry(pipe.logical_w, pipe.logical_h, shape=shape, color=color, scale_mode=cfg.display.scale_mode),
        )
        anim = FaceAnimator(cfg.face)
        # every expression, then the three overlays on a neutral face
        steps = [(name, "none") for name in expressions.names()]
        steps += [("neutral", mode) for mode in ("listening", "thinking", "speaking")]
        per = max(0.8, seconds / len(steps))
        last = time.monotonic()
        for name, mode in steps:
            anim.set_expression(name)
            anim.set_mode(mode)
            typer.echo(f"  {name}" + (f" + {mode}" if mode != "none" else ""))
            until = time.monotonic() + per
            while time.monotonic() < until:
                now = time.monotonic()
                face = anim.update(now - last)
                last = now
                renderer.render(face, pipe.surface, overlay=anim.overlay())
                times.append(pipe.present())
                for ev in pipe.pump_events():
                    if ev.type == pygame.QUIT:
                        until = 0
                time.sleep(max(0.0, 1.0 / cfg.display.target_fps - (time.monotonic() - now)))
    pipe.close()
    if times:
        ms = [t * 1000 for t in times]
        typer.echo(
            f"frames {len(ms)}  present p50 {statistics.median(ms):.1f} ms  p95 {sorted(ms)[int(0.95 * (len(ms) - 1))]:.1f} ms  max {max(ms):.1f} ms"
        )


def bench_face(cfg: RobotConfig) -> None:
    import pygame

    from robot.face import expressions
    from robot.face.renderer import FaceRenderer, PanelGeometry

    pygame.init()
    for size in (240, 480, 800):
        surf = pygame.Surface((size, size))
        r = FaceRenderer(cfg.face, PanelGeometry(size, size))
        face = expressions.get("happiness")
        t0 = time.perf_counter()
        n = 60
        for _ in range(n):
            r.render(face, surf)
        dt = (time.perf_counter() - t0) / n * 1000
        typer.echo(f"{size}x{size}: {dt:.2f} ms/frame render only ({1000 / dt:.0f} fps ceiling)")


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------


@camera_app.command("detect")
def camera_detect(ctx: typer.Context, save: bool = typer.Option(False, "--save")) -> None:
    """Run the camera detection cascade."""
    from robot.hal.camera.detect import detect_camera

    c = get_ctx(ctx)
    det = detect_camera(c.config().camera)
    typer.echo(f"backend: {det.backend}   device: {det.device or '-'}")
    for r in det.reasons:
        typer.echo(f"  because: {r}")
    if save:
        path = write_local_section("camera", det.as_local_config(), config_dir=c.config_dir)
        note = " (backend only; the /dev/video node is found again at each start)" if det.backend == "uvc" else ""
        typer.secho(f"saved to {path}{note}", fg=typer.colors.GREEN)


@camera_app.command("test")
def camera_test(
    ctx: typer.Context,
    seconds: float = typer.Option(15.0, "--seconds"),
    detect: bool = typer.Option(True, "--detect/--no-detect", help="Overlay face detections"),
) -> None:
    """Live preview on the display with an fps counter (and face boxes if a backend is available)."""
    import numpy as np
    import pygame

    from robot.hal.camera.factory import open_camera
    from robot.hal.display.factory import open_display
    from robot.hal.display.pipeline import DisplayPipeline

    cfg = get_ctx(ctx).config()
    pygame.init()
    cam = open_camera(cfg.camera)
    link = None
    if cfg.openmv.enabled and cam.info.backend in ("null", "bus"):
        # no bridge service runs during this test, so talk to the board directly
        cam, link = _openmv_feed(cfg)
    if cam.info.backend == "null":
        typer.secho("no camera", fg=typer.colors.RED)
        from robot.hal.openmv.link import find_port

        if not cfg.openmv.enabled and find_port():
            typer.echo("An OpenMV board is plugged in. Enable it with:")
            typer.echo("  uv run robot config set openmv.enabled true")
            typer.echo("  uv run robot config set camera.backend bus")
        raise typer.Exit(1)
    backend = None
    if detect:
        from robot.perception.factory import open_backend

        backend = open_backend(cfg.perception)
    disp = open_display(_preview_display(cfg.display))
    pipe = DisplayPipeline(cfg.display, disp)
    font = pygame.font.SysFont(None, 20)
    frames = 0
    t0 = time.monotonic()
    t_end = t0 + seconds
    while time.monotonic() < t_end:
        frame = cam.read(timeout=0.5)
        if frame is None:
            time.sleep(0.005)
            for ev in pipe.pump_events():
                if ev.type == pygame.QUIT:
                    t_end = 0
            continue
        frames += 1
        img = frame.image
        if cfg.camera.mirror_preview:
            img = np.ascontiguousarray(img[:, ::-1])
        surf = pygame.image.frombuffer(img.tobytes(), (frame.width, frame.height), "RGB")
        surf = pygame.transform.smoothscale(surf, (pipe.logical_w, pipe.logical_h))
        pipe.surface.blit(surf, (0, 0))
        if backend is not None:
            sx, sy = pipe.logical_w / frame.width, pipe.logical_h / frame.height
            for d in backend.detector.detect(frame.image):
                x1, y1, x2, y2 = d.bbox
                if cfg.camera.mirror_preview:
                    x1, x2 = frame.width - x2, frame.width - x1
                pygame.draw.rect(
                    pipe.surface,
                    (60, 255, 120),
                    pygame.Rect(int(x1 * sx), int(y1 * sy), int((x2 - x1) * sx), int((y2 - y1) * sy)),
                    2,
                )
        fps = frames / max(1e-6, time.monotonic() - t0)
        pipe.surface.blit(
            font.render(
                f"{cam.info.backend} {frame.width}x{frame.height} {cam.info.format} {fps:.1f} fps", True, (255, 255, 0)
            ),
            (6, 6),
        )
        pipe.present()
        for ev in pipe.pump_events():
            if ev.type == pygame.QUIT:
                t_end = 0
    pipe.close()
    cam.close()
    if link is not None:
        link.close()
    typer.echo(f"{frames} frames, {frames / max(1e-6, time.monotonic() - t0):.1f} fps")
    if frames == 0 and link is not None:
        typer.secho(
            "no frames from the OpenMV. Is it showing eyes (script running)? Try `robot openmv probe`.",
            fg=typer.colors.YELLOW,
        )


def _openmv_feed(cfg: RobotConfig) -> tuple[Camera, OpenMvLink]:
    """A bus camera fed straight from the board's serial port, for one-off tests."""
    from robot.hal.camera.bus import BusCamera
    from robot.hal.openmv.link import OpenMvLink

    o = cfg.openmv
    cam = BusCamera(o.width, o.height)
    cam.open()
    link = OpenMvLink(
        None if o.port == "auto" else o.port,
        on_frame=lambda w, h, seq, jpeg: cam.push({"width": w, "height": h, "seq": seq, "jpeg": jpeg}),
        on_log=lambda text: typer.echo(f"board: {text}"),
    )
    try:
        link.open()
    except Exception as exc:
        typer.secho(f"OpenMV: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    link.send_config(o.width, o.height, o.jpeg_quality, o.fps, o.leds)
    typer.echo(f"OpenMV on {link.port_name}: {o.width}x{o.height} @ {o.fps} fps requested")
    return cam, link


def _preview_display(display: DisplayConfig) -> DisplayConfig:
    """The camera preview needs a real screen; a `bus` (OpenMV LCD) display cannot show it."""
    if display.backend == "bus":
        return display.model_copy(update={"backend": "auto", "width": None, "height": None})
    return display


@camera_app.command("bench")
def camera_bench(ctx: typer.Context, seconds: float = typer.Option(5.0, "--seconds")) -> None:
    """Measured fps, frame latency and dropped frames, without a display."""
    from robot.hal.camera.factory import open_camera

    cfg = get_ctx(ctx).config()
    cam = open_camera(cfg.camera)
    if cam.info.backend == "null":
        typer.secho("no camera", fg=typer.colors.RED)
        raise typer.Exit(1)
    gaps: list[float] = []
    last = None
    drops = 0
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        f = cam.read(timeout=1.0)
        if f is None:
            drops += 1
            continue
        now = time.monotonic()
        if last is not None:
            gaps.append(now - last)
        last = now
    cam.close()
    if not gaps:
        typer.secho("no frames received", fg=typer.colors.RED)
        raise typer.Exit(1)
    fps = 1.0 / statistics.mean(gaps)
    typer.echo(
        f"{cam.info.backend} {cam.info.width}x{cam.info.height} format={cam.info.format or '?'} requested {cfg.camera.fps} fps"
    )
    typer.echo(
        f"measured {fps:.1f} fps, frame interval p50 {statistics.median(gaps) * 1000:.1f} ms, max {max(gaps) * 1000:.1f} ms, timeouts {drops}"
    )
    for n in cam.info.notes:
        typer.secho(f"note: {n}", fg=typer.colors.YELLOW)
    if cam.info.format == "YUYV" and fps < cfg.camera.fps * 0.7:
        typer.secho(
            "The camera negotiated YUYV. Set `camera.format: MJPG` in config/local.yaml.", fg=typer.colors.YELLOW
        )


@camera_app.command("record")
def camera_record(
    ctx: typer.Context,
    out: str = typer.Argument(..., help="Output .mp4 (or .avi)"),
    seconds: float = typer.Option(120.0, "--seconds"),
) -> None:
    """Record a session to replay later with camera.backend=file (for tuning recognition)."""
    import cv2

    from robot.hal.camera.factory import open_camera

    cfg = get_ctx(ctx).config()
    cam = open_camera(cfg.camera)
    if cam.info.backend == "null":
        typer.secho("no camera", fg=typer.colors.RED)
        raise typer.Exit(1)
    writer = None
    n = 0
    t_end = time.monotonic() + seconds
    try:
        while time.monotonic() < t_end:
            f = cam.read(timeout=1.0)
            if f is None:
                continue
            if writer is None:
                fourcc = cv2.VideoWriter.fourcc(*("mp4v" if out.endswith(".mp4") else "MJPG"))
                writer = cv2.VideoWriter(out, fourcc, max(1.0, cam.info.fps or cfg.camera.fps), (f.width, f.height))
            writer.write(cv2.cvtColor(f.image, cv2.COLOR_RGB2BGR))
            n += 1
    finally:
        if writer is not None:
            writer.release()
        cam.close()
    typer.echo(f"wrote {n} frames to {out}. Replay: --set camera.backend=file --set camera.device={out}")


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


def _actuators(cfg: RobotConfig, mock: bool):  # type: ignore[no-untyped-def]
    from robot.hal.actuators.map import ActuatorMap
    from robot.hal.gpio.base import open_gpio_backend
    from robot.hal.i2c.base import open_i2c_bus

    gpio = open_gpio_backend("auto" if not mock else "mock")
    is_mock = mock or gpio.name == "mock"
    i2c = None if is_mock else open_i2c_bus(cfg.actuators.drivers.servo.i2c_bus)
    return ActuatorMap.from_config(cfg.actuators, gpio, i2c, mock=is_mock), is_mock


@calibrate_app.command("servos")
def calibrate_servos(ctx: typer.Context, mock: bool = typer.Option(False, "--mock")) -> None:
    """Jog each servo with keys, capture min / centre / max, write them to local.yaml."""
    c = get_ctx(ctx)
    cfg = c.config()
    acts, is_mock = _actuators(cfg, mock)
    if is_mock:
        typer.secho("no GPIO hardware: running against the mock driver (nothing will move)", fg=typer.colors.YELLOW)
    results: dict[str, dict[str, float]] = {}
    try:
        for role, servo in acts.servos.items():
            typer.secho(
                f"\n{servo.name} ({role}): j/k = -/+ 2 deg, J/K = -/+ 10 deg, m = mark min, c = mark centre, x = mark max, n = next",
                bold=True,
            )
            pos = 0.0
            marks: dict[str, float] = {}
            servo.cfg.min_deg, servo.cfg.max_deg = (
                -170.0,
                170.0,
            )  # calibration explores beyond the configured limits, slowly
            servo.cfg.max_deg_s = 60.0
            while True:
                servo.set_target(pos)
                for _ in range(20):
                    servo.update(0.02)
                    time.sleep(0.02)
                key = typer.prompt(f"  at {pos:+.0f} deg, marks={marks}", default="", show_default=False).strip()
                if key == "j":
                    pos -= 2
                elif key == "k":
                    pos += 2
                elif key == "J":
                    pos -= 10
                elif key == "K":
                    pos += 10
                elif key == "m":
                    marks["min"] = pos
                elif key == "c":
                    marks["center"] = pos
                elif key == "x":
                    marks["max"] = pos
                elif key == "n" or (is_mock and not key):
                    break
            if {"min", "max"} <= set(marks):
                center = marks.get("center", 0.0)
                results[servo.name] = {
                    "min_deg": marks["min"] - center,
                    "max_deg": marks["max"] - center,
                    "center_us": servo.pulse_for(center),
                }
            servo.relax()
    finally:
        acts.close()
    for name, vals in results.items():
        write_local_section("actuators", {"channels": {name: vals}}, config_dir=c.config_dir)
        typer.secho(f"{name}: {vals}", fg=typer.colors.GREEN)
    if results:
        typer.echo("written to config/local.yaml")


@calibrate_app.command("drive")
def calibrate_drive(
    ctx: typer.Context,
    distance_mm: float = typer.Option(500.0, "--distance"),
    mock: bool = typer.Option(False, "--mock"),
) -> None:
    """Drive a nominal distance, enter what it actually moved, derive ticks per millimetre."""
    c = get_ctx(ctx)
    cfg = c.config()
    typer.secho("Prop the chassis so the wheels spin free the first time you run this.", fg=typer.colors.YELLOW)
    typer.confirm("Wheels free or a clear metre of floor ahead, and the safety supervisor running?", abort=True)
    acts, is_mock = _actuators(cfg, mock)
    if acts.drive is None:
        typer.secho("no drivetrain in the actuator map", fg=typer.colors.RED)
        raise typer.Exit(1)
    d = acts.drive
    try:
        d.reset_pose()
        d.set_velocity(100.0, 0.0)
        t_end = time.monotonic() + distance_mm / 100.0
        last = time.monotonic()
        while time.monotonic() < t_end:
            now = time.monotonic()
            d.update(now - last)
            last = now
            time.sleep(0.02)
        d.stop()
        d.update(0.02)
        steps_l = d.left.encoder.steps if d.left.encoder else 0
        steps_r = d.right.encoder.steps if d.right.encoder else 0
        typer.echo(f"odometry says {d.pose.x_mm:.0f} mm; encoder steps L={steps_l} R={steps_r}")
        actual = typer.prompt("Measured distance in mm", type=float, default=distance_mm if is_mock else None)
        steps = (abs(steps_l) + abs(steps_r)) / 2.0
        if steps > 0 and actual > 0:
            tpm = steps / actual
            write_local_setting("actuators.geometry.ticks_per_mm", round(tpm, 4), config_dir=c.config_dir)
            typer.secho(f"ticks_per_mm = {tpm:.4f} written to config/local.yaml", fg=typer.colors.GREEN)
        else:
            typer.secho("no encoder ticks counted; check encoder wiring (or this is the mock)", fg=typer.colors.YELLOW)
    finally:
        acts.close()


@calibrate_app.command("cliff")
def calibrate_cliff(ctx: typer.Context, mock: bool = typer.Option(False, "--mock")) -> None:
    """Sample each range sensor on the desk and over the edge; set thresholds halfway between."""
    from robot.hal.i2c.base import MockI2cBus, open_i2c_bus
    from robot.hal.sensors.cliff import MockCliffSensors

    c = get_ctx(ctx)
    cfg = c.config()
    names = [s.name for s in cfg.safety.cliff.sensors]
    i2c = MockI2cBus() if mock else open_i2c_bus(1)
    if isinstance(i2c, MockI2cBus):
        typer.secho("no I2C: using mock readings", fg=typer.colors.YELLOW)
        sensors = MockCliffSensors(names)
    else:
        from robot.hal.sensors.cliff import Vl53l1xArray

        sensors = Vl53l1xArray(cfg.safety.cliff, i2c)  # type: ignore[assignment]

    def sample(label: str) -> dict[str, float]:
        typer.confirm(f"Place the robot {label}, then confirm", abort=True)
        acc: dict[str, list[int]] = {n: [] for n in names}
        for _ in range(20):
            for n, mm in sensors.read().items():
                acc[n].append(mm)
            time.sleep(0.05)
        return {n: statistics.median(v) for n, v in acc.items()}

    on = sample("flat on the desk with all sensors over the surface")
    if isinstance(sensors, MockCliffSensors):
        for n in names:
            sensors.set_off_edge(n)
    off = sample("so that every sensor hangs over the edge")
    new = []
    for s in cfg.safety.cliff.sensors:
        thr = int((on[s.name] + min(off[s.name], on[s.name] + 400)) / 2)
        new.append({"name": s.name, "mux_channel": s.mux_channel, "threshold_mm": thr})
        typer.echo(f"{s.name}: on desk {on[s.name]:.0f} mm, over edge {off[s.name]:.0f} mm -> threshold {thr} mm")
    write_local_section("safety", {"cliff": {"sensors": new, "enabled": True}}, config_dir=c.config_dir)
    typer.secho("written to config/local.yaml (safety.cliff.enabled: true)", fg=typer.colors.GREEN)
    sensors.close()


# ---------------------------------------------------------------------------
# safety
# ---------------------------------------------------------------------------


@safety_app.command("selftest")
def safety_selftest(ctx: typer.Context, mock: bool = typer.Option(False, "--mock")) -> None:
    """Drive the enable line low, high, pulsing, and ask you to confirm each with a multimeter."""
    from robot.hal.gpio.base import open_gpio_backend
    from robot.safety.supervisor import EnableLine

    cfg = get_ctx(ctx).config()
    gpio = open_gpio_backend("mock" if mock else "auto")
    pin = cfg.safety.enable_pin
    typer.secho(
        f"Enable line is GPIO{pin} (header pin 37). Nothing else may be wired to the motor driver STBY/nSLEEP.",
        bold=True,
    )
    typer.secho("Do this with the motor driver's motor supply DISCONNECTED.", fg=typer.colors.YELLOW)
    line = EnableLine(gpio.output(pin), mode="level")
    try:
        line.set_allowed(False)
        typer.confirm("Meter should read ~0 V on GPIO26 and on STBY. Confirm", abort=True)
        line.set_allowed(True)
        typer.confirm("Meter should read ~3.3 V on GPIO26 and STBY high (driver enabled). Confirm", abort=True)
        line.set_allowed(False)
        line.close()
        line = EnableLine(gpio.output(pin), mode="pulse", heartbeat_hz=cfg.safety.heartbeat_hz)
        line.set_allowed(True)
        typer.echo(
            f"Pulsing at {cfg.safety.heartbeat_hz:.0f} Hz. With the pulse watchdog circuit fitted, STBY should read high and steady; without it, a meter shows ~1.6 V average."
        )
        typer.confirm("Confirm", abort=True)
    finally:
        line.close()
    typer.secho("selftest complete", fg=typer.colors.GREEN)


@safety_app.command("killtest")
def safety_killtest(
    ctx: typer.Context,
    readback_pin: int | None = typer.Option(
        None, "--readback-pin", help="GPIO wired to STBY to read the actual level; default: read GPIO26 itself"
    ),
    mock: bool = typer.Option(False, "--mock"),
) -> None:
    """Start a supervisor process, SIGKILL it, measure how long the enable line stays high.

    This is the test the whole safety story depends on. On a Raspberry Pi a GPIO keeps its last
    level when its owner is killed, so in `level` mode this test is EXPECTED TO FAIL unless
    the hardware pulse watchdog is fitted and `--enable-mode pulse` is used.
    """
    import subprocess
    import sys

    from robot.hal.gpio.base import open_gpio_backend

    cfg = get_ctx(ctx).config()
    pin = cfg.safety.enable_pin
    if mock:
        typer.secho("mock mode: simulating the dead-process case on the mock backend", fg=typer.colors.YELLOW)
        typer.echo("RESULT: in level mode a killed process cannot release the line: the mock reports it stays HIGH.")
        typer.echo("Fit the pulse watchdog (HARDWARE.md section 5.3) and run the supervisor with --enable-mode pulse.")
        raise typer.Exit(1)
    script = (
        "import time,sys\n"
        "from robot.hal.gpio.base import open_gpio_backend\n"
        f"g=open_gpio_backend('gpiozero'); o=g.output({pin}, initial=True)\n"
        "sys.stdout.write('high\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line.startswith("high"):
        typer.secho("child failed to drive the line", fg=typer.colors.RED)
        raise typer.Exit(1)
    time.sleep(0.5)
    proc.kill()
    proc.wait()
    t_kill = time.monotonic()
    gpio = open_gpio_backend("gpiozero")
    probe = gpio.input(readback_pin if readback_pin is not None else pin)
    level = probe.read()
    t_low: float | None = None
    deadline = t_kill + 2.0
    while time.monotonic() < deadline:
        if not probe.read():
            t_low = time.monotonic()
            break
        time.sleep(0.005)
    probe.close()
    if t_low is not None:
        typer.secho(
            f"PASS: line fell within {1000 * (t_low - t_kill):.0f} ms of SIGKILL (initial read after kill: {'high' if level else 'low'})",
            fg=typer.colors.GREEN,
        )
        raise typer.Exit(0)
    typer.secho("FAIL: enable line still HIGH 2 s after the supervisor was killed.", fg=typer.colors.RED)
    typer.echo(
        "This is the expected result for a bare GPIO-to-STBY wire. Fit the pulse watchdog circuit (docs/HARDWARE.md 5.3) and run `robot run safety --enable-mode pulse`."
    )
    raise typer.Exit(1)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")
