"""FastAPI / Starlette ASGI integration.

Usage::

    from fastapi import FastAPI
    from alby.integrations.fastapi import AlbyMiddleware

    app = FastAPI()
    app.add_middleware(AlbyMiddleware)

Starlette / FastAPI are imported lazily — ``AlbyMiddleware`` itself is a
plain ASGI callable so it works on any ASGI server without a hard import.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .. import get_client

__all__ = ["AlbyMiddleware"]


Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


class AlbyMiddleware:
    """Minimal ASGI middleware that captures exceptions and re-raises."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            client = get_client()
            if client is not None:
                try:
                    path = scope.get("path") or ""
                    overrides = {
                        "tags": {"asgi.path": str(path)},
                        "extra": {"asgi_scope_type": scope.get("type")},
                    }
                    client.capture_exception(exc, overrides=overrides)
                except Exception:  # pragma: no cover
                    pass
            raise
