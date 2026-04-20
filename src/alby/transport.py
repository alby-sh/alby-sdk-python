"""HTTP transport.

The public :class:`Transport` protocol is the surface used by :class:`Client`.
The default implementation (:class:`HttpTransport`) ships events through
:mod:`urllib.request` on a background worker thread, with:

* bounded :class:`queue.Queue` (drops on overflow),
* 3 retries at 1s/5s/15s backoff,
* ``Retry-After`` honoured on HTTP 429,
* graceful drain on interpreter shutdown via :func:`atexit`.
"""
from __future__ import annotations

import atexit
import json
import logging
import queue
import sys
import threading
import time
from typing import Any, Dict, Iterable, Optional, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from ._version import __version__
from .event import EventPayload

__all__ = ["Transport", "HttpTransport"]


_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_RETRY_DELAYS_S = (1.0, 5.0, 15.0)
_DEFAULT_QUEUE_SIZE = 100
_SHUTDOWN_SENTINEL: Any = object()
_log = logging.getLogger("alby")


class Transport(Protocol):
    """Pluggable ingest transport."""

    def send(self, payload: EventPayload, public_key: str, ingest_url: str) -> None:
        """Enqueue a payload for delivery. MUST NOT block the caller."""

    def flush(self, timeout_ms: int) -> bool:
        """Drain in-flight events. Return True if the queue fully drained."""

    def close(self) -> None:
        """Signal the worker to exit. Idempotent."""


class _Job:
    __slots__ = ("payload", "public_key", "ingest_url")

    def __init__(self, payload: EventPayload, public_key: str, ingest_url: str) -> None:
        self.payload = payload
        self.public_key = public_key
        self.ingest_url = ingest_url


class HttpTransport:
    """Default HTTP transport built on :mod:`urllib.request`.

    Thread-safe. A single daemon worker thread consumes the queue; the
    ``capture_*`` calls on :class:`Client` only enqueue.
    """

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT_S,
        retry_delays: Iterable[float] = _DEFAULT_RETRY_DELAYS_S,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
        debug: bool = False,
        user_agent: Optional[str] = None,
    ) -> None:
        self._timeout = timeout
        self._retry_delays = tuple(retry_delays)
        self._debug = debug
        self._user_agent = user_agent or f"alby-python/{__version__}"
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._drained = threading.Condition()
        self._inflight = 0  # jobs dequeued but not yet fully processed
        self._worker = threading.Thread(
            target=self._run, name="alby-transport", daemon=True
        )
        self._worker.start()
        # Best-effort drain on interpreter exit.
        atexit.register(self._atexit)

    # ---- public API -------------------------------------------------------

    def send(self, payload: EventPayload, public_key: str, ingest_url: str) -> None:
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait(_Job(payload, public_key, ingest_url))
        except queue.Full:
            self._log("queue full, dropping event")

    def flush(self, timeout_ms: int) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
        with self._drained:
            while True:
                if self._queue.empty() and self._inflight == 0:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._drained.wait(timeout=remaining)

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._queue.put_nowait(_SHUTDOWN_SENTINEL)
        except queue.Full:
            pass

    # ---- internals --------------------------------------------------------

    def _atexit(self) -> None:
        # Give the worker up to 2s to clear whatever's queued, then force exit.
        try:
            self.flush(2000)
        finally:
            self.close()

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get()
            except Exception:  # pragma: no cover
                continue
            if item is _SHUTDOWN_SENTINEL:
                self._notify_drained()
                return
            with self._drained:
                self._inflight += 1
            try:
                self._deliver(item)
            except Exception as exc:  # pragma: no cover - last resort
                self._log(f"worker error: {exc!r}")
            finally:
                with self._drained:
                    self._inflight -= 1
                    self._drained.notify_all()
                try:
                    self._queue.task_done()
                except ValueError:  # pragma: no cover
                    pass

    def _notify_drained(self) -> None:
        with self._drained:
            self._drained.notify_all()

    def _deliver(self, job: _Job) -> None:
        body = json.dumps(job.payload, separators=(",", ":"), default=_json_default).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
            "X-Alby-Dsn": job.public_key,
            "Authorization": f"Alby {job.public_key}",
        }

        attempt = 0
        max_attempts = len(self._retry_delays) + 1
        while attempt < max_attempts:
            if self._stop.is_set():
                return
            try:
                req = urlrequest.Request(
                    job.ingest_url, data=body, headers=headers, method="POST"
                )
                with urlrequest.urlopen(req, timeout=self._timeout) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    if 200 <= status < 300:
                        if self._debug:
                            try:
                                raw = resp.read().decode("utf-8", errors="replace")
                                self._log(f"sent: HTTP {status} {raw[:200]}")
                            except Exception:
                                self._log(f"sent: HTTP {status}")
                        return
                    # Unexpected 2xx? already returned. Anything else, fall through.
                    self._log(f"unexpected HTTP {status}; dropping")
                    return
            except urlerror.HTTPError as e:
                status = e.code
                if status == 429:
                    after = _parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
                    self._sleep(max(1.0, after))
                    attempt += 1
                    continue
                if 500 <= status < 600 and attempt < len(self._retry_delays):
                    self._sleep(self._retry_delays[attempt])
                    attempt += 1
                    continue
                if self._debug:
                    try:
                        body_preview = e.read().decode("utf-8", errors="replace")[:200]
                    except Exception:
                        body_preview = ""
                    self._log(f"dropped: HTTP {status} {body_preview}")
                return
            except (urlerror.URLError, TimeoutError, OSError) as e:
                if attempt < len(self._retry_delays):
                    self._sleep(self._retry_delays[attempt])
                    attempt += 1
                    continue
                self._log(f"giving up after {attempt + 1} attempts: {e!r}")
                return

    def _sleep(self, seconds: float) -> None:
        # Interruptible wait so close() and atexit can short-circuit.
        if self._stop.wait(timeout=seconds):
            return

    def _log(self, msg: str) -> None:
        if self._debug:
            print(f"[alby] {msg}", file=sys.stderr)
        _log.debug(msg)


def _parse_retry_after(value: Optional[str]) -> float:
    if not value:
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _json_default(o: Any) -> Any:
    """Fallback serializer: best-effort stringify unknown types."""
    try:
        return str(o)
    except Exception:  # pragma: no cover
        return repr(o)
