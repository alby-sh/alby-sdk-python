"""Tests for :mod:`alby.dsn`."""
from __future__ import annotations

import pytest

from alby.dsn import Dsn, DsnError, parse_dsn

VALID = (
    "https://abcdef0123456789abcdef0123456789abcdef0123456789"
    "@alby.sh/ingest/v1/a195c5dc-01c3-46b3-9db4-b22334c179c9"
)


class TestParseDsn:
    def test_parses_a_standard_dsn(self) -> None:
        d = parse_dsn(VALID)
        assert isinstance(d, Dsn)
        assert d.public_key == "abcdef0123456789abcdef0123456789abcdef0123456789"
        assert d.app_id == "a195c5dc-01c3-46b3-9db4-b22334c179c9"
        assert d.host == "alby.sh"
        assert d.scheme == "https"
        assert d.ingest_url == "https://alby.sh/api/ingest/v1/events"
        assert d.envelope_url == "https://alby.sh/api/ingest/v1/envelope"

    def test_accepts_http_for_dev(self) -> None:
        dsn = (
            "http://abcdef0123456789abcdef0123456789abcdef0123456789"
            "@localhost:8000/ingest/v1/a195c5dc-01c3-46b3-9db4-b22334c179c9"
        )
        d = parse_dsn(dsn)
        assert d.scheme == "http"
        assert d.host == "localhost:8000"
        assert d.ingest_url == "http://localhost:8000/api/ingest/v1/events"

    def test_rejects_empty(self) -> None:
        with pytest.raises(DsnError):
            parse_dsn("")

    def test_rejects_non_string(self) -> None:
        with pytest.raises(DsnError):
            parse_dsn(None)  # type: ignore[arg-type]

    def test_rejects_malformed(self) -> None:
        with pytest.raises(DsnError):
            parse_dsn("not a url")

    def test_rejects_missing_key(self) -> None:
        with pytest.raises(DsnError):
            parse_dsn("https://alby.sh/ingest/v1/a195c5dc-01c3-46b3-9db4-b22334c179c9")

    def test_rejects_missing_app_id(self) -> None:
        with pytest.raises(DsnError):
            parse_dsn("https://abcdef0123456789abcdef0123456789abcdef0123456789@alby.sh/ingest/v1/")

    def test_dsn_is_frozen(self) -> None:
        d = parse_dsn(VALID)
        with pytest.raises(Exception):
            d.public_key = "nope"  # type: ignore[misc]
