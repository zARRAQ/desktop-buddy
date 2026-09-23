"""Face service: subscribes to ``face.*``, renders at the panel's frame rate, publishes state."""

from __future__ import annotations

import time

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import Envelope, FaceExpression, FaceLook, FaceMode, FaceState, VoiceSpeaking
from robot.core.service import Service
from robot.face import expressions
from robot.face.animator import FaceAnimator
from robot.face.renderer import FaceRenderer, PanelGeometry, QualityController
from robot.hal.display.base import Display
from robot.hal.display.factory import open_display
from robot.hal.display.pipeline import DisplayPipeline


class FaceService(Service):
    name = "face"
    subscriptions = ("face.", "orchestrator.state", "safety.estop", "voice.speaking")

    def __init__(self, config: RobotConfig, bus: BusClient, *, display: Display | None = None) -> None:
        super().__init__(config, bus)
        self.tick_hz = config.display.target_fps
        self._display = display
        self.pipeline: DisplayPipeline | None = None
        self.animator = FaceAnimator(config.face)
        self.renderer: FaceRenderer | None = None
        self.quality = QualityController(config.display.target_fps, config.display.min_fps)
        self._last_state = 0.0

    def setup(self) -> None:
        disp = self._display or open_display(self.config.display)
        self.pipeline = DisplayPipeline(self.config.display, disp)
        color: str = self.config.display.color
        if color == "auto":
            color = disp.info.color
        geometry = PanelGeometry(
            self.pipeline.logical_w,
            self.pipeline.logical_h,
            shape=disp.info.shape if self.config.display.shape == "auto" else self.config.display.shape,
            color=color,
            scale_mode=self.config.display.scale_mode,
        )
        self.renderer = FaceRenderer(self.config.face, geometry)
        disp.set_brightness(self.config.display.brightness)
        self.log.info(
            "face on %s %dx%d shape=%s color=%s",
            disp.info.backend,
            geometry.width,
            geometry.height,
            geometry.shape,
            geometry.color,
        )

    def on_message(self, env: Envelope) -> None:
        if env.topic == FaceExpression.TOPIC:
            msg = FaceExpression.model_validate(env.data)
            try:
                self.animator.set_expression(msg.name, hold_ms=msg.hold_ms)
            except KeyError:
                self.log.warning("unknown expression %r", msg.name)
        elif env.topic == FaceLook.TOPIC:
            look = FaceLook.model_validate(env.data)
            self.animator.look(look.x, look.y)
        elif env.topic == "face.blink":
            self.animator.blink()
        elif env.topic == FaceMode.TOPIC:
            self.animator.set_mode(FaceMode.model_validate(env.data).mode)
        elif env.topic == "safety.estop":
            self.animator.set_expression("surprise", hold_ms=1500)
        elif env.topic == VoiceSpeaking.TOPIC:
            # The mouth follows the audio, not the orchestrator: it opens when playback starts
            # and closes the moment it ends, even if the orchestrator is slow to notice.
            speaking = VoiceSpeaking.model_validate(env.data)
            if speaking.state == "start":
                self.animator.set_mode("speaking")
            elif self.animator.mode == "speaking":
                self.animator.set_mode("none")
        elif env.topic == "orchestrator.state":
            state = env.data.get("state")
            if state == "sleep":
                self.animator.set_expression("asleep")
            elif state == "listening":
                self.animator.set_expression("listening")
            elif state == "thinking":
                self.animator.set_expression("thinking")
            if state in ("listening", "thinking", "speaking"):
                self.animator.set_mode(state)
            else:
                self.animator.set_mode("none")

    def tick(self, dt: float) -> None:
        if self.pipeline is None or self.renderer is None:
            return
        self.animator.quality.idle_drift = self.quality.idle_drift and self.config.face.idle_drift
        self.animator.quality.saccade_rate = self.quality.saccade_rate
        t0 = time.perf_counter()
        face = self.animator.update(dt)
        self.renderer.render(
            face, self.pipeline.surface, antialias=self.quality.antialias, overlay=self.animator.overlay()
        )
        self.pipeline.present()
        self.quality.record(time.perf_counter() - t0)
        self.tick_hz = self.quality.effective_fps
        for ev in self.pipeline.pump_events():
            if ev.type == 256:  # pygame.QUIT without importing pygame here
                self.request_stop()
        now = time.monotonic()
        if now - self._last_state >= 1.0:
            self._last_state = now
            self.bus.publish_payload(
                FaceState(
                    expression=self.animator.expression,
                    fps=round(self.pipeline.fps, 1),
                    quality_level=self.quality.level,
                    backend=self.pipeline.display.info.backend,
                    mode=self.animator.mode,
                )
            )

    def teardown(self) -> None:
        if self.pipeline is not None:
            self.pipeline.close()


def expression_names() -> list[str]:
    return expressions.names()
