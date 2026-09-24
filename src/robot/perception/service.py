"""Perception service: camera -> detect -> track -> embed -> match -> bus."""

from __future__ import annotations

import time

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import CameraFrame, Envelope, FaceObs, PerceptionFaces, PersonEvent
from robot.core.service import Service
from robot.hal.camera.base import Camera, Frame
from robot.hal.camera.factory import open_camera
from robot.memory import Memory
from robot.perception.base import PerceptionBackend
from robot.perception.factory import open_backend
from robot.perception.matcher import IdentityIndex
from robot.perception.tracker import Track, Tracker


class PerceptionService(Service):
    name = "perception"
    subscriptions = ("memory.changed", "perception.enroll", CameraFrame.TOPIC)

    def __init__(
        self,
        config: RobotConfig,
        bus: BusClient,
        *,
        camera: Camera | None = None,
        backend: PerceptionBackend | None = None,
        memory: Memory | None = None,
    ) -> None:
        super().__init__(config, bus)
        self.tick_hz = max(5.0, float(config.camera.fps))
        self._camera = camera
        self._backend = backend
        self._memory = memory
        self.camera: Camera | None = camera  # a bus camera needs frames before setup runs
        self.backend: PerceptionBackend | None = None
        self.index: IdentityIndex | None = None
        self.tracker = Tracker(
            lost_after_s=config.perception.lost_after_s, votes_needed=config.perception.recognize_votes
        )
        self._frame_count = 0
        self._last_publish = 0.0
        self._fps_times: list[float] = []
        self.frames_processed = 0
        self._enroll: tuple[str, int | None, int] | None = None  # name, track_id, remaining

    def setup(self) -> None:
        self.camera = self._camera or open_camera(self.config.camera)
        if self.camera.info.backend == "null":
            self.log.warning("no camera: perception idle")
            return
        self.backend = self._backend or open_backend(self.config.perception)
        if self.backend is None:
            self.log.warning("no perception backend: perception idle (camera still open for `robot camera test`)")
            return
        memory = self._memory or Memory(self.config.memory_path())
        threshold = self.config.perception.match_threshold
        if threshold is None:
            threshold = self.backend.embedder.default_threshold
        self.index = IdentityIndex(memory, self.backend.name, threshold)
        self.log.info(
            "matching against %d embeddings (%s, threshold %.3f)", self.index.size, self.backend.name, threshold
        )

    def on_message(self, env: Envelope) -> None:
        if env.topic == CameraFrame.TOPIC:
            from robot.hal.camera.bus import BusCamera

            if isinstance(self.camera, BusCamera):
                self.camera.push(env.data)
        elif env.topic == "memory.changed" and self.index is not None:
            self.index.refresh(force=True)
        elif env.topic == "perception.enroll":
            name = str(env.data.get("name", "")).strip()
            if name:
                tid = env.data.get("track_id")
                self._enroll = (name, int(tid) if tid is not None else None, int(env.data.get("samples", 5)))
                self.log.info("enrolling %r from live video (%d samples)", name, self._enroll[2])

    def tick(self, dt: float) -> None:
        if self.camera is None or self.backend is None or self.index is None:
            return
        frame = self.camera.read(timeout=0.2)
        if frame is None:
            return
        self.process(frame)

    def process(self, frame: Frame) -> None:
        assert self.backend is not None and self.index is not None
        cfg = self.config.perception
        self._frame_count += 1
        now = time.monotonic()
        if self._frame_count % cfg.detect_every_n:
            return
        dets = [d for d in self.backend.detector.detect(frame.image) if min(d.width, d.height) >= cfg.min_face_px]
        tracks, lost = self.tracker.update(dets, now)
        for t in lost:
            self.bus.publish_payload(PersonEvent(event="left", track_id=t.track_id, name=t.name))
        if self._enroll is not None and tracks:
            self._enroll_step(frame, tracks)
        for t in tracks:
            if not t.announced_appeared:
                t.announced_appeared = True
                self.bus.publish_payload(PersonEvent(event="appeared", track_id=t.track_id))
            if t.wants_embedding():
                vec = self.backend.embedder.embed(frame.image, t.det)
                name, score = self.index.match(vec)
                t.vote(name, score, self.tracker.votes_needed)
                if t.name and t.announced_name != t.name:
                    t.announced_name = t.name
                    self.index.memory.touch_seen(t.name)
                    self.bus.publish_payload(
                        PersonEvent(event="recognized", track_id=t.track_id, name=t.name, score=t.match_score)
                    )
        self.frames_processed += 1
        self._fps_times.append(now)
        self._fps_times = [t for t in self._fps_times if t > now - 2.0]
        if now - self._last_publish >= 1.0 / max(cfg.publish_hz, 0.5):
            self._last_publish = now
            self._publish_faces(frame, now)

    def _enroll_step(self, frame: Frame, tracks: list[Track]) -> None:
        assert self.backend is not None and self.index is not None and self._enroll is not None
        name, tid, remaining = self._enroll
        target = next((t for t in tracks if t.track_id == tid), None) if tid is not None else None
        if target is None:
            target = max(tracks, key=lambda t: t.det.width * t.det.height)
        vec = self.backend.embedder.embed(frame.image, target.det)
        self.index.memory.add_embedding(name, vec, self.backend.name)
        remaining -= 1
        target.name = name
        target.votes[name] = self.tracker.votes_needed
        if remaining <= 0:
            self._enroll = None
            self.index.refresh(force=True)
            self.bus.publish("perception.enrolled", {"name": name, "backend": self.backend.name})
            self.bus.publish("memory.changed", {"what": "embeddings", "name": name})
            self.log.info("enrolled %r", name)
        else:
            self._enroll = (name, tid, remaining)

    def _publish_faces(self, frame: Frame, now: float) -> None:
        assert self.backend is not None
        w, h = frame.width, frame.height
        faces = []
        for t in self.tracker.active():
            x1, y1, x2, y2 = t.det.normalised(w, h)
            faces.append(
                FaceObs(
                    track_id=t.track_id,
                    bbox=(x1, y1, x2, y2),
                    center=((x1 + x2) / 2, (y1 + y2) / 2),
                    score=t.det.score,
                    name=t.name,
                    match_score=t.match_score,
                )
            )
        fps = 0.0
        if len(self._fps_times) > 1:
            fps = (len(self._fps_times) - 1) / max(1e-6, self._fps_times[-1] - self._fps_times[0])
        self.bus.publish_payload(
            PerceptionFaces(frame_ts=frame.ts, faces=faces, backend=self.backend.name, fps=round(fps, 1))
        )

    def teardown(self) -> None:
        if self.camera is not None:
            self.camera.close()
        if self.backend is not None:
            self.backend.close()
