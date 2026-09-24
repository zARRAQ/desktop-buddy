# Getting started on a Raspberry Pi 5, for people who have never used Linux

This is the slow, no-assumptions version of `INSTALL.md` Path B. It gets the robot running
in the simplest possible setup: a Raspberry Pi 5, an HDMI screen, a USB webcam and a USB
speaker with a microphone. No Hailo, no motors, no soldering. Everything else can be added
later, one piece at a time.

Expect about two hours, most of it waiting for downloads.

## What you need

- Raspberry Pi 5 (4 GB or more) with its official 27 W USB-C power supply
- microSD card, 32 GB or more
- an HDMI screen and a micro-HDMI to HDMI cable (the Pi 5 has the small micro-HDMI sockets)
- a USB keyboard
- a USB webcam
- a USB speakerphone or a USB speaker plus a USB microphone. The Pi 5 has no headphone jack.
- a laptop with an SD card reader, to prepare the card
- Wi-Fi

## How to read the commands

Everything below that looks like this:

```bash
some command here
```

is typed into the Pi's terminal, one line at a time, followed by the Enter key. The terminal
is the black screen with text that the Pi boots into. Type exactly what is shown, including
spaces and dashes. Capital letters matter. When a command finishes, you get a new line ending
in `$` and can type the next one. If a command asks `Do you want to continue? [Y/n]`, press
`y` then Enter. Some commands take minutes and print a lot; that is normal.

Two shortcuts you will use: the Up arrow brings back the last command, and Tab completes a
half-typed file name.

## Step 1. Put the operating system on the SD card

On your laptop:

1. Download and install **Raspberry Pi Imager** from raspberrypi.com/software.
2. Insert the microSD card into the laptop.
3. Open Imager. Choose device **Raspberry Pi 5**.
4. Choose OS: **Raspberry Pi OS (other)**, then **Raspberry Pi OS Lite (64-bit)**. Check
   that it says Trixie or Debian 13. Lite has no desktop, only the terminal. That is what we
   want; the desktop would eat memory the robot needs.
5. Choose storage: your SD card.
6. Click **Next**, then **Edit settings**. Fill in:
   - hostname: `robot`
   - username and password: pick something you will remember. This guide uses `pi` as the
     username; if you pick another name, use yours wherever `pi` appears.
   - Wi-Fi name and password, and your country
   - locale and timezone
   - on the **Services** tab, enable SSH with password authentication
7. Save, then **Yes** to apply the settings, then **Yes** to erase the card. Wait until it
   says the write is complete.

## Step 2. First boot

1. Put the SD card in the Pi. Plug in the screen, the keyboard, the webcam and the speaker.
   Plug in power last.
2. The screen shows scrolling text for about a minute, then a `robot login:` prompt.
3. Type your username, Enter, then your password, Enter. The password does not show as you
   type. You now see a line ending in `$`. That is the terminal.
4. Update the system. This takes five to fifteen minutes:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

`sudo` means "do this as the administrator". It will ask for your password the first time.
The Pi restarts; log in again.

### Optional but recommended: type from your laptop instead

Long commands are easier to copy and paste than to type. On your laptop, open a terminal
(macOS: Terminal app; Windows: PowerShell) and type:

```bash
ssh pi@robot.local
```

Answer `yes` to the fingerprint question and enter the Pi's password. You are now typing into
the Pi from your laptop and can paste commands from this page. Everything below works the
same either way.

## Step 3. Turn on I2C

Not needed today, but harmless, and every sensor you add later uses it:

```bash
sudo raspi-config nonint do_i2c 0
```

## Step 4. Install the tools the robot depends on

One long command. Copy it whole, including the backslashes at the ends of lines:

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

Ten minutes or so.

## Step 5. Get the robot's code

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
git clone https://github.com/zARRAQ/desktop-buddy.git
cd desktop-buddy
```

The first line installs `uv`, the tool that manages the Python code. The third downloads the
robot's code into a folder called `desktop-buddy`. The fourth moves you into that folder.
**Every later command assumes you are in this folder.** If you reboot or log in again, type
`cd desktop-buddy` first.

Now install the robot itself:

```bash
uv venv --system-site-packages --python /usr/bin/python3
uv sync --extra voice
```

Five to ten minutes. Check it worked:

```bash
uv run robot --version
```

It prints a version number. If it prints an error instead, run `uv sync --extra voice` again
and read the last line of its output.

## Step 6. Download the AI models

```bash
uv run robot provision
```

This downloads the face-recognition models (about 40 MB) and the voice models (about 180 MB)
and checks them. It can be re-run if the Wi-Fi drops; it continues where it stopped.

## Step 7. Health check

```bash
uv run robot doctor
```

You get a table with a row per part of the system, each PASS, WARN, FAIL or SKIP. For the
setup in this guide, expect:

- `python`, `config`, `data dir`, `models`, `memory`: PASS
- `display`: PASS with `kms` or `fbdev` and your screen size
- `camera`: PASS with `uvc` and something like `/dev/video0`
- `audio`: PASS naming your USB device. WARN means the speaker or mic was not found: unplug
  it, plug it back in, run the doctor again.
- `hailo`: SKIP or WARN. Correct, you do not have one.
- `i2c`, `gpio`, `spi`: PASS, WARN or SKIP are all fine with no hardware attached.
- `brain`: SKIP. The robot uses scripted replies until you install a language model.
- `interlock`: PASS, since no motors are configured. A fresh install declares no actuators at
  all; they are added in `config/hardware.yaml` when you build the body.

Any FAIL: read the text in its row. It says what to fix. If it does not make sense, copy the
whole table and ask for help with it.

Then save what was detected so the robot does not have to guess every start:

```bash
uv run robot display detect --save
uv run robot camera detect --save
```

If you are typing inside a desktop (the Raspberry Pi OS with windows, not the black text
console), the display detector reports `window` and deliberately saves nothing for it: a window
only exists while you are logged in to that desktop, and the autostart in Step 11 runs without
one. That is expected. The camera detector saves the backend but not the `/dev/videoN` number,
because the number changes with plug order; the webcam is found again at each start.

## Step 8. Try the pieces one at a time

Look at the screen for each of these.

```bash
uv run robot display test --seconds 20
```

The face cycles through all of its expressions, then shows the listening bars, thinking dots
and mouth. If it is upside down or sideways, note it; Step 10 fixes that.

```bash
uv run robot camera test
```

A live camera picture with boxes around faces, for fifteen seconds; it stops by itself. If the
picture is mirrored the wrong way or slow, note it for Step 10.

```bash
speaker-test -t wav -c 2 -l 1
arecord -d 3 test.wav && aplay test.wav
```

The first plays a voice saying "front left, front right". The second records three seconds
from the microphone and plays it back. If you hear nothing, run `aplay -l` and `arecord -l`,
which list the sound devices, and ask for help with their output.

## Step 9. Run the robot

```bash
uv run robot run all
```

All of the robot's programs start. After a few seconds the face appears on the screen. Sit in
front of the camera. The eyes find you and it says hello. Say the wake word, then talk to it.
It answers with scripted replies for now. Press Ctrl and C together to stop everything.

Teach it your face:

```bash
uv run robot enroll --name "Your Name"
```

Look at the camera and turn your head slowly as it asks. Next time you run the robot it greets
you by name.

## Step 10. Small fixes you might need

All of these are one line in a text file called `config/local.yaml`. Open it with:

```bash
nano config/local.yaml
```

`nano` is a simple text editor. Arrow keys move, type to insert. When done, press Ctrl+O then
Enter to save, and Ctrl+X to leave. The file looks like this; add only the lines you need,
keeping the two-space indentation exactly:

```yaml
display:
  rotation: 180        # face upside down: 180. Sideways: 90 or 270.
camera:
  format: MJPG         # webcam slow or choppy
  mirror_preview: false  # preview mirrored the wrong way
face:
  design_scale: 1.5    # bigger eyes
```

## Step 11. Make it start on its own when powered on

Only once Step 9 works from the terminal. There are two ways, and which one you use depends
on whether your Pi boots to a desktop (windows, mouse pointer) or to the black text console.

### 11a. Pi boots to the desktop

From a terminal window on that desktop:

```bash
uv run robot autostart install
```

That registers the robot to open as a fullscreen face the moment the desktop logs in, and
makes the face cover the screen. Check that the Pi logs in by itself: `sudo raspi-config`,
System Options, Boot / Auto Login, **Desktop Autologin**. Then `sudo reboot`. The desktop
appears, then the face over it, in about half a minute.

Useful commands:

```bash
uv run robot autostart status    # is it installed, is it running
uv run robot autostart stop      # stop the running robot (it starts again at next login)
uv run robot autostart remove    # stop starting it at login
```

The robot's messages go to `~/.local/share/robot/autostart.log`; read them with
`tail -f ~/.local/share/robot/autostart.log`. To get at the desktop while the face covers it,
press Alt+Tab, or stop the robot with the command above.

### 11b. Pi boots to the console

This is the mode for the finished robot: nothing but the face, on in about twenty seconds. Set
`sudo raspi-config`, System Options, Boot / Auto Login, **Console Autologin**, then:

```bash
sudo ./deploy/install.sh
sudo systemctl enable --now robot.target
sudo reboot
```

To see what the robot is doing, log in (Ctrl+Alt+F1 gives a text console if the face is on the
screen) and type:

```bash
journalctl -u 'robot-*' -f
```

Ctrl+C stops the log view. To stop the robot: `sudo systemctl stop robot.target`. To start
it: `sudo systemctl start robot.target`. To switch off automatic start:
`sudo systemctl disable robot.target`.

Do not use both ways at once; each refuses to install while the other is active.

## Updating later

```bash
cd desktop-buddy
git pull
uv sync --extra voice
sudo systemctl restart robot.target
```

## Using an OpenMV camera board instead of a webcam

If your camera is an OpenMV board (a red circuit board with its own little screen), it is not
a webcam and needs its own setup: see `OPENMV.md` in this folder. Everything above still
applies; the OpenMV replaces the webcam step and gives the robot a second, tiny face.

## What to add next, in order

1. **Language model.** Follow `INSTALL.md` B6 (llama.cpp) and set `brain.managed: llama_server`
   in `config/local.yaml`. Real conversation instead of scripted replies.
2. **Hailo AI HAT.** `INSTALL.md` B4. Faster face recognition; the CPU version works without it.
3. **Motors and servos.** Read `HARDWARE.md` section 5 first, in full, and run
   `robot safety selftest` and `robot safety killtest` before a motor ever gets power.

## When something goes wrong

Copy the exact command you typed and everything it printed, and ask for help with both. "It
did not work" cannot be diagnosed; the printed text almost always can.
