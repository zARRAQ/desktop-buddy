# Desktop Buddy: OpenMV Cam H7 R2 side of the USB bridge.
#
# Copy this file onto the OpenMV's flash drive as main.py (`robot openmv flash` does it), then
# unplug and replug the camera. It then:
#   * shows blinking "boot eyes" on the attached LCD until the Pi connects,
#   * streams JPEG camera frames to the Pi over the USB serial port,
#   * shows the face frames the Pi sends back on the LCD.
#
# Wire format (mirrors src/robot/hal/openmv/protocol.py): b"OM" + type byte + uint32 length +
# payload. J = JPEG (u16 w, u16 h, u32 seq, jpeg). F = face (u16 w, u16 h, u16 fg565,
# u16 bg565, 1-bit rows padded to 32-bit words, LSB first). C = camera config (u16 w, u16 h,
# u8 quality, u8 fps, u8 leds). P/Q = ping/pong. L = log text.
#
# Screen size and eye colour below are the only things you should need to edit.

import gc
import struct
import time

import image
import pyb
import sensor

LCD_W = 128  # your screen, in the orientation it is mounted
LCD_H = 160
BOOT_EYE = (62, 224, 230)  # same cyan as the Pi's default face
BOOT_BG = (0, 0, 0)
LED_PIN = "P9"  # illumination LEDs, if the unit wires them to a pin
VERSION = "buddy-openmv 1"

cam_w, cam_h, quality, fps, leds_on = 320, 240, 70, 10, 0
seq = 0
last_pi_ms = 0
PI_TIMEOUT_MS = 5000

usb = pyb.USB_VCP()
usb.setinterrupt(-1)  # binary data must never raise KeyboardInterrupt

blue = pyb.LED(3)
red = pyb.LED(1)
led_pin = None

# ----------------------------------------------------------------------------- LCD


class Screen:
    """Wraps whichever display API this firmware has."""

    def __init__(self):
        self.dev = None
        self.kind = "none"
        try:
            import display

            self.dev = display.SPIDisplay(width=LCD_W, height=LCD_H)
            self.kind = "display.SPIDisplay"
            return
        except Exception:
            pass
        try:
            import lcd

            lcd.init()
            self.dev = lcd
            self.kind = "lcd"
        except Exception as exc:
            self.kind = "none (%s)" % exc

    def show(self, img):
        if self.dev is None:
            return
        if self.kind == "lcd":
            self.dev.display(img)
        else:
            self.dev.write(img)


screen = Screen()
palette = image.Image(256, 1, image.RGB565)
fast_binary = True  # zero-copy path; falls back to drawing runs if the firmware lacks it


def set_palette(fg, bg):
    palette.set_pixel(0, 0, bg)
    palette.set_pixel(255, 0, fg)


def rgb565_to_tuple(v):
    return (((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3)


def show_face(w, h, fg, bg, bits):
    global fast_binary
    fgc, bgc = rgb565_to_tuple(fg), rgb565_to_tuple(bg)
    if fast_binary:
        try:
            set_palette(fgc, bgc)
            img = image.Image(w, h, image.BINARY, buffer=bits)
            screen.show(img.to_rgb565(color_palette=palette))
            return
        except Exception as exc:
            fast_binary = False
            log("binary fast path unavailable (%s); drawing runs" % exc)
    # slow path: draw horizontal runs of lit pixels
    img = image.Image(w, h, image.RGB565)
    if bgc != (0, 0, 0):
        img.draw_rectangle(0, 0, w, h, color=bgc, fill=True)
    words = (w + 31) // 32
    fmt = "<%dI" % words
    row_bytes = words * 4
    for y in range(h):
        row = struct.unpack(fmt, bits[y * row_bytes : (y + 1) * row_bytes])
        start = -1
        for x in range(w):
            lit = (row[x >> 5] >> (x & 31)) & 1
            if lit and start < 0:
                start = x
            elif not lit and start >= 0:
                img.draw_line(start, y, x - 1, y, color=fgc)
                start = -1
        if start >= 0:
            img.draw_line(start, y, w - 1, y, color=fgc)
    screen.show(img)


def boot_eyes(blink=0.0):
    """Two rounded cyan eyes, drawn by the board itself before the Pi takes over."""
    img = image.Image(LCD_W, LCD_H, image.RGB565)
    if BOOT_BG != (0, 0, 0):
        img.draw_rectangle(0, 0, LCD_W, LCD_H, color=BOOT_BG, fill=True)
    ew = LCD_W * 28 // 128
    eh = max(2, int(LCD_W * 40 // 128 * (1.0 - 0.95 * blink)))
    gap = LCD_W * 24 // 128
    r = max(1, min(ew, eh) // 4)
    cy = LCD_H // 2
    for cx in (LCD_W // 2 - gap - ew // 2, LCD_W // 2 + gap + ew // 2):
        x0, y0 = cx - ew // 2, cy - eh // 2
        img.draw_rectangle(x0 + r, y0, ew - 2 * r, eh, color=BOOT_EYE, fill=True)
        img.draw_rectangle(x0, y0 + r, ew, eh - 2 * r, color=BOOT_EYE, fill=True)
        for px, py in ((x0 + r, y0 + r), (x0 + ew - r - 1, y0 + r), (x0 + r, y0 + eh - r - 1), (x0 + ew - r - 1, y0 + eh - r - 1)):
            img.draw_circle(px, py, r, color=BOOT_EYE, fill=True)
    screen.show(img)


# ----------------------------------------------------------------------------- USB


def send(kind, payload):
    usb.write(struct.pack("<2sBI", b"OM", ord(kind), len(payload)))
    if payload:
        usb.write(payload)


def log(text):
    try:
        send("L", text.encode())
    except Exception:
        pass


def read_exact(n, timeout_ms):
    buf = bytearray(n)
    mv = memoryview(buf)
    got = 0
    t0 = time.ticks_ms()
    while got < n:
        k = usb.recv(mv[got:], timeout=timeout_ms)
        if k:
            got += k
        elif time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
            return None
    return buf


def poll_pi():
    """Handle everything the Pi has sent. Returns True if a face frame was shown."""
    global cam_w, cam_h, quality, fps, leds_on, last_pi_ms
    shown = False
    while usb.any():
        first = read_exact(1, 50)
        if first is None or first[0] != ord("O"):
            continue  # resync: skip until the magic
        second = read_exact(1, 50)
        if second is None or second[0] != ord("M"):
            continue
        rest = read_exact(5, 200)
        if rest is None:
            return shown
        kind, length = struct.unpack("<BI", rest)
        if length > 262144:
            continue
        payload = read_exact(length, 2000) if length else b""
        if payload is None:
            return shown
        last_pi_ms = time.ticks_ms()
        if kind == ord("F"):
            w, h, fg, bg = struct.unpack("<HHHH", payload[:8])
            show_face(w, h, fg, bg, payload[8:])
            shown = True
        elif kind == ord("C"):
            w, h, q, f, l = struct.unpack("<HHBBB", payload[:7])
            if (w, h) != (cam_w, cam_h):
                cam_w, cam_h = w, h
                cam_init()
            quality, fps, leds_on = q, max(1, f), l
            set_leds(leds_on)
            log("config %dx%d q%d fps%d leds%d" % (cam_w, cam_h, quality, fps, leds_on))
        elif kind == ord("P"):
            send("Q", ("%s sensor=%s lcd=%dx%d via %s fast=%d" % (VERSION, sensor_name(), LCD_W, LCD_H, screen.kind, fast_binary)).encode())
    return shown


# ----------------------------------------------------------------------------- camera


def sensor_name():
    try:
        sid = sensor.get_id()
        return {sensor.MT9M114: "MT9M114", sensor.OV7725: "OV7725", sensor.OV5640: "OV5640"}.get(sid, str(sid))
    except Exception:
        return "?"


def cam_init():
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    if cam_w >= 640:
        sensor.set_framesize(sensor.VGA)
    elif cam_w >= 320:
        sensor.set_framesize(sensor.QVGA)
    else:
        sensor.set_framesize(sensor.QQVGA)
    sensor.skip_frames(time=800)


def set_leds(on):
    global led_pin
    try:
        if led_pin is None:
            led_pin = pyb.Pin(LED_PIN, pyb.Pin.OUT_PP)
        led_pin.value(1 if on else 0)
    except Exception:
        pass


def send_frame():
    global seq
    img = sensor.snapshot()
    jpg = img.compress(quality=quality)
    seq += 1
    send("J", struct.pack("<HHI", img.width(), img.height(), seq))
    usb.write(jpg.bytearray())


# ----------------------------------------------------------------------------- main

cam_init()
boot_eyes()
log("boot: %s" % VERSION)
next_blink = time.ticks_add(time.ticks_ms(), 2500)
next_frame = time.ticks_ms()
connected = False

while True:
    try:
        now = time.ticks_ms()
        poll_pi()
        pi_alive = last_pi_ms and time.ticks_diff(now, last_pi_ms) < PI_TIMEOUT_MS
        if pi_alive != connected:
            connected = pi_alive
            if connected:
                blue.on()
            else:
                blue.off()
                boot_eyes()
                next_blink = time.ticks_add(now, 2500)
        if connected:
            if time.ticks_diff(now, next_frame) >= 0:
                send_frame()
                next_frame = time.ticks_add(now, 1000 // fps)
            else:
                time.sleep_ms(2)
        else:
            # idle animation: blink every few seconds until the Pi shows up
            if time.ticks_diff(now, next_blink) >= 0:
                boot_eyes(1.0)
                time.sleep_ms(120)
                boot_eyes()
                next_blink = time.ticks_add(now, 2500 + (now % 2000))
            time.sleep_ms(20)
        gc.collect() if seq % 50 == 0 else None
    except Exception as exc:
        red.on()
        log("error: %r" % exc)
        time.sleep_ms(200)
        red.off()
        try:
            cam_init()
        except Exception:
            pass
