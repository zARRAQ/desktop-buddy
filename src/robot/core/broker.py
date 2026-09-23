"""ZeroMQ XSUB/XPUB proxy. One per robot, started first by ``robot.target``."""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)


class Broker:
    def __init__(self, xsub: str, xpub: str) -> None:
        import zmq

        self._zmq = zmq
        self._ctx: Any = zmq.Context.instance()
        self._xsub = self._ctx.socket(zmq.XSUB)
        self._xpub = self._ctx.socket(zmq.XPUB)
        self._xpub.setsockopt(zmq.XPUB_VERBOSE, 1)
        self._xsub.bind(xsub)
        self._xpub.bind(xpub)
        self._control_in = self._ctx.socket(zmq.PAIR)
        self._control_out = self._ctx.socket(zmq.PAIR)
        addr = f"inproc://broker-control-{id(self)}"
        self._control_in.bind(addr)
        self._control_out.connect(addr)
        self._thread: threading.Thread | None = None
        log.info("broker xsub=%s xpub=%s", xsub, xpub)

    def serve_forever(self) -> None:
        """Blocks until :meth:`stop` is called from another thread."""
        try:
            self._zmq.proxy_steerable(self._xsub, self._xpub, None, self._control_in)
        except self._zmq.ContextTerminated:
            pass
        finally:
            self._close()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, name="zmq-broker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with contextlib.suppress(self._zmq.ZMQError):
            self._control_out.send(b"TERMINATE")
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _close(self) -> None:
        for s in (self._xsub, self._xpub, self._control_in):
            s.close(linger=0)

    @property
    def endpoints(self) -> dict[str, Any]:
        return {
            "xsub": self._xsub.getsockopt_string(self._zmq.LAST_ENDPOINT),
            "xpub": self._xpub.getsockopt_string(self._zmq.LAST_ENDPOINT),
        }
