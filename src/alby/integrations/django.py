"""Django integration.

Register the middleware by adding this string to ``MIDDLEWARE`` in your
Django ``settings.py``::

    MIDDLEWARE = [
        # ... Django's own middleware ...
        "alby.integrations.django.AlbyMiddleware",
    ]

Django itself is imported lazily inside this module, so the core ``alby``
package stays dependency-free.
"""
from __future__ import annotations

from typing import Any, Callable

from .. import get_client

__all__ = ["AlbyMiddleware"]


class AlbyMiddleware:
    """Classic Django middleware that reports exceptions and re-raises.

    Django's own exception-handling middleware is installed later in the
    request/response cycle, so our capture happens while the traceback is
    still live.
    """

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        try:
            return self.get_response(request)
        except Exception as exc:
            client = get_client()
            if client is not None:
                try:
                    overrides = {"tags": {"django.request_path": getattr(request, "path", "")}}
                    client.capture_exception(exc, overrides=overrides)
                except Exception:  # pragma: no cover
                    pass
            raise

    # Django also calls this as an ASGI-or-sync introspection hook. Nothing to do.
    def process_exception(self, request: Any, exception: BaseException) -> None:
        client = get_client()
        if client is None:
            return
        try:
            client.capture_exception(exception)
        except Exception:  # pragma: no cover
            pass
