# Installation

Two paths. Pick one.

- **[Path A: laptop](#path-a-laptop-no-hardware)** runs the full simulator with no Raspberry Pi
  and no parts. Start here.
- **[Path B: Raspberry Pi 5](#path-b-raspberry-pi-5)** deploys to real hardware.

New to Linux? [GETTING_STARTED.md](GETTING_STARTED.md) walks Path B one keystroke at a time
with the simplest hardware (HDMI screen, USB webcam, USB speakerphone).

---

## Path A: laptop, no hardware

Linux, macOS and Windows (WSL2). About five minutes.

### A1. Prerequisites

```bash
python3 --version        # 3.11, 3.12 or 3.13
git --version
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### A2. Clone and install

```bash
git clone https://github.com/zARRAQ/desktop-buddy.git
cd desktop-buddy
uv sync --extra dev
```

No hardware libraries are pulled in. Add `--extra voice` if you want to try the real speech
engines on the laptop (they need `robot provision --group voice`, about 180 MB).

### A3. Run

```bash
uv run robot sim
```

A window opens with three panels: the face, a top-down view of a simulated desk, and a live bus
log. The robot notices the person, greets them, and answers with the scripted brain.

| Key | Action |
|---|---|
| Arrow keys | Move the simulated person |
| W A S D | Nudge the robot (real `motion.command` messages through the safety interlock) |
| Space | Fake wake word; the fake speech-to-text hears "hello there" |
| T | Wake and say the next canned utterance ("what is your name", "remember that I like tea", ...) |
| N | Toggle the person between known ("Ann") and stranger; a stranger is asked their name |
| E | Hide / show the person |
| 1 to 9 | Force an expression |
| M | Cycle the state overlays: listening bars, thinking dots, speaking mouth, off |
| K | Kill the safety supervisor: the enable line falls and motion refuses to drive |
| R | Reset the world and restart the supervisor |
| Esc / Q | Quit |

Drive toward a desk edge with W and watch the cliff sensor turn red and the robot stop.

To talk to a real language model from the simulator:

```bash
# any OpenAI-compatible server: llama-server, Ollama, LM Studio, ...
uv run robot sim --brain-endpoint http://127.0.0.1:8080/v1
```

### A4. Verify

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run robot sim --headless --seconds 3
```

All six pass on a clean checkout (they are what CI runs). You are ready to develop.

### A5. Face recognition on the laptop

```bash
uv run robot provision --group vision-cpu     # 37 MB: YuNet + SFace
uv run robot camera detect                    # finds your webcam
uv run robot enroll --name "Your Name"        # five captures while you turn your head
uv run robot run all --mock                   # every service as processes, mock GPIO
```

---

## Path B: Raspberry Pi 5

60 to 90 minutes, most of it waiting for downloads.

### B1. Flash the OS

Use Raspberry Pi Imager.

- Device: **Raspberry Pi 5**
- OS: **Raspberry Pi OS (64-bit) Lite, Trixie** (Debian 13). Current HailoRT (4.23+) and the
  `hailo-all` package are built for Trixie only; Bookworm gets an older, unsupported stack.
  Trixie ships Python 3.13, which this project supports.

Do not pick the Desktop image; it costs roughly 400 MB of RAM and background CPU that the
language model needs.

Click the gear icon and pre-configure hostname (`robot.local`), user and password, Wi-Fi, SSH,
locale and timezone. Write, insert, boot.

### B2. First connection

```bash
ssh <user>@robot.local
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

### B3. Interfaces and boot config

```bash
sudo raspi-config nonint do_i2c 0      # I2C on
sudo raspi-config nonint do_spi 0      # SPI on (only needed for a bare SPI panel)
```

Do **not** enable the serial port on the header: GPIO14/15 are motor pins. The Pi 5 debug UART
is a separate connector and is unaffected.

Append the snippet from `deploy/boot/config.txt.snippet` to `/boot/firmware/config.txt`. It
sets PCIe Gen 3 for the AI HAT, I2C at 400 kHz, I2S for the MAX98357A, hardware PWM on GPIO12/13,
and leaves camera and display autodetect on:

```bash
sudo tee -a /boot/firmware/config.txt < deploy/boot/config.txt.snippet
```

If you use a **DSI panel**, add its overlay line as well, for example
`dtoverlay=vc4-kms-dsi-waveshare-panel,2_8_inch` for the Waveshare 2.8" (add `,dsi0` if it is
on connector 0). A **bare SPI panel** needs nothing here; the software drives it through
`spidev`.

```bash
sudo reboot
```

### B4. Install the Hailo stack

```bash
sudo apt install -y dkms hailo-all
sudo reboot
hailortcli fw-control identify
```

Write down the architecture it prints (`HAILO8L`, `HAILO8` or `HAILO10H`) and the firmware
version. If the command fails: reseat the PCIe ribbon at both ends, confirm
`dtparam=pciex1_gen=3` is in `config.txt`, run `lspci` and `dmesg | grep -i hailo`. Nothing
downstream works until this succeeds.

### B5. System dependencies

```bash
sudo apt install -y \
  git build-essential cmake pkg-config \
  python3-dev python3-venv python3-pip \
  python3-picamera2 python3-libcamera \
  python3-gpiozero python3-lgpio python3-smbus2 python3-spidev \
  libcamera-apps \
  portaudio19-dev libsndfile1 alsa-utils \
  libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-ttf-2.0-0 \
  i2c-tools libopenblas-dev
```

The Pi-specific Python packages (`picamera2`, `libcamera`, `gpiozero`, `lgpio`, `smbus2`,
`spidev`, and the HailoRT bindings installed by `hailo-all`) come from apt. They are not on PyPI
in a form that builds reliably on the Pi, so the virtual environment below is created with
access to the system packages.

### B6. Clone and install the robot

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

git clone https://github.com/zARRAQ/desktop-buddy.git
cd desktop-buddy
uv venv --system-site-packages --python /usr/bin/python3
uv sync --extra voice
```

`--system-site-packages` is what lets the venv see `picamera2` and `hailo_platform`. `uv sync`
keeps that flag (tested). Check it worked:

```bash
uv run python -c "import hailo_platform, picamera2, gpiozero; print('ok')"
```

If you prefer pip-installed GPIO libraries, `uv sync --extra pi --extra voice` compiles them;
the apt route is faster and is what the docs assume.

For the language model:

```bash
uv run robot llm install              # prebuilt llama-server for arm64 (falls back to --build)
uv run robot provision --group llm    # Gemma 3 1B, about 0.8 GB
uv run robot llm test                 # starts the server, asks one question, reports tokens/s
```

`llm install` fetches llama.cpp's CPU build for Linux arm64 into `~/.local/share/robot/llama`,
checks it runs, and writes `brain.managed: llama_server` plus the binary path into
`config/local.yaml`. If the prebuilt binary will not run on your OS it compiles from source
(`--build`, 10 to 15 minutes). Alternatively run Ollama, or on an AI HAT+ 2 `hailo-ollama`, and
point `brain.endpoint` at it with `brain.managed: none`.

### B7. Download models

```bash
uv run robot provision --list     # what will be downloaded, with sizes and licences
uv run robot provision            # everything this machine needs
```

Default groups: CPU face models (37 MB, always, as a fallback), voice (Moonshine STT, a Piper
voice, Silero VAD, openWakeWord, about 180 MB), the Hailo face models for your architecture
(about 12 MB), and the language model (Gemma 3 1B, 0.8 GB) when the brain is not scripted.
Resumable and idempotent; re-run if it fails partway. `--verify` checksums what is on disk.

### B8. Detect your hardware

```bash
uv run robot doctor
```

A pass/warn/fail table: Python, config, models, display, camera, Hailo, audio, I2C devices,
GPIO and hardware PWM, SPI, thermals, the language model endpoint, memory. Fix anything red.

```bash
uv run robot display detect --save
uv run robot camera detect --save
```

Both write into `config/local.yaml`, which is gitignored and per robot.

**Bare SPI panel:** detection cannot identify it (no MISO), so set it yourself:

```yaml
# config/local.yaml
display:
  backend: spi
  controller: gc9a01      # or st7789, ili9341, ili9488
  rotation: 0
```

Then confirm it looks right:

```bash
uv run robot display test --pattern
```

Colour bars, RGB squares, a grid, a circle and a block "F". This is how you discover in ten
seconds that your panel is BGR (`display.color: bgr888`) or mounted 90 degrees off
(`display.rotation: 90`).

```bash
uv run robot camera test          # live preview with fps counter and face boxes
uv run robot camera bench         # measured fps, latency, timeouts
```

If a USB webcam shows 10 fps, it negotiated YUYV; set `camera.format: MJPG`.

### B9. Declare your actuators

Copy the layout you built from `config/hardware.example.yaml` into `config/hardware.yaml`, then:

```bash
uv run robot calibrate servos    # jog each channel, capture min / centre / max
uv run robot calibrate drive     # drive 500 mm, enter the actual distance
uv run robot calibrate cliff     # sample each sensor on-surface and off-edge
```

**Prop the chassis up so the wheels spin free** before running `calibrate drive`.

Skip this step if you have no actuators yet. The stack runs fine without them.

### B10. Prove the safety interlock

Before a motor is ever connected to power.

```bash
uv run robot safety selftest
```

Drives GPIO26 low, high and pulsing and asks you to confirm each with a multimeter on the
motor driver's STBY pin. Then:

```bash
uv run robot safety killtest
```

Starts a process that drives the line high, SIGKILLs it, and measures how long the line takes
to fall. **Expect this to FAIL with a bare wire**: a Raspberry Pi keeps a GPIO at its last level
when its owner dies. That is the point of the test. Build the pulse watchdog (`HARDWARE.md` 5.3), set
`safety.enable_mode: pulse` in `config/hardware.yaml`, and run the killtest again. It must
pass before the robot runs untethered on a desk. `robot doctor` warns (row `interlock`) while a
drivetrain is configured in `level` mode, and so does the safety service at startup.

### B11. Install as a service

Two mutually exclusive options. On a Pi that boots to the **desktop** (Raspberry Pi OS with
desktop), the systemd units cannot take the display; use the desktop autostart instead:

```bash
uv run robot autostart install     # fullscreen window at desktop login; `status`, `stop`, `remove`
```

On a Pi that boots to the **console** (Lite, or Desktop set to Console Autologin), install the
systemd units; `install.sh` refuses on a desktop-booting Pi unless given `--force`:

```bash
sudo ./deploy/install.sh
```

Idempotent. It installs one systemd unit per process under `robot.target`, creates `/run/robot`
for the bus sockets, adds a udev rule so the service user can reach `/dev/spidev*`,
`/dev/i2c-*`, `/dev/gpiochip*`, the PWM chip and the Hailo device without root, adds the user to
`video audio gpio i2c spi render input`, caps journald, and writes `config/robot.env` pointing the
bus at unix sockets. Log out and back in so the groups apply, then:

```bash
sudo systemctl enable --now robot.target
```

### B12. Verify

```bash
systemctl status 'robot-*'
journalctl -u 'robot-*' -f
```

The face should appear within about 20 seconds of boot. Enrol yourself:

```bash
uv run robot enroll --name "Your Name"
```

Look at the camera and turn your head slowly as prompted; five embeddings are stored. Walk out of
frame and back in; it greets you by name in under a second. Strangers are asked their name and
learned on the spot.

Talk to it: "hey jarvis" (the default wake word), then a question.

---

## Troubleshooting

Work down this table in order. The first five explain most problems.

| Symptom | Check | Fix |
|---|---|---|
| Random crashes, corrupted files, inexplicable bugs | `dmesg \| grep -i voltage`; `robot doctor` thermal row | Undervoltage. Use the official 27 W PSU or a stronger 5 V rail. This masquerades as software bugs more than anything else. |
| Everything slows down after a few minutes | `vcgencmd measure_temp`, `vcgencmd get_throttled` | Thermal throttling above 80 C. Active cooler plus real airflow holes. |
| `hailortcli` fails | `lspci`, `dmesg \| grep -i hailo` | Reseat the PCIe FFC. Confirm `dtparam=pciex1_gen=3`. Confirm Trixie, not Bookworm. |
| `import hailo_platform` fails inside the venv | `uv run python -c "import hailo_platform"` | The venv was created without `--system-site-packages`. Delete `.venv` and redo B6. |
| I2C device missing | `i2cdetect -y 1` | Address collision: strap INA219 to `0x41`. VL53L1X all at `0x29`: use the mux. |
| Nothing on GPIO responds | Physical inspection | The AI HAT covers the header. Seat the booster header properly. |
| No hardware PWM chip (`robot doctor` gpio WARN) | `ls /sys/class/pwm` | Add `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` under `[pi5]`. |
| Black screen | `robot display detect` | Wrong backend. Check `/sys/class/drm/*/status` and `/dev/fb*`. For SPI panels set `controller`. Try `display.backend: fbdev`. |
| Colours inverted or swapped | `robot display test --pattern` | `display.color: bgr888`, or the panel wants inversion off. |
| Image rotated or mirrored | `robot display test --pattern` | `display.rotation`, `display.flip`. |
| Face stutters | `journalctl -u robot-face` | Adaptive quality logs each step. Usually thermal or an SPI panel clocked too slowly; raise `display.spi.speed_hz`. |
| Camera at 10 fps | `robot camera bench` | Negotiated YUYV. `camera.format: MJPG`. |
| No audio out (MAX98357A) | `aplay -l`, `speaker-test -c2` | Trixie needs `dtoverlay=max98357a` and `dtparam=audio=off` (not `hifiberry-dac`); copy `deploy/alsa/asound.conf` to `/etc/asound.conf`; `raspi-config` audio selection to the I2S card. |
| No audio in | `arecord -l`, `arecord -d 5 t.wav` | USB mic not default. Set `voice.audio.input_device` to its name or index. |
| Wake word never fires | `journalctl -u robot-voice` | Check input gain with `alsamixer`. A mic at 10% detects nothing. Try `voice.wake.threshold: 0.4`. |
| `openwakeword` fails to install | `uv sync` output | Needs the `tflite-runtime` override in `pyproject.toml` (already there); do not `pip install` it manually. |
| Recognised as the wrong person | match scores in the perception log | Lower `perception.match_threshold` is the wrong direction; raise it (0.45 for SFace, 0.55 for ArcFace) or re-enrol with better lighting. |
| Never recognised | match scores | More light on your face. Re-enrol. Front lighting matters far more than camera quality. |
| LLM very slow | `robot bench llm` | Check RSS against the 2.5 GB budget; `robot-brain` has `MemoryMax`. Check thermals. A model that swaps has stopped. Consider the AI HAT+ 2. |
| Servo buzzing and hot | `journalctl -u robot-motion` | It is holding against a stop. Fix `min_deg`/`max_deg`. Auto-relax should have caught this; if not, that is a bug worth reporting. |
| Robot drove off the desk | Everything | The cliff estop failed. Stop. Re-run `robot calibrate cliff` and `robot safety killtest`, and do not run untethered until both pass. |

---

## Updating

```bash
cd desktop-buddy
git pull
uv sync --extra voice
uv run robot provision           # fetches new or changed models
sudo ./deploy/install.sh         # refreshes systemd units
sudo systemctl restart robot.target
```

`config/local.yaml` and `config/hardware.yaml` are never touched by updates.

---

## Uninstalling

```bash
sudo systemctl disable --now robot.target
sudo ./deploy/install.sh --uninstall
rm -rf ~/.local/share/robot      # removes models AND the memory database
```

The last command deletes every stored face embedding and every remembered fact. To remove
people while keeping the install:

```bash
uv run robot memory forget --name "Some Name"   # one person, hard delete
uv run robot memory forget --all                # everyone
```
