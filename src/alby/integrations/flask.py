"""Flask integration.

Usage::

    from flask import Flask
    from alby.integrations.flask import init_app

    app = Flask(__name__)
    init_app(app)

Flask is imported lazily — if it's not installed, calling :func:`init_app`
raises :class:`ImportError` with an actionable message.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import get_client

__all__ = ["init_app"]


def init_app(app: Any) -> None:
    """Wire up error reporting for a Flask application.

    Hooks into ``got_request_exception`` so errors raised inside view
    functions are reported with a live traceback, and ``teardown_request``
    so even async-looking teardown errors don't escape silently.
    """
    try:
        from flask.signals import got_request_exception  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Flask is required for alby.integrations.flask. "
            "Install it with `pip install flask`."
        ) from e

    def _on_exception(sender: Any, exception: BaseException, **_: Any) -> None:
        client = get_client()
        if client is None:
            return
        try:
            client.capture_exception(exception)
        except Exception:  # pragma: no cover
            pass

    got_request_exception.connect(_on_exception, app)

    @app.teardown_request  # type: ignore[misc]
    def _teardown(exc: Optional[BaseException]) -> None:  # noqa: D401
        if exc is None:
            return
        client = get_client()
        if client is None:
            return
        try:
            client.capture_exception(exc)
        except Exception:  # pragma: no cover
            pass
