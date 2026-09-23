"""Message bus.

Two implementations of the same small interface:

* :class:`ZmqBusClient` for the real robot: one process per service, a ZeroMQ XSUB/XPUB
  broker (see :mod:`robot.core.broker`) in the middle.
* :class:`LocalHub` / :class:`LocalBusClient` for the simulator and tests: every service in
  one process, one thread each, delivery through thread-safe queues.

Services only ever see :class:`BusClient`, so the same code runs in both worlds.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

import msgpack

from robot.core.messages import Envelope, Payload

log = logging.getLogger(__name__)


def encode(env: Envelope) -> list[bytes]:
    body = msgpack.packb({"ts": env.ts, "src": env.src, "seq": env.seq, "data": env.data}, use_bin_type=True)
    return [env.topic.encode("utf-8"), body]


def decode(frames: list[bytes]) -> Envelope:
    if len(frames) != 2:
        raise ValueError(f"expected 2 frames, got {len(frames)}")
    topic = frames[0].decode("utf-8")
    body = msgpack.unpackb(frames[1], raw=False)
    return Envelope(topic=topic, ts=body["ts"], src=body["src"], seq=body["seq"], data=body["data"])


class BusClient(ABC):
    """Publish and subscribe. One instance per service; not shared across threads."""

    def __init__(self, src: str) -> None:
        self.src = src
        self._seq = 0

    def _next_envelope(self, topic: str, data: Mapping[str, Any]) -> Envelope:
        self._seq += 1
        return Envelope(topic=topic, ts=time.time(), src=self.src, seq=self._seq, data=dict(data))

    @abstractmethod
    def publish(self, topic: str, data: Mapping[str, Any]) -> None: ...

    def publish_payload(self, payload: Payload) -> None:
        self.publish(type(payload).TOPIC, payload.to_data())

    @abstractmethod
    def subscribe(self, *prefixes: str) -> None: ...

    @abstractmethod
    def recv(self, timeout: float | None) -> Envelope | None:
        """Block up to ``timeout`` seconds for the next matching message. None on timeout."""

    @abstractmethod
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# In-process bus
# ---------------------------------------------------------------------------


class LocalHub:
    """Fan-out hub for in-process clients. Thread-safe."""

    def __init__(self, *, record: int = 0) -> None:
        self._clients: list[LocalBusClient] = []
        self._lock = threading.Lock()
        self.record = record
        self.history: list[Envelope] = []

    def client(self, src: str) -> LocalBusClient:
        c = LocalBusClient(self, src)
        with self._lock:
            self._clients.append(c)
        return c

    def _remove(self, client: LocalBusClient) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def _dispatch(self, env: Envelope) -> None:
        with self._lock:
            clients = list(self._clients)
            if self.record:
                self.history.append(env)
                if len(self.history) > self.record:
                    del self.history[: len(self.history) - self.record]
        for c in clients:
            c._deliver(env)


class LocalBusClient(BusClient):
    def __init__(self, hub: LocalHub, src: str) -> None:
        super().__init__(src)
        self._hub = hub
        self._prefixes: tuple[str, ...] = ()
        self._queue: queue.Queue[Envelope] = queue.Queue(maxsize=10_000)
        self._closed = False

    def publish(self, topic: str, data: Mapping[str, Any]) -> None:
        if self._closed:
            return
        env = self._next_envelope(topic, data)
        # Round-trip through msgpack so the local bus has exactly the wire semantics
        # (tuples become lists, numpy scalars are rejected) and tests catch that early.
        self._hub._dispatch(decode(encode(env)))

    def subscribe(self, *prefixes: str) -> None:
        self._prefixes = tuple({*self._prefixes, *prefixes})

    def _deliver(self, env: Envelope) -> None:
        if self._closed or not any(env.topic.startswith(p) for p in self._prefixes):
            return
        try:
            self._queue.put_nowait(env)
        except queue.Full:
            log.warning("%s: dropping %s, subscriber queue full", self.src, env.topic)

    def recv(self, timeout: float | None) -> Envelope | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True
        self._hub._remove(self)


# ---------------------------------------------------------------------------
# ZeroMQ bus
# ---------------------------------------------------------------------------


class ZmqBusClient(BusClient):
    """PUB connected to the broker's XSUB, SUB connected to its XPUB."""

    def __init__(self, src: str, xsub: str, xpub: str, *, context: Any | None = None) -> None:
        super().__init__(src)
        import zmq

        self._ctx = context or zmq.Context.instance()
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 200)
        self._pub.setsockopt(zmq.SNDHWM, 1000)
        self._pub.connect(xsub)
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.setsockopt(zmq.RCVHWM, 1000)
        self._sub.connect(xpub)
        self._poller = zmq.Poller()
        self._poller.register(self._sub, zmq.POLLIN)
        self._zmq = zmq

    def publish(self, topic: str, data: Mapping[str, Any]) -> None:
        env = self._next_envelope(topic, data)
        try:
            self._pub.send_multipart(encode(env), flags=self._zmq.NOBLOCK)
        except self._zmq.Again:
            log.warning("%s: dropping %s, publisher HWM reached", self.src, topic)

    def subscribe(self, *prefixes: str) -> None:
        for p in prefixes:
            self._sub.setsockopt(self._zmq.SUBSCRIBE, p.encode("utf-8"))

    def recv(self, timeout: float | None) -> Envelope | None:
        ms = None if timeout is None else max(0, int(timeout * 1000))
        events = dict(self._poller.poll(ms))
        if self._sub in events:
            return decode(self._sub.recv_multipart())
        return None

    def close(self) -> None:
        self._pub.close()
        self._sub.close()
