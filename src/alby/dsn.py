"""DSN parsing.

A DSN identifies an Alby ingest target. Format::

    https://<public_key>@<host>/ingest/v1/<app_id>

Only ``public_key`` is used for authentication at the wire level — the
``app_id`` is kept for human-readability and to mirror the backend's DSN
display logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Dsn", "DsnError", "parse_dsn"]


_DSN_RE = re.compile(
    r"^(?P<scheme>https?)://"
    r"(?P<key>[A-Za-z0-9]{16,})@"
    r"(?P<host>[^/]+)"
    r"/ingest/v1/(?P<app>[0-9a-fA-F-]{8,})/?$"
)


class DsnError(ValueError):
    """Raised when a DSN string cannot be parsed."""

    def __init__(self, message: str) -> None:
        super().__init__(f"[alby] invalid DSN: {message}")


@dataclass(frozen=True)
class Dsn:
    """A parsed DSN. Immutable.

    Attributes:
        public_key: The credential shipped on every ingest request.
        app_id: The Alby application UUID. Informational client-side.
        host: The ingest host, including port if any.
        scheme: ``"https"`` or ``"http"``.
        ingest_url: ``POST`` URL for a single event.
        envelope_url: ``POST`` URL for a batch (newline-delimited JSON).
    """

    public_key: str
    app_id: str
    host: str
    scheme: str
    ingest_url: str
    envelope_url: str


def parse_dsn(dsn: str) -> Dsn:
    """Parse a DSN string into a :class:`Dsn`.

    Raises:
        DsnError: If ``dsn`` is empty, not a string, or does not match the
            expected ``https://<key>@<host>/ingest/v1/<app-id>`` shape.
    """
    if not isinstance(dsn, str) or not dsn:
        raise DsnError("empty")

    m = _DSN_RE.match(dsn.strip())
    if not m:
        raise DsnError(
            "unrecognised format. Expected "
            "https://<key>@<host>/ingest/v1/<app-id>"
        )

    scheme = m.group("scheme").lower()
    host = m.group("host")
    public_key = m.group("key")
    app_id = m.group("app")

    base = f"{scheme}://{host}/api/ingest/v1"
    return Dsn(
        public_key=public_key,
        app_id=app_id,
        host=host,
        scheme=scheme,
        ingest_url=f"{base}/events",
        envelope_url=f"{base}/envelope",
    )
