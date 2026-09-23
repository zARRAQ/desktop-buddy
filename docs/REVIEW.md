# Plan review: what changed and why

The three documents that came in (`INSTALL.md`, `HARDWARE.md`, `HARDWARE_ABSTRACTION.md`) were
reviewed against current hardware, software and licensing facts (September 2026), and against
each other. This file lists every material change. The corrected documents are in this
directory; `PLAN.md` was written fresh because the originals referred to a `PLAN.md` that was
not supplied.

Honesty about verification: everything marked **verified** below was exercised in this
session on a Linux machine without a Raspberry Pi. Everything marked **written, not yet run
on hardware** follows the vendor documentation but has not touched a Pi 5. The first thing to
do on real hardware is `robot doctor`, then the acceptance commands in `INSTALL.md`.

## Contradictions inside the original plan

1. **GPIO19 used twice.** The wiring diagram put DRV8833 BIN2 on GPIO19, which is the I2S
   LRCLK. The pinout table moved it to GPIO16, but `HARDWARE_ABSTRACTION.md` section 3.2 still
   had `bin2: 19`. There is now one source of truth: `robot/data/defaults.yaml`, mirrored in
   `HARDWARE.md` 5.2, and the config is validated at startup.
2. **Display reset pin collision.** The SPI display block used `pin_reset: 27`; GPIO27 is
   encoder L channel B. The pinout table said GPIO4. GPIO4 it is.
3. **Python version.** `INSTALL.md` said Python 3.11 or 3.12. Raspberry Pi OS Trixie (the only
   OS current HailoRT supports, see below) ships Python 3.13. The project now targets 3.11 to
   3.13 and CI runs all three.

## Hardware facts that change the design

4. **The Pi 5 has hardware PWM only on GPIO 12, 13, 18 and 19.** GPIO18/19 carry I2S for the
   MAX98357A, leaving exactly two hardware PWM pins. A DRV8833 needs PWM on all four inputs;
   with gpiozero on a Pi 5 that means software PWM at about 800 Hz, audible and jittery when the
   language model saturates the cores. **The default H-bridge is now the TB6612FNG**, which takes
   one PWM pin per motor (PWMA on GPIO12, PWMB on GPIO13) plus plain direction pins, so 20 kHz
   PWM is real. The DRV8833 is still supported with the documented limitation. Verified:
   TB6612FNG driver logic against the mock GPIO; hardware PWM via sysfs written, not yet run.
5. **Pi 5 camera and display connectors are 22-pin.** Camera Module 3 and every DSI panel need a
   22-to-15-pin adapter cable, and the camera cable and the display cable are different parts.
   The original said "15-pin FPC" for both.
6. **The recommended round display does not exist as a supported product.** No vendor sells a
   2.1" 480x480 *DSI* round panel with a Raspberry Pi kernel overlay. Bare ST7701S panels exist
   (Alibaba, BuyDisplay, Newhaven) but need a custom panel driver and overlay: days of kernel
   work, not "one config line". Pimoroni's HyperPixel 2.1 Round is 480x480 but uses the DPI
   interface, which occupies the whole GPIO header and is incompatible with everything else in
   this robot. `HARDWARE.md` now recommends: any HDMI monitor for phase 1; the Waveshare 2.8" DSI
   LCD (480x640, supported overlay) as the plug-and-play head panel; Waveshare's 3.4" 800x800
   HDMI round display if round matters; a 1.28" GC9A01 SPI round panel as the cheap round
   option (driver written, not yet run).
7. **A GPIO does not "fall low the instant the process dies".** On a Raspberry Pi an output
   keeps its last level when its owner is SIGKILLed; only an orderly close resets it. The
   original interlock therefore did not do what it claimed. The supervisor now supports a
   `pulse` mode: the enable line carries a square wave and a small hardware pulse watchdog (a
   TLC555 missing-pulse detector, `HARDWARE.md` 5.3) converts pulses into the driver's STBY
   level, so a dead process really does stop the motors within ~100 ms. `robot safety killtest`
   measures which behaviour your wiring actually has instead of asserting it. Motion additionally
   refuses to drive unless it has heard from the supervisor in the last second, so the two
   processes watch each other. Verified in tests: cliff, pickup, heartbeat loss and supervisor
   death all drop the line and halt the drive within the tick budget.
8. **BNO085 over I2C is a known problem on the Pi** (clock stretching). The BOM now recommends
   the MPU6050 (driver written) and notes UART-RVC mode for the BNO085 if you insist.
9. **Do not enable the header UART.** GPIO14/15 are now the right motor's direction pins. The Pi
   5 debug UART is a separate connector, so nothing is lost. The original `do_serial_hw 0` step
   was removed.
10. **PCA9685 OE pin.** Layout C (no drivetrain) needs a safety gate too; the servo driver's
    output-enable on GPIO16 is reserved for it.

## Software facts that change the plan

11. **HailoRT 4.23 and `hailo-all` are built for Raspberry Pi OS Trixie only** and do not
    support Bookworm. `INSTALL.md` now targets Trixie 64-bit Lite. Python bindings arrive via
    apt and must be seen through a `--system-site-packages` venv; `uv venv --system-site-packages`
    followed by `uv sync` was tested to preserve that flag.
12. **`hailo-rpi5-examples` is deprecated** in favour of `hailo-apps`. We use the same models
    that hailo-apps' face recognition pipeline uses (SCRFD + ArcFace MobileFaceNet, Model Zoo
    v2.17.0 for Hailo-8/8L, v5.2.0 for Hailo-10H; URLs verified reachable) and its identity
    threshold of 0.5, driven directly through HailoRT's Python `InferModel` API. Decoder and
    alignment are unit tested; the device calls are written, not yet run.
13. **The AI HAT+ 2 (Hailo-10H, January 2026) runs LLMs and Whisper on the accelerator** through
    `hailo-ollama`, an Ollama/OpenAI-compatible server. The brain therefore speaks the
    OpenAI-compatible chat API and does not care whether `llama-server`, Ollama or `hailo-ollama`
    answers. Owners of an original AI Kit / AI HAT+ keep the CPU model; owners of an AI HAT+ 2
    change one URL and free two cores and a gigabyte of RAM. This is the recommended upgrade if
    the LLM latency bothers you.
14. **Piper TTS is GPL-3.0 now.** The MIT `rhasspy/piper` repository was archived in October 2025
    and development moved to `OHF-Voice/piper1-gpl`. Piper *voices* stay permissive, so we run
    them with sherpa-onnx (Apache-2.0), which also provides Moonshine speech-to-text, Silero VAD
    and a keyword spotter, in one wheel that exists for Python 3.13 on aarch64.
15. **`rpi-hardware-pwm` is GPL-3.0.** Replaced with our own sysfs implementation.
16. **openWakeWord 0.6 cannot be pip-installed on Python 3.12+** because it hard-depends on
    `tflite-runtime`. We only use its ONNX path and mask that dependency with a uv override.
17. **MAX98357A on Trixie** has a known "no sound" report with a documented fix
    (`dtoverlay=max98357a`, `dtparam=audio=off`, an `asound.conf` with softvol). The overlay in
    `INSTALL.md` changed from `hifiberry-dac` to `max98357a` and the ALSA file ships in `deploy/`.
18. **SDL's kmsdrm driver drew garbage on the Pi 5 until SDL 2.30.** pygame-ce 2.5 bundles SDL
    2.32, so `kms` should work, but the `fbdev` backend writes `/dev/fb0` (the KMS framebuffer
    emulation) and is kept as the robust fallback. Detection prefers KMS, falls back to fbdev.
19. **Perception runs on a laptop for real.** OpenCV's built-in YuNet detector and SFace embedder
    (OpenCV Zoo, Apache-2.0) make the "same perception service everywhere" claim true instead of
    aspirational, and let you enrol and test recognition with a webcam before any Hailo arrives.
    Verified on a real photo of six people (numbers in `REUSE.md`).
20. **LLM choice.** Default Gemma 3 1B Q4_K_M (0.8 GB, roughly 12 tok/s on a Pi 5, well inside the
    2.5 GB RSS budget). Qwen3 1.7B Q4_K_M (1.1 GB, about 8 tok/s) as the stronger alternative;
    disable its thinking mode. Gemma 4 E2B (April 2026) is the best small model but its GGUF is
    about 3 GB, so it only fits if you raise the budget, which the 8 GB Pi allows when the rest of
    the stack is lean. Numbers are from published community benchmarks, not measured here.

## Design changes in the software

21. **Bus and process model kept** (ZeroMQ XSUB/XPUB, one process per service, msgpack
    envelopes), plus an in-process bus with identical wire semantics for the simulator and
    tests. Verified: broker round trip, prefix subscriptions, slow-joiner handling.
22. **Configuration is validated.** Unknown keys are errors; `--set section.key=value` and
    `ROBOT__SECTION__KEY` environment overrides layer over `defaults.yaml`, `hardware.yaml`,
    `local.yaml`. Verified.
23. **Face renderer is polygon based**, not raster, so one code path serves 128x64 mono OLEDs and
    800x800 round HDMI panels; round panels pull the eyes inward 12% and mask corners; mono panels
    switch to a two-colour preset. Adaptive quality degrades in four documented steps. Verified
    in tests (symmetry, occlusion, blink, round mask, mono, quality controller).
24. **Gaze mirroring is explicit.** The camera looks outward and the screen faces the person, so a
    face on the image's right is on the robot's right, which is the viewer's left. The
    orchestrator has `mirror_gaze` (default true) instead of the original camera-level
    `hflip_for_mirror`, which would also have mirrored what the detector sees.
25. **Learning names by conversation.** A stranger is asked their name; a reply like "I'm Bob"
    triggers live enrolment of the next five embeddings of that track. Verified in the
    orchestrator tests and the simulator.
26. **`robot doctor`, `robot provision`, calibration and safety tests** exist as specified, with
    checksums pinned where publishers provide them and recorded on first download otherwise.

## Verified in this session versus not

| Area | Status |
|---|---|
| Config layering, bus, broker, service loop | verified (tests) |
| Face geometry, renderer, animator, quality controller, test pattern | verified (tests) |
| Display: window, null, fbdev (against a file), SPI protocol (against a recorder), I2C OLED packing | verified (tests); real panels not yet |
| Display: kms fullscreen on a Pi | written, not yet run |
| Camera: detection cascade, letterbox round trip (property tested), synthetic | verified; UVC/CSI on a Pi not yet |
| Perception CPU (YuNet + SFace) | verified on a real photo |
| Perception Hailo (SCRFD decode, alignment) | decoder and alignment verified with synthetic data; device path not yet |
| Actuators: servo clamp/slew/relax, TB6612FNG logic, kinematics, odometry, three layouts | verified (tests) |
| PCA9685 register writes | verified against a register mock |
| Safety logic, enable line modes, service interlock, supervisor death | verified (tests) |
| Brain: prompt/parse, scripted fallback, OpenAI-compatible client | verified against a fake server |
| Voice: state machine with fake engines | verified; sherpa-onnx and openWakeWord engines written, not yet run |
| Orchestrator behaviours | verified (tests) |
| Simulator, headless CI smoke test | verified (runs end to end) |
| systemd units, udev, install script, config.txt snippet | written, syntax checked, not yet installed on a Pi |
