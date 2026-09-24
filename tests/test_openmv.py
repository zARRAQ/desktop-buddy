"""OpenMV bridge: wire protocol, bitmap layout, bus camera/display, the bridge service and the
face service's LCD mirror. The board itself is faked; nothing here needs hardware."""

from __future__ import annotations

import time

import cv2
import numpy as np
import pygame
import pytest

from robot.core.bus import LocalHub
from robot.core.config import load_config
from robot.core.messages import CameraFrame, DisplayFrame, OpenMvState
from robot.hal.camera.bus import BusCamera
from robot.hal.display.bus import BusDisplay
from robot.hal.openmv import protocol as proto
from robot.hal.openmv.link import OpenMvLink
from robot.openmv.service import OpenMvService


def test_frame_roundtrip_and_resync():
    parser = proto.FrameParser()
    a = proto.encode_jpeg(320, 240, 7, b"\xff\xd8jpeg\xff\xd9")
    b = proto.frame(proto.TYPE_PONG, b"hello")
    stream = b"garbage" + a + b"O" + b
    out = []
    # feed in awkward pieces so headers and payloads straddle reads
    for i in range(0, len(stream), 5):
        out += list(parser.feed(stream[i : i + 5]))
    assert [k for k, _ in out] == [proto.TYPE_JPEG, proto.TYPE_PONG]
    assert proto.decode_jpeg(out[0][1]) == (320, 240, 7, b"\xff\xd8jpeg\xff\xd9")
    assert out[1][1] == b"hello"
    assert parser.resyncs >= 1
    # an absurd length is skipped instead of waiting forever
    bad = proto.MAGIC + b"J" + (2**31).to_bytes(4, "little") + b"xx"
    assert list(parser.feed(bad + b)) == [(proto.TYPE_PONG, b"hello")]


@pytest.mark.parametrize("width,height", [(128, 160), (100, 7), (33, 2), (32, 1), (1, 1)])
def test_bitmap_layout_roundtrip(width, height):
    rng = np.random.default_rng(width * 31 + height)
    mask = rng.random((height, width)) > 0.5
    bits = proto.pack_bitmap(mask)
    assert len(bits) == height * proto.words_per_row(width) * 4
    assert np.array_equal(proto.unpack_bitmap(bits, width, height), mask)
    # pixel x lives in bit x & 31 of little-endian word x >> 5, exactly what OpenMV's BINARY wants
    if width >= 33:
        single = np.zeros((1, width), dtype=bool)
        single[0, 32] = True
        words = np.frombuffer(proto.pack_bitmap(single), dtype="<u4")
        assert words[0] == 0 and words[1] == 1


def test_face_payload_and_rgb565():
    assert proto.rgb565((255, 255, 255)) == 0xFFFF
    assert proto.rgb565((255, 0, 0)) == 0xF800
    assert proto.rgb565((0, 0, 255)) == 0x001F
    kind, payload = next(iter(proto.FrameParser().feed(proto.encode_face(4, 1, 0xF800, 0, b"\x05\x00\x00\x00"))))
    assert kind == proto.TYPE_FACE
    assert proto.decode_face(payload) == (4, 1, 0xF800, 0, b"\x05\x00\x00\x00")


def _jpeg(w=64, h=48, color=(10, 200, 30)) -> bytes:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color[::-1]  # cv2 wants BGR
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return bytes(buf)


def test_bus_camera_decodes_pushed_jpegs():
    cam = BusCamera(320, 240)
    cam.open()
    assert cam.read() is None
    cam.push({"width": 64, "height": 48, "seq": 3, "jpeg": _jpeg(), "ts": 1.0})
    f = cam.read()
    assert f is not None and f.width == 64 and f.height == 48 and f.seq == 3
    r, g, b = (int(v) for v in f.image.reshape(-1, 3).mean(axis=0))
    assert g > 150 and r < 60 and b < 80  # came back as RGB, not BGR
    assert cam.read() is None  # consumed
    cam.push({"jpeg": b"not a jpeg"})
    assert cam.decode_errors == 1 and cam.healthy


def test_bus_display_publishes_one_bit_frames_and_rate_limits():
    pygame.init()
    sent: list[DisplayFrame] = []
    disp = BusDisplay(128, 160, publish=sent.append, eye_color=(62, 224, 230), max_fps=1000)  # type: ignore[arg-type]
    disp.open()
    assert disp.info.color == "mono1" and disp.info.backend == "bus"
    surf = pygame.Surface((128, 160))
    surf.fill((0, 0, 0))
    pygame.draw.rect(surf, (255, 255, 255), pygame.Rect(10, 20, 30, 40))
    disp.push(surf)
    assert len(sent) == 1
    f = sent[0]
    assert (f.width, f.height, f.fg) == (128, 160, proto.rgb565((62, 224, 230)))
    mask = proto.unpack_bitmap(f.bits, 128, 160)
    assert mask.sum() == 30 * 40 and mask[20, 10] and not mask[19, 10]
    slow = BusDisplay(8, 8, publish=sent.append, max_fps=1)  # type: ignore[arg-type]
    for _ in range(5):
        slow.push(pygame.Surface((8, 8)))
    assert slow.sent == 1 and slow.skipped == 4


class FakeLink(OpenMvLink):
    """Behaves like an open serial link; records what the Pi sends, lets tests inject frames."""

    def __init__(self) -> None:
        super().__init__("/dev/fake", on_frame=lambda *a: None)
        self.sent: list[bytes] = []
        self.opened = False

    @property
    def open_(self) -> bool:
        return self.opened

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def send(self, data: bytes) -> bool:
        self.sent.append(data)
        self.frames_out += 1
        return True

    def inject(self, data: bytes) -> None:
        for kind, payload in self.parser.feed(data):
            self.dispatch(kind, payload)


def test_bridge_service_moves_frames_both_ways(hub: LocalHub, tmp_path):
    cfg = load_config(
        config_dir=tmp_path, use_env=False, overrides=["openmv.enabled=true", "openmv.width=640", "openmv.height=480"]
    )
    link = FakeLink()
    svc = OpenMvService(cfg, hub.client("openmv"), link=link)
    svc.bus.subscribe(*svc.subscriptions)
    probe = hub.client("probe")
    probe.subscribe(CameraFrame.TOPIC, OpenMvState.TOPIC)
    svc.setup()
    # setup configured the board and pinged it
    kinds = [next(iter(proto.FrameParser().feed(d)))[0] for d in link.sent]
    assert kinds == [proto.TYPE_CONFIG, proto.TYPE_PING]
    assert link.sent[0][proto.HEADER.size :][:4] == (640).to_bytes(2, "little") + (480).to_bytes(2, "little")
    # a camera frame from the board becomes a camera.frame message
    link.inject(proto.encode_jpeg(64, 48, 9, _jpeg()))
    link.inject(proto.frame(proto.TYPE_PONG, b"buddy-openmv 1 sensor=MT9M114"))
    svc.tick(0.0)
    env = probe.recv(0.5)
    assert env is not None and env.topic == CameraFrame.TOPIC
    msg = CameraFrame.model_validate(env.data)
    assert (msg.width, msg.height, msg.seq) == (64, 48, 9) and msg.jpeg[:2] == b"\xff\xd8"
    assert svc.board.startswith("buddy-openmv")
    # a display.frame message goes down to the LCD
    probe.publish_payload(DisplayFrame(width=32, height=2, fg=0xFFFF, bg=0, bits=b"\x01\x00\x00\x00" * 2))
    while (e := svc.bus.recv(0)) is not None:
        svc.on_message(e)
    assert next(iter(proto.FrameParser().feed(link.sent[-1])))[0] == proto.TYPE_FACE
    # state is published about once a second
    svc._last_state = 0.0
    svc.tick(0.0)
    states = []
    while (env := probe.recv(0.2)) is not None:
        if env.topic == OpenMvState.TOPIC:
            states.append(OpenMvState.model_validate(env.data))
    assert states and states[-1].connected and states[-1].board.startswith("buddy-openmv")
    svc.teardown()
    assert not link.opened


def test_bridge_service_survives_a_missing_board(hub: LocalHub, tmp_path):
    cfg = load_config(
        config_dir=tmp_path, use_env=False, overrides=["openmv.enabled=true", "openmv.port=/dev/does-not-exist"]
    )
    svc = OpenMvService(cfg, hub.client("openmv"))
    svc.setup()  # must not raise
    assert svc.link is not None and not svc.link.open_
    svc.tick(0.0)
    svc.teardown()


def test_face_service_bus_display_and_lcd_mirror(hub: LocalHub, tmp_path):
    from robot.face.service import FaceService
    from robot.hal.display.null import NullDisplay

    probe = hub.client("probe")
    probe.subscribe(DisplayFrame.TOPIC)
    # 1. the LCD is the main display
    cfg = load_config(
        config_dir=tmp_path,
        use_env=False,
        overrides=["display.backend=bus", "openmv.enabled=true", "openmv.lcd_width=96", "openmv.lcd_height=64"],
    )
    svc = FaceService(cfg, hub.client("face"))
    svc.setup()
    assert svc.renderer is not None and svc.renderer.panel.width == 96 and svc.renderer.panel.is_mono
    svc.tick(0.05)
    env = probe.recv(0.5)
    assert env is not None
    f = DisplayFrame.model_validate(env.data)
    assert (f.width, f.height) == (96, 64) and proto.unpack_bitmap(f.bits, 96, 64).sum() > 0
    svc.teardown()
    # 2. another display is the main one; the LCD mirrors it
    cfg2 = load_config(config_dir=tmp_path, use_env=False, overrides=["openmv.enabled=true"])
    svc2 = FaceService(cfg2, hub.client("face2"), display=NullDisplay(240, 240))
    svc2.setup()
    assert svc2.mirror is not None
    while probe.recv(0) is not None:
        pass
    svc2.tick(0.05)
    env = probe.recv(0.5)
    assert env is not None and DisplayFrame.model_validate(env.data).width == 128
    svc2.teardown()


def test_perception_service_feeds_bus_camera(hub: LocalHub, tmp_path):
    from robot.core.messages import Envelope
    from robot.perception.service import PerceptionService

    cfg = load_config(config_dir=tmp_path, use_env=False, overrides=["camera.backend=bus"])
    cam = BusCamera(320, 240)
    svc = PerceptionService(cfg, hub.client("perception"), camera=cam)
    assert CameraFrame.TOPIC in svc.subscriptions
    svc.on_message(
        Envelope(
            topic=CameraFrame.TOPIC,
            ts=time.time(),
            src="openmv",
            seq=1,
            data=CameraFrame(width=64, height=48, seq=1, jpeg=_jpeg()).model_dump(),
        )
    )
    assert cam.received == 1 and cam.read() is not None


def test_openmv_config_and_doctor():
    from pathlib import Path

    from robot.doctor import SKIP, check_openmv

    cfg = load_config(config_dir=Path("/nonexistent"), use_env=False)
    assert not cfg.openmv.enabled and cfg.openmv.lcd_width == 128
    assert check_openmv(cfg).status == SKIP
    with pytest.raises(ValueError):
        load_config(config_dir=Path("/nonexistent"), use_env=False, overrides=["openmv.jpeg_quality=5"])


def test_find_openmv_drive(tmp_path):
    from robot.cli.openmv_cmd import find_openmv_drive

    media = tmp_path / "media"
    (media / "USB STICK").mkdir(parents=True)
    d = media / "NO NAME"
    d.mkdir()
    (d / "main.py").write_text("# hi")
    (d / "README.txt").write_text("Thank you for buying an OpenMV Cam!")
    assert find_openmv_drive([media]) == d
    (media / "OPENMV").mkdir()
    assert find_openmv_drive([media]) == media / "NO NAME"  # sorted: "NO NAME" before "OPENMV"; both valid
    assert find_openmv_drive([tmp_path / "nothing"]) is None


def test_open_camera_direct_falls_back_cleanly(tmp_path):
    from robot.hal.openmv.direct import open_camera_direct

    # not enabled: the configured camera, untouched
    cfg = load_config(config_dir=tmp_path, use_env=False, overrides=["camera.backend=synthetic"])
    cam = open_camera_direct(cfg)
    assert cam.info.backend == "synthetic" and not cam.via_openmv
    cam.close()
    # enabled but no board on the given port: a null camera with the reason, no exception
    cfg2 = load_config(
        config_dir=tmp_path,
        use_env=False,
        overrides=["openmv.enabled=true", "openmv.port=/dev/nope", "camera.backend=bus"],
    )
    cam2 = open_camera_direct(cfg2)
    assert cam2.info.backend == "null" and not cam2.via_openmv and cam2.info.notes
    cam2.close()
