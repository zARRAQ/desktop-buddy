from __future__ import annotations

import threading
import time

import pytest

from robot.core.broker import Broker
from robot.core.bus import LocalHub, ZmqBusClient, decode, encode
from robot.core.messages import ALL_TOPICS, Envelope, FaceExpression, PerceptionFaces
from robot.core.service import Service, ServiceThread


def test_encode_decode_round_trip():
    env = Envelope(topic="a.b", ts=1.5, src="t", seq=3, data={"x": [1, 2], "y": {"z": "w"}})
    assert decode(encode(env)) == env


def test_topics_are_unique_and_dotted():
    assert len(set(ALL_TOPICS)) == len(ALL_TOPICS)
    assert all("." in t for t in ALL_TOPICS)


def test_local_bus_prefix_matching(hub: LocalHub):
    a = hub.client("a")
    b = hub.client("b")
    b.subscribe("face.")
    a.publish_payload(FaceExpression(name="happy"))
    a.publish("perception.faces", {"faces": []})
    env = b.recv(timeout=1)
    assert env is not None and env.topic == "face.expression" and env.data["name"] == "happy"
    assert b.recv(timeout=0.05) is None
    assert [e.topic for e in hub.history] == ["face.expression", "perception.faces"]


def test_local_bus_wire_semantics(hub: LocalHub):
    """Tuples become lists after msgpack, exactly like ZeroMQ."""
    a = hub.client("a")
    a.subscribe("perception.")
    a.publish_payload(PerceptionFaces(frame_ts=1.0, faces=[]))
    a.publish("perception.raw", {"t": (1, 2)})
    first = a.recv(1)
    second = a.recv(1)
    assert first is not None and second is not None
    assert second.data["t"] == [1, 2]


class Echo(Service):
    name = "echo"
    subscriptions = ("ping.",)
    tick_hz = 50

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.ticks = 0

    def on_message(self, env):
        self.bus.publish("pong.reply", {"n": env.data["n"]})

    def tick(self, dt):
        self.ticks += 1


def test_service_thread_round_trip(config, hub: LocalHub):
    svc = Echo(config, hub.client("echo"))
    t = ServiceThread(svc)
    t.start()
    probe = hub.client("probe")
    probe.subscribe("pong.", "service.heartbeat")
    time.sleep(0.05)
    probe.publish("ping.x", {"n": 7})
    seen: dict[str, Envelope] = {}
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not {"pong.reply", "service.heartbeat"} <= set(seen):
        env = probe.recv(timeout=0.5)
        if env is not None:
            seen[env.topic] = env
    assert seen["pong.reply"].data["n"] == 7
    assert seen["service.heartbeat"].data["name"] == "echo"
    t.stop()
    assert t.error is None
    assert svc.ticks > 0


@pytest.mark.timeout(20)
def test_zmq_broker_round_trip():
    broker = Broker("tcp://127.0.0.1:*", "tcp://127.0.0.1:*")
    ep = broker.endpoints
    broker.start()
    try:
        sub = ZmqBusClient("sub", ep["xsub"], ep["xpub"])
        pub = ZmqBusClient("pub", ep["xsub"], ep["xpub"])
        sub.subscribe("face.")
        # Slow joiner: keep publishing until the subscription has propagated.
        got = None
        deadline = time.monotonic() + 10
        while got is None and time.monotonic() < deadline:
            pub.publish("face.expression", {"name": "happy"})
            pub.publish("other.topic", {"x": 1})
            got = sub.recv(timeout=0.1)
        assert got is not None
        assert got.topic == "face.expression"
        assert got.src == "pub"
        sub.close()
        pub.close()
    finally:
        broker.stop()


def test_zmq_broker_stops_cleanly():
    broker = Broker("tcp://127.0.0.1:*", "tcp://127.0.0.1:*")
    t = threading.Thread(target=broker.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    broker.stop()
    t.join(timeout=3)
    assert not t.is_alive()
