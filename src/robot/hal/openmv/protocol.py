"""Wire format shared with ``openmv/main.py`` (the MicroPython side keeps its own copy).

Every message is ``b"OM"`` + one type byte + little-endian uint32 payload length + payload.
Types:

* ``J`` camera JPEG, OpenMV -> Pi: ``u16 width, u16 height, u32 seq, jpeg bytes``
* ``F`` face bitmap, Pi -> OpenMV: ``u16 width, u16 height, u16 fg565, u16 bg565, bits``
  where ``bits`` is OpenMV's BINARY image layout: each row is ``ceil(width / 32)`` little-endian
  uint32 words, pixel ``x`` in bit ``x & 31`` of word ``x >> 5``. The OpenMV wraps that buffer
  as an image with no copying, which is what keeps 12 frames a second affordable there.
* ``C`` camera config, Pi -> OpenMV: ``u16 width, u16 height, u8 jpeg quality, u8 fps, u8 leds``
* ``P`` ping (any payload), ``Q`` pong (``u8`` text describing the board)
* ``L`` log line, OpenMV -> Pi (``u8`` text)
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

import numpy as np
import pygame

MAGIC = b"OM"
HEADER = struct.Struct("<2scI")
MAX_PAYLOAD = 4 * 1024 * 1024

TYPE_JPEG = b"J"
TYPE_FACE = b"F"
TYPE_CONFIG = b"C"
TYPE_PING = b"P"
TYPE_PONG = b"Q"
TYPE_LOG = b"L"


def frame(kind: bytes, payload: bytes = b"") -> bytes:
    if len(kind) != 1:
        raise ValueError("type must be one byte")
    return HEADER.pack(MAGIC, kind, len(payload)) + payload


class FrameParser:
    """Feed it bytes as they arrive; it yields complete ``(type, payload)`` messages and
    resynchronises on the magic after garbage or a dropped byte."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.resyncs = 0

    def feed(self, data: bytes) -> Iterator[tuple[bytes, bytes]]:
        self._buf += data
        while True:
            start = self._buf.find(MAGIC)
            if start < 0:
                if len(self._buf) > 1:
                    self.resyncs += 1
                self._buf = self._buf[-1:]  # a lone "O" might start the next magic
                return
            if start > 0:
                self.resyncs += 1
                del self._buf[:start]
            if len(self._buf) < HEADER.size:
                return
            _, kind, length = HEADER.unpack_from(self._buf, 0)
            if length > MAX_PAYLOAD:
                self.resyncs += 1
                del self._buf[:1]
                continue
            end = HEADER.size + length
            if len(self._buf) < end:
                return
            payload = bytes(self._buf[HEADER.size : end])
            del self._buf[:end]
            yield kind, payload


# --- payload encoders / decoders -------------------------------------------------------

_JPEG_HDR = struct.Struct("<HHI")
_FACE_HDR = struct.Struct("<HHHH")
_CONFIG = struct.Struct("<HHBBB")


def encode_config(width: int, height: int, quality: int, fps: int, leds: bool) -> bytes:
    return frame(TYPE_CONFIG, _CONFIG.pack(width, height, quality, fps, 1 if leds else 0))


def decode_jpeg(payload: bytes) -> tuple[int, int, int, bytes]:
    w, h, seq = _JPEG_HDR.unpack_from(payload, 0)
    return w, h, seq, payload[_JPEG_HDR.size :]


def encode_jpeg(width: int, height: int, seq: int, jpeg: bytes) -> bytes:
    """Used by tests and the simulator to fake the OpenMV side."""
    return frame(TYPE_JPEG, _JPEG_HDR.pack(width, height, seq) + jpeg)


def encode_face(width: int, height: int, fg565: int, bg565: int, bits: bytes) -> bytes:
    return frame(TYPE_FACE, _FACE_HDR.pack(width, height, fg565, bg565) + bits)


def decode_face(payload: bytes) -> tuple[int, int, int, int, bytes]:
    w, h, fg, bg = _FACE_HDR.unpack_from(payload, 0)
    return w, h, fg, bg, payload[_FACE_HDR.size :]


def rgb565(color: tuple[int, int, int]) -> int:
    r, g, b = color
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def words_per_row(width: int) -> int:
    return (width + 31) // 32


def pack_bitmap(mask: np.ndarray) -> bytes:
    """``mask`` is HxW bool (True = lit). Returns OpenMV BINARY layout, see module doc."""
    h, w = mask.shape
    nwords = words_per_row(w)
    padded = np.zeros((h, nwords * 32), dtype=np.uint32)
    padded[:, :w] = mask.astype(np.uint32)
    weights = (np.uint32(1) << np.arange(32, dtype=np.uint32)).astype(np.uint32)
    words = (padded.reshape(h, nwords, 32) * weights).sum(axis=2, dtype=np.uint64).astype("<u4")
    return words.tobytes()


def unpack_bitmap(bits: bytes, width: int, height: int) -> np.ndarray:
    """Inverse of :func:`pack_bitmap`; the OpenMV does this implicitly, tests do it explicitly."""
    nwords = words_per_row(width)
    words = np.frombuffer(bits, dtype="<u4").reshape(height, nwords).astype(np.uint32)
    shifts = np.arange(32, dtype=np.uint32)
    bitsarr = (words[:, :, None] >> shifts) & 1
    return bitsarr.reshape(height, nwords * 32)[:, :width].astype(bool)


def surface_to_mask(surface: pygame.Surface, threshold: int = 96) -> np.ndarray:
    """Lit pixels of a rendered face: luminance above ``threshold``. HxW bool."""
    arr = pygame.surfarray.array3d(surface)  # WxHx3
    lum = arr[..., 0].astype(np.uint16) * 3 + arr[..., 1].astype(np.uint16) * 6 + arr[..., 2].astype(np.uint16)
    return (lum > threshold * 10).T
