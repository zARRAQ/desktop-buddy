"""Camera interface. Every backend delivers RGB uint8 HxWx3 frames."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Frame:
    image: np.ndarray  # HxWx3 uint8 RGB
    ts: float
    seq: int = 0

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass
class CameraInfo:
    backend: str
    device: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    format: str = ""
    notes: list[str] = field(default_factory=list)


class Camera(ABC):
    info: CameraInfo

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def read(self, timeout: float = 1.0) -> Frame | None:
        """Next frame, or None if nothing arrived in ``timeout`` seconds."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    def healthy(self) -> bool:
        return True


def apply_orientation(image: np.ndarray, rotation: int, flip: str) -> np.ndarray:
    if flip in ("h", "hv"):
        image = image[:, ::-1]
    if flip in ("v", "hv"):
        image = image[::-1, :]
    if rotation == 90:
        image = np.rot90(image, k=-1)
    elif rotation == 180:
        image = np.rot90(image, k=2)
    elif rotation == 270:
        image = np.rot90(image, k=1)
    return np.ascontiguousarray(image)
