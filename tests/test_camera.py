from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from robot.core.config import CameraConfig
from robot.hal.camera.base import apply_orientation
from robot.hal.camera.detect import CameraProbes, VideoNode, detect_camera
from robot.hal.camera.letterbox import compute_letterbox, letterbox
from robot.hal.camera.synthetic import SyntheticCamera


@settings(max_examples=200, deadline=None)
@given(
    in_w=st.integers(16, 4000),
    in_h=st.integers(16, 4000),
    out_w=st.integers(32, 1024),
    out_h=st.integers(32, 1024),
    fx=st.floats(0, 1),
    fy=st.floats(0, 1),
)
def test_letterbox_round_trip(in_w, in_h, out_w, out_h, fx, fy):
    lb = compute_letterbox(in_w, in_h, out_w, out_h)
    x, y = fx * in_w, fy * in_h
    mx, my = lb.to_model(x, y)
    assert -1e-6 <= mx <= out_w + 1e-6 and -1e-6 <= my <= out_h + 1e-6  # never outside the tensor
    bx, by = lb.to_original(mx, my)
    assert abs(bx - x) < 1e-6 * max(1, in_w) and abs(by - y) < 1e-6 * max(1, in_h)
    # aspect preserved
    assert lb.scale == pytest.approx(min(out_w / in_w, out_h / in_h))


def test_letterbox_image_pads_grey():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:] = 255
    out, lb = letterbox(img, 64, 64)
    assert out.shape == (64, 64, 3)
    assert out[0, 0].tolist() == [114, 114, 114]  # top pad
    assert out[32, 32].tolist() == [255, 255, 255]
    assert lb.pad_y == pytest.approx(16)


def test_orientation():
    img = np.arange(6, dtype=np.uint8).reshape(2, 3, 1).repeat(3, axis=2)
    assert apply_orientation(img, 90, "none").shape[:2] == (3, 2)
    assert np.array_equal(apply_orientation(img, 180, "none"), img[::-1, ::-1])
    assert np.array_equal(apply_orientation(img, 0, "h"), img[:, ::-1])


def test_detect_cascade():
    p = CameraProbes(csi_cameras=lambda: ["imx708"], video_nodes=lambda: [])
    assert detect_camera(CameraConfig(), p).backend == "csi"
    nodes = [VideoNode("/dev/video0", "cam", True), VideoNode("/dev/video1", "cam meta", False)]
    p2 = CameraProbes(csi_cameras=lambda: [], video_nodes=lambda: nodes)
    d = detect_camera(CameraConfig(), p2)
    assert d.backend == "uvc" and d.device == "/dev/video0"
    assert detect_camera(CameraConfig(device="rtsp://x/y"), p2).backend == "rtsp"
    p3 = CameraProbes(csi_cameras=lambda: [], video_nodes=lambda: [])
    assert detect_camera(CameraConfig(), p3).backend == "null"
    assert detect_camera(CameraConfig(backend="synthetic"), p3).backend == "synthetic"


def test_synthetic_camera_frames():
    cam = SyntheticCamera(width=160, height=120, fps=200)
    cam.open()
    f = cam.read()
    assert f is not None and f.image.shape == (120, 160, 3) and f.image.dtype == np.uint8
    assert f.seq == 1
