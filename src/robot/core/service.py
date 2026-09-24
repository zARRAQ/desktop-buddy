"""Service base class: a loop that receives bus messages and ticks at a fixed rate.

Every process on the robot is one :class:`Service` subclass running under
:func:`run_service`, which wires logging, signals and systemd's watchdog. In the simulator
the same classes run as threads over a :class:`~robot.core.bus.LocalHub`.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from types import FrameType

from robot.core.bus import BusClient
from robot.core.config import RobotConfig
from robot.core.messages import Envelope, ServiceHeartbeat

log = logging.getLogger(__name__)


def rss_mb() -> float | None:
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:  # noqa: PTH123
            pages = int(fh.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        return None


def sd_notify(state: str) -> None:
    """Minimal sd_notify(3). No-op when not started by systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode("utf-8"))
    except OSError as exc:
        log.debug("sd_notify failed: %s", exc)


class Service:
    """Subclass and override :meth:`setup`, :meth:`on_message`, :meth:`tick`, :meth:`teardown`."""

    name: str = "service"
    tick_hz: float = 10.0
    subscriptions: tuple[str, ...] = ()

    def __init__(self, config: RobotConfig, bus: BusClient) -> None:
        self.config = config
        self.bus = bus
        self.started_at = time.monotonic()
        self._stop = threading.Event()
        self._last_heartbeat = 0.0
        self.log = logging.getLogger(f"robot.{self.name}")
        self._loop_beat = time.monotonic()
        self.hang_timeout_s: float = float(getattr(getattr(config, "system", None), "hang_timeout_s", 20.0))
        self.on_hang: Callable[[], None] = self._exit_for_restart  # tests swap this for a flag

    # -- lifecycle hooks ------------------------------------------------------------
    def setup(self) -> None: ...

    def on_message(self, env: Envelope) -> None: ...

    def tick(self, dt: float) -> None: ...

    def teardown(self) -> None: ...

    # -- control ---------------------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def uptime(self) -> float:
        return time.monotonic() - self.started_at

    def run(self) -> None:
        """Run until :meth:`request_stop`. Exceptions in hooks are logged, not fatal."""
        self.bus.subscribe(*self.subscriptions)
        self.log.info("starting (pid %d)", os.getpid())
        try:
            self.setup()
        except Exception:
            self.log.exception("setup failed")
            raise
        sd_notify("READY=1")
        period = 1.0 / max(self.tick_hz, 0.1)
        next_tick = time.monotonic()
        last = next_tick
        self._loop_beat = time.monotonic()
        threading.Thread(target=self._hang_watchdog, name=f"{self.name}-watchdog", daemon=True).start()
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                self._loop_beat = now
                wait = max(0.0, next_tick - now)
                env = self.bus.recv(timeout=min(wait, 0.25))
                if env is not None:
                    try:
                        self.on_message(env)
                    except Exception:
                        self.log.exception("on_message(%s) failed", env.topic)
                now = time.monotonic()
                if now >= next_tick:
                    dt = now - last
                    last = now
                    next_tick = now + period
                    try:
                        self.tick(dt)
                    except Exception:
                        self.log.exception("tick failed")
                    self._maybe_heartbeat(now)
        finally:
            try:
                self.teardown()
            except Exception:
                self.log.exception("teardown failed")
            self.bus.close()
            self.log.info("stopped")

    def _hang_watchdog(self) -> None:
        """A blocked loop (a display flip that never returns, a driver call that hangs) would
        otherwise leave the robot half alive forever. Dump every thread's stack so the cause
        is in the log, then let the supervisor restart the process."""
        while not self._stop.is_set():
            time.sleep(1.0)
            stalled = time.monotonic() - self._loop_beat
            if not self._stop.is_set() and stalled > self.hang_timeout_s:
                self.log.critical("loop stalled for %.0fs; dumping stacks and exiting for restart", stalled)
                with contextlib.suppress(Exception):  # best effort: the exit matters more
                    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                self.on_hang()
                return

    def _exit_for_restart(self) -> None:
        sd_notify("STOPPING=1")
        os._exit(3)

    def _maybe_heartbeat(self, now: float) -> None:
        if now - self._last_heartbeat < self.config.bus.heartbeat_s:
            return
        self._last_heartbeat = now
        self.bus.publish_payload(
            ServiceHeartbeat(name=self.name, pid=os.getpid(), uptime_s=self.uptime(), rss_mb=rss_mb())
        )
        sd_notify("WATCHDOG=1")


def install_signal_handlers(service: Service) -> None:
    def _handler(signum: int, _frame: FrameType | None) -> None:
        service.log.info("signal %d, stopping", signum)
        service.request_stop()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


class ServiceThread(threading.Thread):
    """Run a service in a thread (simulator, tests)."""

    def __init__(self, service: Service) -> None:
        super().__init__(name=f"svc-{service.name}", daemon=True)
        self.service = service
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.service.run()
        except BaseException as exc:
            self.error = exc
            log.exception("service %s crashed", self.service.name)

    def stop(self, timeout: float = 5.0) -> None:
        self.service.request_stop()
        self.join(timeout=timeout)
