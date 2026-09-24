"""``robot audio``: which microphones and speakers the Pi sees, and a round trip through
the robot's own engines (record, play back, synthesise a sentence)."""

from __future__ import annotations

import time

import numpy as np
import typer

from robot.cli.common import get_ctx

audio_app = typer.Typer(help="List sound devices and test the microphone, speaker and speech engines")


@audio_app.command("list")
def audio_list() -> None:
    """Every sound device with its index, inputs and outputs; the defaults are marked."""
    try:
        import sounddevice as sd
    except Exception as exc:
        typer.secho(f"sounddevice unavailable: {exc} (uv sync --extra voice)", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    devices = sd.query_devices()
    try:
        d_in, d_out = sd.default.device
    except Exception:
        d_in, d_out = -1, -1
    typer.echo(f"{'idx':>3}  {'in':>3} {'out':>3}  name")
    for i, d in enumerate(devices):
        mark = ("<" if i == d_in else " ") + (">" if i == d_out else " ")
        typer.echo(f"{i:>3}  {d['max_input_channels']:>3} {d['max_output_channels']:>3}  {d['name']} {mark}")
    typer.echo("< default input   > default output")
    typer.echo("Pick a device by part of its name, e.g.: uv run robot config set voice.audio.input_device B129")
    typer.echo("Leave both unset to follow the desktop's default device (the speaker icon in the top bar).")


@audio_app.command("test")
def audio_test(
    ctx: typer.Context,
    seconds: float = typer.Option(3.0, "--seconds", help="Recording length"),
    say: str = typer.Option("Hello. I can hear you, and you can hear me.", "--say", help="Sentence to speak"),
) -> None:
    """Record from the robot's microphone, play it back, then speak a sentence with its own voice."""
    from robot.voice.base import to_float32
    from robot.voice.factory import build_engines

    cfg = get_ctx(ctx).config()
    t0 = time.monotonic()
    e = build_engines(cfg.voice)
    typer.echo(f"engines ({time.monotonic() - t0:.1f}s to load): {e.describe()}")
    if type(e.source).__name__ == "SilentSource":
        typer.secho("no microphone opened; see the warning above and `robot audio list`", fg=typer.colors.RED)
    if type(e.sink).__name__ == "NullSink":
        typer.secho("no speaker opened; see the warning above and `robot audio list`", fg=typer.colors.RED)

    typer.echo(f"recording {seconds:.0f}s... say something")
    blocks: list[np.ndarray] = []
    t_end = time.monotonic() + seconds
    speech_blocks = 0
    while time.monotonic() < t_end:
        b = e.source.read(timeout=0.2)
        if b is None:
            continue
        blocks.append(b)
        if e.vad.is_speech(b):
            speech_blocks += 1
    audio = np.concatenate(blocks) if blocks else np.zeros(1, dtype=np.int16)
    peak = float(np.abs(to_float32(audio)).max()) if audio.size else 0.0
    typer.echo(
        f"recorded {len(audio) / e.source.sample_rate:.1f}s, peak level {peak:.2f}, speech in {speech_blocks} blocks"
    )
    if peak < 0.01:
        typer.secho("that is silence: wrong microphone, muted, or gain at zero", fg=typer.colors.YELLOW)
    typer.echo("playing it back...")
    e.sink.play(to_float32(audio), e.source.sample_rate)

    if e.stt is not None and speech_blocks:
        t1 = time.monotonic()
        text = e.stt.transcribe(audio, e.source.sample_rate)
        typer.echo(f"heard: {text!r}  ({time.monotonic() - t1:.1f}s)")
    if e.tts is not None:
        t2 = time.monotonic()
        samples, sr = e.tts.synthesize(say)
        typer.echo(f"speaking ({time.monotonic() - t2:.1f}s to synthesise {len(samples) / sr:.1f}s of audio)")
        e.sink.play(to_float32(samples), sr)
    else:
        why = e.errors.get("tts", "")
        if "libonnxruntime" in why or "sherpa_onnx" in why or "ImportError" in why:
            hint = "the speech library is incomplete: run `uv sync --extra voice`"
        elif "FileNotFoundError" in why or "none of" in why:
            hint = "voice models missing: run `robot provision --group voice`"
        else:
            hint = why or "unknown reason"
        typer.secho(f"no text-to-speech engine: {hint}", fg=typer.colors.RED)
    for eng in (e.wake, e.stt, e.tts):
        if eng is not None:
            eng.close()
    e.source.close()
    e.sink.close()
    typer.echo("done. If you heard yourself and then the robot, voice is ready.")
