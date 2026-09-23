"""Enrollment: capture several embeddings of one person and store them."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import cv2
import numpy as np

from robot.hal.camera.base import Camera
from robot.memory import Memory
from robot.perception.base import PerceptionBackend

log = logging.getLogger(__name__)

PROMPTS = [
    "Look straight at the camera",
    "Turn your head slowly to the left",
    "Turn your head slowly to the right",
    "Tilt your chin up a little",
    "Tilt your chin down a little",
]


def enroll_from_camera(
    camera: Camera,
    backend: PerceptionBackend,
    memory: Memory,
    name: str,
    *,
    samples: int = 5,
    timeout_s: float = 60.0,
    prompt: Callable[[str], None] = log.info,
    min_face_px: int = 80,
) -> int:
    """Collect ``samples`` embeddings, one per prompt, largest face in frame. Returns count."""
    stored = 0
    deadline = time.monotonic() + timeout_s
    idx = 0
    last_capture = 0.0
    while stored < samples and time.monotonic() < deadline:
        if time.monotonic() - last_capture < 0.8:  # give the person time to move
            frame = camera.read(timeout=0.2)
            continue
        prompt(f"[{stored + 1}/{samples}] {PROMPTS[idx % len(PROMPTS)]}")
        frame = camera.read(timeout=1.0)
        if frame is None:
            continue
        dets = backend.detector.detect(frame.image)
        dets = [d for d in dets if min(d.width, d.height) >= min_face_px]
        if not dets:
            continue
        best = max(dets, key=lambda d: d.width * d.height)
        vec = backend.embedder.embed(frame.image, best)
        memory.add_embedding(name, vec, backend.name)
        stored += 1
        idx += 1
        last_capture = time.monotonic()
    memory.log_event("enroll", f"{name}: {stored} samples via {backend.name}")
    return stored


def enroll_from_images(
    paths: Iterable[Path], backend: PerceptionBackend, memory: Memory, name: str, *, min_face_px: int = 40
) -> int:
    stored = 0
    for p in paths:
        bgr = cv2.imread(str(p))
        if bgr is None:
            log.warning("cannot read %s", p)
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        dets = [d for d in backend.detector.detect(rgb) if min(d.width, d.height) >= min_face_px]
        if len(dets) != 1:
            log.warning("%s: expected exactly one face, found %d; skipped", p, len(dets))
            continue
        vec = backend.embedder.embed(rgb, dets[0])
        memory.add_embedding(name, np.asarray(vec), backend.name)
        stored += 1
    memory.log_event("enroll", f"{name}: {stored} images via {backend.name}")
    return stored
