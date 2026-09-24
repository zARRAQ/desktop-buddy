# Using an OpenMV camera board as the robot's eyes and camera

An OpenMV Cam (tested target: H7 R2) is not a webcam. It is a small computer with a camera,
running MicroPython. Plugged into the Pi it appears as a serial port, not as `/dev/video*`, so
the normal camera code cannot see it. This guide makes it work anyway, and uses the little LCD
wired to it as a second face.

How it works: a program (`openmv/main.py`) runs on the board. It shows blinking "boot eyes" on
its LCD, streams JPEG frames to the Pi over USB, and shows whatever face frames the Pi sends
back. On the Pi, the `openmv` bridge service owns the USB port, publishes the frames as
`camera.frame` messages for the perception service, and forwards `display.frame` messages
from the face service to the LCD.

```
OpenMV board  <== USB serial ==>  robot openmv (bridge)  <== bus ==>  perception, face
  camera  ------- JPEG frames ----------------------------------------->  camera.backend: bus
  LCD     <------ 1-bit face frames -----------------------------------  display.backend: bus (or mirror)
```

Expect 320x240 at about 10 frames a second, or 640x480 at 4 to 6. Face recognition works at
either; the smaller one is snappier.

## 1. Put the program on the board

Plug the board into the Pi with a USB cable. Within a few seconds a small drive appears on
the desktop (it is the board's flash). Then:

```bash
cd ~/desktop-buddy
uv run robot openmv flash
```

It copies `openmv/main.py` onto that drive and keeps the previous `main.py` as
`main.py.bak`. Unplug the board and plug it back in. Its LCD should show two cyan eyes that
blink every few seconds. If the drive was not found automatically, pass `--drive` with the
path shown in the file manager.

If the screen stays blank or shows garbage, the two lines at the top of `openmv/main.py`
(`LCD_W`, `LCD_H`) do not match your screen. Edit them, flash again, replug.

## 2. Tell the robot to use it

Add to `config/local.yaml` (create the file if it does not exist):

```yaml
openmv:
  enabled: true
  width: 320          # or 640 x 480
  height: 240
  lcd_width: 128      # match LCD_W / LCD_H in openmv/main.py
  lcd_height: 160
camera:
  backend: bus        # frames come over the bridge
display:
  backend: bus        # the LCD is the face ...
```

If you would rather keep the big HDMI face and have the LCD mirror it, leave
`display.backend` alone (or `auto`): with `openmv.enabled: true` the face service always sends a
copy to the LCD.

If the LCD is mounted sideways, add `display.rotation: 90` (or 270). The face is rendered at
LCD size and rotated on the Pi; the board just shows what it gets.

## 3. Prove the link

```bash
uv run robot openmv probe
```

Expected: the board answers the ping with its name and sensor, a frame count around 50 in
five seconds at 320x240, and a happy face appears on the LCD for a couple of seconds. Then:

```bash
uv run robot doctor
```

The `openmv` row should be PASS and `camera` should say `bus`. If the port says permission
denied, run `sudo usermod -aG dialout $USER`, log out and back in.

## 4. Run

`robot run all` (and the autostart) start the bridge automatically when `openmv.enabled` is
true. Watch it:

```bash
uv run robot run all
```

The bridge logs the board's messages and, once a second, publishes `openmv.state` with camera
and LCD frame rates. `robot camera test` works through the bridge too.

## Limits and honesty

- Nothing in this path has run on a real board yet. The protocol, the bitmap layout and the
  bridge are unit-tested with a fake board; the MicroPython side follows the OpenMV
  documentation. Expect one round of fixes on first contact, most likely around the LCD
  driver call or the fast bitmap path (the program falls back to a slower drawing method
  and says so in its log if the fast one is missing on your firmware).
- USB full speed caps the link around 800 KB/s. That is why 640x480 runs at a few frames a
  second. A real webcam is faster and sharper; the OpenMV is fine for a desk robot.
- The board's illumination LEDs, if your unit has them on pin P9, turn on with
  `openmv.leds: true`. If they are wired elsewhere nothing happens.
- One process owns the serial port. Do not run `robot openmv probe` while `robot run all` is
  running, and close the OpenMV IDE on any laptop connected to the board.
