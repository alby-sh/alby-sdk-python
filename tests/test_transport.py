"""Transport end-to-end tests against a local :mod:`http.server`."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

import pytest

from alby.client import Client
from alby.transport import HttpTransport

VALID_DSN_TEMPLATE = (
    "http://abcdef0123456789abcdef0123456789abcdef0123456789"
    "@localhost:{port}/ingest/v1/a195c5dc-01c3-46b3-9db4-b22334c179c9"
)


class _IngestHandler(BaseHTTPRequestHandler):
    # Set by the fixture.
    received: List[Dict[str, Any]] = []
    headers_received: List[Dict[str, str]] = []
    status_code: int = 202
    retry_after_header: Optional[str] = None
    fail_until_attempt: int = 0
    attempt_counter: List[int] = [0]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return  # silence

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"__raw__": raw.decode("utf-8", errors="replace")}
        self.__class__.received.append(payload)
        self.__class__.headers_received.append(
            {k.lower(): v for k, v in self.headers.items()}
        )

        self.__class__.attempt_counter[0] += 1
        if self.__class__.attempt_counter[0] <= self.__class__.fail_until_attempt:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"simulated"}')
            return

        if self.__class__.status_code == 429:
            self.send_response(429)
            if self.__class__.retry_after_header is not None:
                self.send_header("Retry-After", self.__class__.retry_after_header)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"rate_limited"}')
            return

        self.send_response(self.__class__.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "ok": True,
                    "status": "accepted",
                    "issue_id": "i1",
                    "event_id": payload.get("event_id", "e1"),
                }
            ).encode("utf-8")
        )


@pytest.fixture()
def server():
    # Fresh handler class per test so state doesn't leak.
    class Handler(_IngestHandler):
        received: List[Dict[str, Any]] = []
        headers_received: List[Dict[str, str]] = []
        status_code = 202
        retry_after_header: Optional[str] = None
        fail_until_attempt = 0
        attempt_counter = [0]

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, Handler, port
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


def _dsn_for(port: int) -> str:
    return VALID_DSN_TEMPLATE.format(port=port)


def test_transport_sends_event_with_correct_headers_and_body(server):
    srv, Handler, port = server
    t = HttpTransport(debug=False)
    try:
        client = Client(dsn=_dsn_for(port), transport=t, auto_register=False)
        client.capture_message("hello-transport", level="info")
        assert t.flush(5000) is True

        assert len(Handler.received) == 1
        body = Handler.received[0]
        # Protocol required-ish fields.
        assert body["platform"] == "python"
        assert body["message"] == "hello-transport"
        assert body["level"] == "info"
        assert "event_id" in body and len(body["event_id"]) == 36
        assert "timestamp" in body
        assert body["contexts"]["runtime"]["name"] in ("cpython", "pypy")

        # Headers.
        h = Handler.headers_received[0]
        assert h["content-type"].startswith("application/json")
        assert h["x-alby-dsn"] == "abcdef0123456789abcdef0123456789abcdef0123456789"
        assert h["authorization"].lower().startswith("alby ")
        assert "alby-python" in h["user-agent"]
    finally:
        t.close()


def test_transport_retries_on_500_then_succeeds(server):
    srv, Handler, port = server
    Handler.fail_until_attempt = 1  # first attempt -> 500, second -> 202

    # Shrink retry delays so the test is fast.
    t = HttpTransport(retry_delays=(0.05, 0.1, 0.2), debug=False)
    try:
        client = Client(dsn=_dsn_for(port), transport=t, auto_register=False)
        client.capture_message("retry-me")
        assert t.flush(5000) is True
        # Server saw two attempts (the 500 and the 202).
        assert Handler.attempt_counter[0] == 2
    finally:
        t.close()


def test_transport_respects_retry_after_on_429(server):
    srv, Handler, port = server
    Handler.status_code = 429
    Handler.retry_after_header = "1"

    t = HttpTransport(retry_delays=(0.05, 0.05, 0.05), debug=False)
    start = time.monotonic()
    try:
        client = Client(dsn=_dsn_for(port), transport=t, auto_register=False)
        client.capture_message("slow down")
        # flush should return within a reasonable bound — we just care that
        # Retry-After was honoured (the transport sleeps ~1s between attempts).
        drained = t.flush(8000)
        elapsed = time.monotonic() - start
        assert drained is True
        # At least one ~1s wait happened.
        assert elapsed >= 0.9
        # All attempts exhausted.
        assert Handler.attempt_counter[0] >= 2
    finally:
        t.close()


def test_transport_drops_on_4xx_without_retry(server):
    srv, Handler, port = server
    Handler.status_code = 400

    t = HttpTransport(retry_delays=(0.05,), debug=False)
    try:
        client = Client(dsn=_dsn_for(port), transport=t, auto_register=False)
        client.capture_message("broken")
        assert t.flush(5000) is True
        # Exactly one attempt.
        assert Handler.attempt_counter[0] == 1
    finally:
        t.close()


def test_transport_queue_overflow_drops_silently(server):
    srv, Handler, port = server
    Handler.status_code = 202

    # queue_size=1 + a tiny retry_delays makes it easy to cause overflow if we
    # enqueue fast enough. The key contract is "doesn't raise", plus "the
    # inflight + queued never exceeds the cap".
    t = HttpTransport(queue_size=2, retry_delays=(0.01, 0.02), debug=False)
    try:
        client = Client(dsn=_dsn_for(port), transport=t, auto_register=False)
        for i in range(25):
            client.capture_message(f"event-{i}")
        # Doesn't matter how many were dropped, but flush must succeed.
        assert t.flush(5000) is True
        assert Handler.attempt_counter[0] >= 1
        assert Handler.attempt_counter[0] <= 25
    finally:
        t.close()
