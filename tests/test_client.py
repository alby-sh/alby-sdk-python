"""Tests for :class:`alby.Client` using a fake transport."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

import pytest

from alby.breadcrumbs import BreadcrumbBuffer
from alby.client import Client
from alby.transport import Transport

VALID_DSN = (
    "https://abcdef0123456789abcdef0123456789abcdef0123456789"
    "@alby.sh/ingest/v1/a195c5dc-01c3-46b3-9db4-b22334c179c9"
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: List[Tuple[Dict[str, Any], str, str]] = []
        self.flush_calls: List[int] = []
        self.closed = False

    def send(self, payload: Dict[str, Any], public_key: str, ingest_url: str) -> None:
        self.sent.append((dict(payload), public_key, ingest_url))

    def flush(self, timeout_ms: int) -> bool:
        self.flush_calls.append(timeout_ms)
        return True

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(fake: FakeTransport) -> Client:
    return Client(dsn=VALID_DSN, transport=fake, auto_register=False)


class TestCaptureException:
    def test_captures_an_error_with_stack(self, client: Client, fake: FakeTransport) -> None:
        try:
            raise TypeError("bad thing")
        except TypeError as e:
            event_id = client.capture_exception(e)

        assert event_id is not None
        assert len(fake.sent) == 1
        payload, key, url = fake.sent[0]
        assert key == "abcdef0123456789abcdef0123456789abcdef0123456789"
        assert url == "https://alby.sh/api/ingest/v1/events"
        assert payload["platform"] == "python"
        assert payload["level"] == "error"
        assert payload["event_id"] == event_id
        assert payload["exception"]["type"] == "TypeError"
        assert payload["exception"]["value"] == "bad thing"
        assert len(payload["exception"]["frames"]) >= 1

    def test_capture_exception_without_argument_uses_sys_exc_info(
        self, client: Client, fake: FakeTransport
    ) -> None:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            client.capture_exception()
        assert len(fake.sent) == 1
        assert fake.sent[0][0]["exception"]["type"] == "RuntimeError"

    def test_capture_exception_with_no_active_exception_is_noop(
        self, client: Client, fake: FakeTransport
    ) -> None:
        assert client.capture_exception() is None
        assert fake.sent == []


class TestCaptureMessage:
    def test_captures_a_message(self, client: Client, fake: FakeTransport) -> None:
        event_id = client.capture_message("hello", level="warning")
        assert event_id is not None
        assert len(fake.sent) == 1
        payload, _, _ = fake.sent[0]
        assert payload["message"] == "hello"
        assert payload["level"] == "warning"
        assert "exception" not in payload


class TestScope:
    def test_user_tag_context_and_breadcrumbs_are_attached(
        self, client: Client, fake: FakeTransport
    ) -> None:
        client.set_user({"id": "42", "email": "a@b.c"})
        client.set_tag("region", "eu-west-3")
        client.set_context("billing", {"plan": "pro"})
        client.add_breadcrumb({"type": "http", "message": "GET /"})
        client.capture_message("boom")

        payload, _, _ = fake.sent[0]
        assert payload["contexts"]["user"] == {"id": "42", "email": "a@b.c"}
        assert payload["contexts"]["billing"] == {"plan": "pro"}
        assert "runtime" in payload["contexts"]
        assert "os" in payload["contexts"]
        assert payload["tags"] == {"region": "eu-west-3"}
        assert len(payload["breadcrumbs"]) == 1
        bc = payload["breadcrumbs"][0]
        assert bc["type"] == "http"
        assert bc["message"] == "GET /"
        assert "timestamp" in bc

    def test_set_user_to_none_clears(self, client: Client, fake: FakeTransport) -> None:
        client.set_user({"id": "1"})
        client.set_user(None)
        client.capture_message("x")
        payload, _, _ = fake.sent[0]
        assert "user" not in payload["contexts"]

    def test_non_string_tag_values_ignored(self, client: Client, fake: FakeTransport) -> None:
        client.set_tag("bad", 123)  # type: ignore[arg-type]
        client.set_tag("ok", "v")
        client.capture_message("x")
        payload, _, _ = fake.sent[0]
        assert payload["tags"] == {"ok": "v"}

    def test_set_context_none_removes_key(self, client: Client, fake: FakeTransport) -> None:
        client.set_context("a", {"v": 1})
        client.set_context("a", None)
        client.capture_message("x")
        payload, _, _ = fake.sent[0]
        assert "a" not in payload["contexts"]


class TestSampleRate:
    def test_sample_rate_zero_drops_everything(self, fake: FakeTransport) -> None:
        c = Client(dsn=VALID_DSN, transport=fake, sample_rate=0.0, auto_register=False)
        for _ in range(50):
            c.capture_message("nope")
        assert fake.sent == []

    def test_sample_rate_one_keeps_everything(self, fake: FakeTransport) -> None:
        c = Client(dsn=VALID_DSN, transport=fake, sample_rate=1.0, auto_register=False)
        for _ in range(10):
            c.capture_message("yep")
        assert len(fake.sent) == 10

    def test_sample_rate_clamped(self, fake: FakeTransport) -> None:
        # Values out of [0,1] still produce a working client (clamped).
        c = Client(dsn=VALID_DSN, transport=fake, sample_rate=2.0, auto_register=False)
        c.capture_message("x")
        assert len(fake.sent) == 1


class TestFlush:
    def test_flush_delegates_to_transport(self, client: Client, fake: FakeTransport) -> None:
        assert client.flush(50) is True
        assert fake.flush_calls == [50]


class TestMonitorDecorator:
    def test_monitor_reports_and_reraises(self, client: Client, fake: FakeTransport) -> None:
        @client.monitor
        def boom() -> None:
            raise ValueError("monitored")

        with pytest.raises(ValueError):
            boom()
        assert len(fake.sent) == 1
        assert fake.sent[0][0]["exception"]["type"] == "ValueError"

    def test_monitor_returns_value_on_success(
        self, client: Client, fake: FakeTransport
    ) -> None:
        @client.monitor
        def ok(x: int) -> int:
            return x * 2

        assert ok(3) == 6
        assert fake.sent == []


class TestBreadcrumbBuffer:
    def test_ring_buffer_caps_size(self) -> None:
        buf = BreadcrumbBuffer(maxlen=3)
        for i in range(10):
            buf.add({"message": str(i)})
        snapshot = buf.snapshot()
        assert len(snapshot) == 3
        assert [b["message"] for b in snapshot] == ["7", "8", "9"]

    def test_timestamp_filled_if_missing(self) -> None:
        buf = BreadcrumbBuffer()
        buf.add({"message": "x"})
        assert "timestamp" in buf.snapshot()[0]

    def test_explicit_timestamp_preserved(self) -> None:
        buf = BreadcrumbBuffer()
        buf.add({"message": "x", "timestamp": "2020-01-01T00:00:00Z"})
        assert buf.snapshot()[0]["timestamp"] == "2020-01-01T00:00:00Z"

    def test_thread_safe_concurrent_adds(self) -> None:
        buf = BreadcrumbBuffer(maxlen=500)
        N = 200
        workers = 10

        def w(worker_id: int) -> None:
            for i in range(N):
                buf.add({"message": f"{worker_id}-{i}"})

        threads = [threading.Thread(target=w, args=(i,)) for i in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Expect exactly maxlen entries (or fewer if work total < maxlen).
        total = workers * N
        assert len(buf) == min(500, total)


class TestTransportProtocol:
    def test_fake_transport_satisfies_protocol(self, fake: FakeTransport) -> None:
        # Duck-typed check: the Client already accepts it — this is just
        # belt-and-braces for the Protocol class.
        _: Transport = fake  # noqa: F841


class TestInitAndFacade:
    def test_init_returns_a_client_and_is_reusable(self) -> None:
        import alby

        fake = FakeTransport()
        c = alby.init(dsn=VALID_DSN, transport=fake, auto_register=False)
        try:
            assert alby.get_client() is c
            alby.capture_message("hi")
            assert len(fake.sent) == 1
            alby.set_user({"id": "u"})
            alby.set_tag("k", "v")
            alby.set_context("app", {"v": 1})
            alby.add_breadcrumb({"type": "nav", "message": "/"})
            alby.capture_message("enriched")
            payload, _, _ = fake.sent[-1]
            assert payload["contexts"]["user"]["id"] == "u"
            assert payload["tags"]["k"] == "v"
            assert payload["contexts"]["app"] == {"v": 1}
            assert payload["breadcrumbs"][0]["type"] == "nav"
            assert alby.flush(5) is True
        finally:
            alby.close()
            assert alby.get_client() is None
