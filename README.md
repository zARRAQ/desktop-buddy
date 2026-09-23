# desktop-buddy

A desktop companion robot for the Raspberry Pi 5: a procedural face on whatever display you
have, on-device face recognition (Hailo, or the CPU on a laptop), a fully local voice
pipeline, a small local language model, four actuators and a safety supervisor that can stop
the motors regardless of what the language model wants.

Nothing leaves the robot. No cloud, no accounts.

## Start here

| I want to | Read |
|---|---|
| Run the full simulator on my laptop in five minutes | [docs/INSTALL.md](docs/INSTALL.md) Path A |
| Put it on a Raspberry Pi 5 | [docs/INSTALL.md](docs/INSTALL.md) Path B |
| Know what to buy and how to wire it | [docs/HARDWARE.md](docs/HARDWARE.md) |
| Understand how any screen, camera or actuator layout is supported | [docs/HARDWARE_ABSTRACTION.md](docs/HARDWARE_ABSTRACTION.md) |
| See the architecture, process model and build phases | [docs/PLAN.md](docs/PLAN.md) |
| See what was changed from the original plan and why | [docs/REVIEW.md](docs/REVIEW.md) |
| See which existing projects are reused and under what licence | [docs/REUSE.md](docs/REUSE.md) |

## Quick start (laptop)

```bash
uv sync --extra dev
uv run robot sim
```

## Status

Version 0.1: the simulator, face renderer, bus, configuration, actuator abstraction, safety
interlock, CPU face recognition (verified on a real photo), memory, brain client, voice state
machine and CLI run and are tested on a laptop: 94 tests, ruff, mypy strict and import-linter
all pass, and CI runs a headless end-to-end simulation on Python 3.11 to 3.13.
Hardware backends (Hailo, SPI panels, GPIO, I2S audio, systemd deployment) are written against
the documented vendor APIs but have **not yet been exercised on a real Pi 5**. `docs/REVIEW.md`
lists exactly what is verified and what is not.

## Licence

MIT. Third-party attributions are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
