"""``robot status``: listen to the running robot's bus for a few seconds and say what every
service is doing. The first thing to run when the face looks stuck."""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

import typer

from robot.cli.common import get_ctx


def status_command(
    ctx: typer.Context,
    seconds: float = typer.Option(4.0, "--seconds", help="How long to listen"),
    raw: bool = typer.Option(False, "--raw", help="Also print every message topic as it arrives"),
) -> None:
    """Snapshot of the running robot: services alive, states, rates. Needs `robot run all` running."""
    from robot.core.bus import ZmqBusClient

    cfg = get_ctx(ctx).config()
    bus = ZmqBusClient("status", cfg.bus.xsub, cfg.bus.xpub)
    bus.subscribe("")
    time.sleep(0.3)  # let the subscription reach the broker
    counts: Counter[str] = Counter()
    latest: dict[str, dict[str, object]] = {}
    heartbeats: dict[str, dict[str, Any]] = {}
    events: defaultdict[str, list[str]] = defaultdict(list)
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        env = bus.recv(timeout=0.2)
        if env is None:
            continue
        counts[env.topic] += 1
        latest[env.topic] = env.data
        if raw:
            typer.echo(f"  {env.src:>12}  {env.topic}")
        if env.topic == "service.heartbeat":
            heartbeats[str(env.data.get("name"))] = env.data
        elif env.topic in ("voice.wake", "voice.listening", "voice.speaking", "voice.transcript", "brain.response"):
            key = env.data.get("state") or env.data.get("text") or env.data.get("say") or env.data.get("word")
            events[env.topic].append(str(key)[:60])
    bus.close()

    if not counts:
        typer.secho(
            "nothing on the bus. Is the robot running (`robot run all` or the autostart)? Same config dir?",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    expected = ("broker", "safety", "motion", "face", "perception", "voice", "brain", "orchestrator")
    typer.secho("services", bold=True)
    for name in expected:
        hb = heartbeats.get(name)
        if name == "broker":
            continue  # the broker has no heartbeat; messages arriving proves it
        if hb is None:
            typer.secho(f"  {name:<13} no heartbeat in {seconds:.0f}s  <-- dead or hung?", fg=typer.colors.RED)
        else:
            rss = hb.get("rss_mb")
            typer.echo(
                f"  {name:<13} alive  pid {hb.get('pid')}  up {float(hb.get('uptime_s', 0)):.0f}s"
                + (f"  {float(rss):.0f} MB" if rss else "")
            )
    for name, hb in heartbeats.items():
        if name not in expected:
            typer.echo(f"  {name:<13} alive  pid {hb.get('pid')}  up {float(hb.get('uptime_s', 0)):.0f}s")

    typer.secho("state", bold=True)
    o = latest.get("orchestrator.state")
    typer.echo(
        f"  orchestrator  {o.get('state') if o else 'no update seen'}"
        + (f"  person={o.get('person')}" if o and o.get("person") else "")
    )
    f = latest.get("face.state")
    if f:
        typer.echo(
            f"  face          {f.get('expression')}  mode={f.get('mode')}  {f.get('fps')} fps  {f.get('backend')}"
        )
    s = latest.get("safety.state")
    if s:
        typer.echo(f"  safety        {'ENABLED' if s.get('enabled') else 'DISABLED'}  {s.get('reason', '')}")
    om = latest.get("openmv.state")
    if om:
        typer.echo(
            f"  openmv        {'connected' if om.get('connected') else 'NOT connected'}  camera {om.get('camera_fps')} fps  lcd {om.get('lcd_fps')} fps"
        )
    faces = counts.get("perception.faces", 0)
    typer.echo(
        f"  perception    {faces / seconds:.1f} face reports/s" + ("" if faces else "  (no faces seen or no camera)")
    )
    frames = counts.get("camera.frame", 0)
    if frames:
        typer.echo(f"  camera        {frames / seconds:.1f} frames/s over the bus")

    if events:
        typer.secho(f"voice and brain events in {seconds:.0f}s", bold=True)
        for topic, items in events.items():
            typer.echo(f"  {topic:<17} {len(items)}: {', '.join(items[-5:])}")
        wakes = len(events.get("voice.wake", []))
        if wakes >= 3:
            typer.secho(
                f"  {wakes} wake triggers in {seconds:.0f}s: the room is noisy or the robot hears itself. Try "
                "`robot config set voice.listen_on_presence false` or raise voice.after_speech_guard_ms.",
                fg=typer.colors.YELLOW,
            )
    typer.secho("traffic", bold=True)
    for topic, n in counts.most_common(8):
        typer.echo(f"  {topic:<22} {n / seconds:6.1f}/s")
