"""Brain service: brain.request in, brain.response out. LLM when reachable, scripted otherwise."""

from __future__ import annotations

import queue
import threading
import time

import httpx

from robot.brain.client import ChatClient
from robot.brain.llama_server import LlamaServerManager
from robot.brain.prompt import parse_reply, system_prompt
from robot.brain.scripted import ScriptedBrain
from robot.core import paths
from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import BrainRequest, BrainResponse, Envelope
from robot.core.service import Service


class BrainService(Service):
    name = "brain"
    subscriptions = ("brain.request",)
    tick_hz = 5.0

    def __init__(
        self,
        config: RobotConfig,
        bus: BusClient,
        *,
        client: ChatClient | None = None,
        scripted: ScriptedBrain | None = None,
    ) -> None:
        super().__init__(config, bus)
        self._client = client
        self.client: ChatClient | None = None
        self.scripted = scripted or ScriptedBrain(config.system.name.capitalize())
        self.manager: LlamaServerManager | None = None
        self._queue: queue.Queue[BrainRequest] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._healthy = False
        self._next_health = 0.0
        self.handled = 0

    def setup(self) -> None:
        cfg = self.config.brain
        if cfg.backend == "scripted":
            self.log.info("scripted brain only (brain.backend=scripted)")
            return
        if cfg.managed == "llama_server":
            self.manager = LlamaServerManager(cfg, paths.models_dir() / "llm" / cfg.model_file)
            try:
                self.manager.start()
                endpoint = self.manager.endpoint
            except (FileNotFoundError, RuntimeError, TimeoutError) as exc:
                self.log.error("%s; falling back to scripted replies", exc)
                self.manager = None
                endpoint = cfg.endpoint
        else:
            endpoint = cfg.endpoint
        self.client = self._client or ChatClient(endpoint, cfg.model, cfg.api_key, cfg.timeout_s)
        self._check_health(force=True)
        self._worker = threading.Thread(target=self._work, name="brain-worker", daemon=True)
        self._worker.start()

    def _check_health(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self._next_health:
            return
        self._next_health = now + 30.0
        was = self._healthy
        self._healthy = self.client.healthy() if self.client is not None else False
        if self._healthy != was or force:
            self.log.info(
                "language model at %s: %s",
                self.client.endpoint if self.client else "-",
                "reachable" if self._healthy else "unreachable, using scripted replies",
            )

    def on_message(self, env: Envelope) -> None:
        if env.topic != BrainRequest.TOPIC:
            return
        req = BrainRequest.model_validate(env.data)
        if self._worker is not None:
            self._queue.put(req)
        else:
            self._respond(req)

    def tick(self, dt: float) -> None:
        if self.client is not None:
            self._check_health()

    def _work(self) -> None:
        while not self.stopping:
            try:
                req = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            self._respond(req)

    def _respond(self, req: BrainRequest) -> None:
        t0 = time.monotonic()
        use_llm = self.client is not None and self._healthy and self.config.brain.backend in ("auto", "openai_compat")
        if use_llm:
            assert self.client is not None
            try:
                messages = [
                    {"role": "system", "content": system_prompt(self.config.brain.persona, req.person, req.facts)}
                ]
                messages += req.history[-self.config.orchestrator.history_turns :]
                messages.append({"role": "user", "content": req.text})
                result = self.client.chat(
                    messages, max_tokens=self.config.brain.max_tokens, temperature=self.config.brain.temperature
                )
                reply = parse_reply(result.text)
                resp = BrainResponse(
                    request_id=req.request_id,
                    say=reply.say,
                    expression=reply.expression,
                    gesture=reply.gesture,
                    remember=reply.remember,
                    backend=f"llm:{result.model or self.config.brain.model}",
                    latency_s=round(result.latency_s, 2),
                )
                self.handled += 1
                self.bus.publish_payload(resp)
                return
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                self.log.warning("LLM call failed (%s); scripted fallback", exc)
                self._healthy = False
        reply = self.scripted.reply(req.text, req.person, req.facts)
        self.handled += 1
        self.bus.publish_payload(
            BrainResponse(
                request_id=req.request_id,
                say=reply.say,
                expression=reply.expression,
                gesture=reply.gesture,
                remember=reply.remember,
                backend="scripted",
                latency_s=round(time.monotonic() - t0, 3),
            )
        )

    def teardown(self) -> None:
        if self._worker is not None:
            self._worker.join(timeout=2.0)
        if self.client is not None:
            self.client.close()
        if self.manager is not None:
            self.manager.stop()
