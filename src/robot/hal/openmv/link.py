"""Serial link to the OpenMV board (USB CDC, ``/dev/ttyACM*``). One reader thread; writes
are serialised with a lock and dropped rather than blocked when the board is slow."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from robot.hal.openmv import protocol as proto

log = logging.getLogger(__name__)

FrameCallback = Callable[[int, int, int, bytes], None]  # width, height, seq, jpeg
TextCallback = Callable[[str], None]


def find_port() -> str | None:
    """The OpenMV's serial device, preferring the stable by-id symlink."""
    for base, pattern in (
        (Path("/dev/serial/by-id"), "*OpenMV*"),
        (Path("/dev/serial/by-id"), "*openmv*"),
        (Path("/dev"), "ttyACM*"),
    ):
        hits = sorted(str(p) for p in base.glob(pattern)) if base.is_dir() else []
        if hits:
            return hits[0]
    return None


class OpenMvLink:
    def __init__(
        self,
        port: str | None,
        *,
        on_frame: FrameCallback,
        on_log: TextCallback | None = None,
        on_pong: TextCallback | None = None,
        write_timeout_s: float = 0.5,
        keepalive_s: float = 1.0,
    ) -> None:
        self.port_name = port
        self._on_frame = on_frame
        self._on_log = on_log or (lambda s: log.info("openmv: %s", s))
        self._on_pong = on_pong or (lambda s: log.info("openmv pong: %s", s))
        self._write_timeout = write_timeout_s
        self._keepalive = keepalive_s  # the board stops streaming if it hears nothing from us
        self._last_ping = 0.0
        self.board = ""
        self._serial: object | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.parser = proto.FrameParser()
        self.frames_in = 0
        self.bytes_in = 0
        self.frames_out = 0
        self.dropped_out = 0
        self.last_rx = 0.0

    def bind(
        self, *, on_frame: FrameCallback, on_log: TextCallback | None = None, on_pong: TextCallback | None = None
    ) -> None:
        """Point the callbacks at a new owner (the bridge service adopts an injected link)."""
        self._on_frame = on_frame
        if on_log is not None:
            self._on_log = on_log
        if on_pong is not None:
            self._on_pong = on_pong

    @property
    def open_(self) -> bool:
        return self._serial is not None

    def open(self) -> None:
        import serial  # pyserial

        port = self.port_name or find_port()
        if not port:
            raise FileNotFoundError("no OpenMV serial device (/dev/ttyACM*); is it plugged in and running main.py?")
        self.port_name = port
        # baud is meaningless over USB CDC but pyserial wants one
        self._serial = serial.Serial(port, 115200, timeout=0.2, write_timeout=self._write_timeout)
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, name="openmv-rx", daemon=True)
        self._thread.start()
        log.info("openmv link open on %s", port)

    def close(self) -> None:
        self._stop.set()
        ser = self._serial
        self._serial = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if ser is not None:
            try:
                ser.close()  # type: ignore[attr-defined]
            except Exception as exc:
                log.debug("close: %s", exc)

    # -- tx ---------------------------------------------------------------------------
    def send(self, data: bytes) -> bool:
        ser = self._serial
        if ser is None:
            return False
        with self._lock:
            try:
                ser.write(data)  # type: ignore[attr-defined]
                self.frames_out += 1
                return True
            except Exception as exc:  # SerialTimeoutException or a vanished device
                self.dropped_out += 1
                if self.dropped_out in (1, 10, 100) or self.dropped_out % 1000 == 0:
                    log.warning("openmv write dropped (%d so far): %s", self.dropped_out, exc)
                return False

    def send_config(self, width: int, height: int, quality: int, fps: int, leds: bool) -> bool:
        return self.send(proto.encode_config(width, height, quality, fps, leds))

    def send_face(self, width: int, height: int, fg565: int, bg565: int, bits: bytes) -> bool:
        return self.send(proto.encode_face(width, height, fg565, bg565, bits))

    def ping(self) -> bool:
        return self.send(proto.frame(proto.TYPE_PING, b"pi"))

    # -- rx ---------------------------------------------------------------------------
    def _reader(self) -> None:
        while not self._stop.is_set():
            ser = self._serial
            if ser is None:
                return
            now = time.monotonic()
            if self._keepalive > 0 and now - self._last_ping >= self._keepalive:
                self._last_ping = now
                self.ping()
            try:
                data = ser.read(65536)  # type: ignore[attr-defined]
            except Exception as exc:
                if not self._stop.is_set():
                    log.error("openmv read failed: %s", exc)
                    self._serial = None
                return
            if not data:
                continue
            self.bytes_in += len(data)
            self.last_rx = time.monotonic()
            for kind, payload in self.parser.feed(data):
                self.dispatch(kind, payload)

    def dispatch(self, kind: bytes, payload: bytes) -> None:
        if kind == proto.TYPE_JPEG:
            try:
                w, h, seq, jpeg = proto.decode_jpeg(payload)
            except Exception as exc:
                log.debug("bad jpeg frame: %s", exc)
                return
            self.frames_in += 1
            self._on_frame(w, h, seq, jpeg)
        elif kind == proto.TYPE_LOG:
            self._on_log(payload.decode("utf-8", errors="replace"))
        elif kind == proto.TYPE_PONG:
            text = payload.decode("utf-8", errors="replace")
            if text != self.board:  # pongs answer every keepalive; only news is worth a callback
                self.board = text
                self._on_pong(text)
