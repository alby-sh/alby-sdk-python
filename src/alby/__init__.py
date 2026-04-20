"""Alby error-tracking SDK for Python.

Typical usage::

    import os
    import alby

    alby.init(
        dsn=os.environ["ALBY_DSN"],
        release="1.4.2",
        environment="production",
    )

    try:
        work()
    except Exception as exc:
        alby.capture_exception(exc)

    alby.flush(2000)

See :mod:`alby.integrations` for Django / Flask / FastAPI hooks.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, TypeVar, cast

from ._version import __version__
from .client import Client
from .dsn import Dsn, DsnError, parse_dsn
from .event import (
    Breadcrumb,
    EventPayload,
    ExceptionPayload,
    Level,
    StackFrame,
    UserContext,
)
from .stack import exception_from_error
from .transport import HttpTransport, Transport

__all__ = [
    "__version__",
    "Client",
    "Dsn",
    "DsnError",
    "parse_dsn",
    "HttpTransport",
    "Transport",
    "Breadcrumb",
    "EventPayload",
    "ExceptionPayload",
    "Level",
    "StackFrame",
    "UserContext",
    "exception_from_error",
    "init",
    "get_client",
    "capture_exception",
    "capture_message",
    "set_user",
    "set_tag",
    "set_context",
    "add_breadcrumb",
    "flush",
    "close",
    "monitor",
]

F = TypeVar("F", bound=Callable[..., Any])

_default_client: Optional[Client] = None


def init(
    *,
    dsn: str,
    release: str = "",
    environment: Optional[str] = None,
    sample_rate: float = 1.0,
    platform: str = "python",
    server_name: Optional[str] = None,
    auto_register: bool = True,
    debug: bool = False,
    transport: Optional[Transport] = None,
    max_breadcrumbs: int = 100,
) -> Client:
    """Initialise the default SDK client and return it.

    Calling :func:`init` a second time replaces the default client; the
    previous client's transport is closed to avoid zombie worker threads.
    """
    global _default_client
    if _default_client is not None:
        try:
            _default_client.close()
        except Exception:  # pragma: no cover
            pass
    _default_client = Client(
        dsn=dsn,
        release=release,
        environment=environment,
        sample_rate=sample_rate,
        platform=platform,
        server_name_=server_name,
        auto_register=auto_register,
        debug=debug,
        transport=transport,
        max_breadcrumbs=max_breadcrumbs,
    )
    return _default_client


def get_client() -> Optional[Client]:
    """Return the default :class:`Client`, or ``None`` if :func:`init` wasn't called."""
    return _default_client


def capture_exception(
    error: Optional[BaseException] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Capture an exception on the default client."""
    if _default_client is None:
        return None
    return _default_client.capture_exception(error, overrides=overrides)


def capture_message(message: str, level: Level = "info") -> Optional[str]:
    """Capture a non-exception event on the default client."""
    if _default_client is None:
        return None
    return _default_client.capture_message(message, level=level)


def set_user(user: Optional[Dict[str, Any]]) -> None:
    if _default_client is not None:
        _default_client.set_user(user)


def set_tag(key: str, value: str) -> None:
    if _default_client is not None:
        _default_client.set_tag(key, value)


def set_context(key: str, value: Optional[Dict[str, Any]]) -> None:
    if _default_client is not None:
        _default_client.set_context(key, value)


def add_breadcrumb(breadcrumb: Dict[str, Any]) -> None:
    if _default_client is not None:
        _default_client.add_breadcrumb(breadcrumb)


def flush(timeout_ms: int = 2000) -> bool:
    """Drain the transport queue. Returns ``True`` if fully drained."""
    if _default_client is None:
        return True
    return _default_client.flush(timeout_ms)


def close() -> None:
    """Close the default client, uninstall handlers, and stop the worker."""
    global _default_client
    if _default_client is not None:
        try:
            _default_client.close()
        finally:
            _default_client = None


def monitor(func: F) -> F:
    """Decorator: report any exception raised by *func* before re-raising.

    Safe to apply before :func:`init` — in that case the decorator becomes a
    no-op wrapper until a client is registered, then starts reporting
    automatically.
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            if _default_client is not None:
                try:
                    _default_client.capture_exception(exc)
                except Exception:  # pragma: no cover
                    pass
            raise

    return cast(F, wrapper)
