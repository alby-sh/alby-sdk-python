"""The :class:`Client` — assembles events and hands them to the transport.

Instances are usually created via :func:`alby.init` which stores one as the
default singleton. For rare multi-DSN setups, construct additional ``Client``
objects yourself.
"""
from __future__ import annotations

import os
import random
import sys
import threading
import uuid
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Callable, Dict, Optional, Type, TypeVar, cast

from .breadcrumbs import BreadcrumbBuffer
from .context import os_context, runtime_context, server_name
from .dsn import Dsn, parse_dsn
from .event import EventPayload, Level, UserContext
from .stack import exception_from_error
from .transport import HttpTransport, Transport

__all__ = ["Client"]


F = TypeVar("F", bound=Callable[..., Any])


def _now_iso() -> str:
    now = datetime.now(tz=timezone.utc)
    # Millisecond precision, Z suffix — matches the protocol example.
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _detect_environment() -> str:
    return (
        os.environ.get("ALBY_ENV")
        or os.environ.get("PYTHON_ENV")
        or os.environ.get("ENV")
        or "production"
    )


def _clamp01(n: float) -> float:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return 1.0
    if v != v:  # NaN
        return 1.0
    return max(0.0, min(1.0, v))


class Client:
    """Main SDK client.

    Usually constructed implicitly through :func:`alby.init`. Direct
    instantiation is supported for tests or multi-tenant use cases.
    """

    def __init__(
        self,
        *,
        dsn: str,
        release: str = "",
        environment: Optional[str] = None,
        sample_rate: float = 1.0,
        platform: str = "python",
        server_name_: Optional[str] = None,
        auto_register: bool = True,
        debug: bool = False,
        transport: Optional[Transport] = None,
        max_breadcrumbs: int = 100,
    ) -> None:
        if not dsn:
            raise ValueError("[alby] init: dsn is required")

        self._dsn: Dsn = parse_dsn(dsn)
        self._release = release or ""
        self._environment = environment or _detect_environment()
        self._sample_rate = _clamp01(sample_rate)
        self._platform = platform or "python"
        self._server_name = server_name_ if server_name_ is not None else server_name()
        self._debug = bool(debug)
        self._transport: Transport = transport or HttpTransport(debug=self._debug)

        self._breadcrumbs = BreadcrumbBuffer(maxlen=max_breadcrumbs)
        self._user: Optional[UserContext] = None
        self._tags: Dict[str, str] = {}
        self._contexts: Dict[str, Any] = {}
        self._scope_lock = threading.RLock()

        # Cache mostly-static contexts.
        self._runtime_ctx = runtime_context()
        self._os_ctx = os_context()

        self._prev_excepthook: Optional[Callable[..., Any]] = None
        self._prev_threading_hook: Optional[Callable[..., Any]] = None
        self._auto_registered = False
        if auto_register:
            self._install_handlers()

    # ---- properties ------------------------------------------------------

    @property
    def dsn(self) -> Dsn:
        return self._dsn

    @property
    def debug(self) -> bool:
        return self._debug

    # ---- capture --------------------------------------------------------

    def capture_exception(
        self,
        error: Optional[BaseException] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Capture an exception.

        If ``error`` is ``None``, :func:`sys.exc_info` is consulted. The
        return value is the generated ``event_id``, or ``None`` if the event
        was sampled out.
        """
        if error is None:
            _, current, _ = sys.exc_info()
            if current is None:
                return None
            error = current

        exception_payload = exception_from_error(error)
        partial: Dict[str, Any] = {
            "exception": exception_payload,
            "level": (overrides or {}).get("level", "error"),
        }
        if overrides:
            for k, v in overrides.items():
                if k == "level":
                    continue
                partial[k] = v
        return self._dispatch(partial)

    def capture_message(self, message: str, level: Level = "info") -> Optional[str]:
        """Capture a string event (no exception attached)."""
        return self._dispatch({"message": str(message), "level": level})

    # ---- scope ---------------------------------------------------------

    def set_user(self, user: Optional[Dict[str, Any]]) -> None:
        with self._scope_lock:
            self._user = None if user is None else cast(UserContext, dict(user))

    def set_tag(self, key: str, value: str) -> None:
        if not isinstance(key, str) or not isinstance(value, str):
            return
        with self._scope_lock:
            self._tags[key] = value

    def set_context(self, key: str, value: Optional[Dict[str, Any]]) -> None:
        if not isinstance(key, str):
            return
        with self._scope_lock:
            if value is None:
                self._contexts.pop(key, None)
            else:
                self._contexts[key] = dict(value)

    def add_breadcrumb(self, crumb: Dict[str, Any]) -> None:
        self._breadcrumbs.add(crumb)

    # ---- lifecycle -----------------------------------------------------

    def flush(self, timeout_ms: int = 2000) -> bool:
        return self._transport.flush(timeout_ms)

    def close(self) -> None:
        try:
            self._transport.close()
        finally:
            self._uninstall_handlers()

    # ---- decorator -----------------------------------------------------

    def monitor(self, func: F) -> F:
        """Decorator: report any exception raised by *func* before re-raising."""
        import functools

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - we reraise
                self.capture_exception(exc)
                raise

        return cast(F, wrapper)

    # ---- internals -----------------------------------------------------

    def _dispatch(self, partial: Dict[str, Any]) -> Optional[str]:
        if self._sample_rate < 1.0 and random.random() > self._sample_rate:
            return None

        event_id = uuid.uuid4().hex
        # Canonical UUID-with-dashes form, as the protocol example shows.
        event_id = str(uuid.UUID(event_id))

        payload: Dict[str, Any] = {
            "event_id": event_id,
            "timestamp": _now_iso(),
            "platform": self._platform,
            "level": partial.get("level", "error"),
        }
        if self._release:
            payload["release"] = self._release
        if self._environment:
            payload["environment"] = self._environment
        if self._server_name:
            payload["server_name"] = self._server_name
        if "message" in partial and partial["message"] is not None:
            payload["message"] = partial["message"]
        if "exception" in partial and partial["exception"] is not None:
            payload["exception"] = partial["exception"]

        breadcrumbs = self._breadcrumbs.snapshot()
        if breadcrumbs:
            payload["breadcrumbs"] = breadcrumbs

        contexts = self._build_contexts()
        if contexts:
            payload["contexts"] = contexts

        with self._scope_lock:
            if self._tags:
                payload["tags"] = dict(self._tags)

        if "extra" in partial and partial["extra"]:
            payload["extra"] = partial["extra"]

        # Passthrough for any other user-supplied override keys.
        for k, v in partial.items():
            if k in ("message", "exception", "level", "extra") or v is None:
                continue
            payload[k] = v

        self._transport.send(cast(EventPayload, payload), self._dsn.public_key, self._dsn.ingest_url)
        return event_id

    def _build_contexts(self) -> Dict[str, Any]:
        with self._scope_lock:
            out: Dict[str, Any] = {k: dict(v) if isinstance(v, dict) else v for k, v in self._contexts.items()}
            if self._user:
                out["user"] = dict(self._user)
        out.setdefault("runtime", dict(self._runtime_ctx))
        out.setdefault("os", dict(self._os_ctx))
        return out

    # ---- auto handlers -------------------------------------------------

    def _install_handlers(self) -> None:
        if self._auto_registered:
            return
        self._auto_registered = True

        self._prev_excepthook = sys.excepthook

        def alby_excepthook(
            exc_type: Type[BaseException],
            exc_value: BaseException,
            tb: Optional[TracebackType],
        ) -> None:
            try:
                # Attach traceback to the exception if missing so our stack
                # walker can pick it up.
                if exc_value is not None and tb is not None and exc_value.__traceback__ is None:
                    try:
                        exc_value = exc_value.with_traceback(tb)
                    except Exception:  # pragma: no cover
                        pass
                self.capture_exception(exc_value, overrides={"level": "fatal"})
                # Best-effort flush before the interpreter dies.
                self.flush(2000)
            except Exception:  # pragma: no cover
                pass
            # Chain to the previously installed hook.
            prev = self._prev_excepthook
            if prev and prev is not sys.__excepthook__:
                try:
                    prev(exc_type, exc_value, tb)
                    return
                except Exception:  # pragma: no cover
                    pass
            sys.__excepthook__(exc_type, exc_value, tb)

        sys.excepthook = alby_excepthook

        # threading.excepthook lands on Python 3.8+.
        if hasattr(threading, "excepthook"):
            self._prev_threading_hook = threading.excepthook  # type: ignore[attr-defined]

            def alby_threading_hook(args: Any) -> None:
                try:
                    if getattr(args, "exc_value", None) is not None:
                        self.capture_exception(args.exc_value, overrides={"level": "error"})
                except Exception:  # pragma: no cover
                    pass
                prev = self._prev_threading_hook
                if prev is not None:
                    try:
                        prev(args)
                        return
                    except Exception:  # pragma: no cover
                        pass

            threading.excepthook = alby_threading_hook  # type: ignore[attr-defined]

    def _uninstall_handlers(self) -> None:
        if not self._auto_registered:
            return
        try:
            if self._prev_excepthook is not None:
                sys.excepthook = self._prev_excepthook
        except Exception:  # pragma: no cover
            pass
        try:
            if self._prev_threading_hook is not None and hasattr(threading, "excepthook"):
                threading.excepthook = self._prev_threading_hook  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
        self._auto_registered = False
