"""Typed descriptors for the wire payload defined in ``PROTOCOL_V1.md``.

These are plain :mod:`typing` / :mod:`dataclasses` helpers that the
:class:`alby.client.Client` assembles before serialization. No behaviour.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from typing import Literal, TypedDict
except ImportError:  # pragma: no cover - 3.8 fallback
    from typing_extensions import Literal, TypedDict  # type: ignore[assignment]


Level = Literal["debug", "info", "warning", "error", "fatal"]
"""Allowed values for the ``level`` field on the wire."""


class StackFrame(TypedDict, total=False):
    """A single stack frame as it travels over the wire."""

    filename: str
    function: str
    lineno: int
    colno: int
    pre_context: List[str]
    context_line: str
    post_context: List[str]
    vars: Dict[str, Any]


class ExceptionPayload(TypedDict, total=False):
    """``exception`` sub-object of an event."""

    type: str
    value: str
    frames: List[StackFrame]


class Breadcrumb(TypedDict, total=False):
    """A single breadcrumb."""

    timestamp: str
    type: str
    category: str
    message: str
    data: Dict[str, Any]


class UserContext(TypedDict, total=False):
    """Recognised keys on the ``contexts.user`` object."""

    id: Any
    email: str
    name: str
    ip_address: str


class EventPayload(TypedDict, total=False):
    """Full wire payload for a single ingest event.

    See ``PROTOCOL_V1.md`` for the field reference.
    """

    event_id: str
    timestamp: str
    platform: str
    level: Level
    release: str
    environment: str
    server_name: str
    message: Optional[str]
    exception: ExceptionPayload
    breadcrumbs: List[Breadcrumb]
    contexts: Dict[str, Any]
    tags: Dict[str, str]
    extra: Dict[str, Any]
