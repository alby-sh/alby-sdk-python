"""Bounded, thread-safe breadcrumb ring buffer."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List

from .event import Breadcrumb

__all__ = ["BreadcrumbBuffer", "MAX_BREADCRUMBS"]

MAX_BREADCRUMBS = 100


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{datetime.now(tz=timezone.utc).microsecond // 1000:03d}Z"
    )


class BreadcrumbBuffer:
    """A thread-safe ring buffer capped at ``maxlen`` entries (default 100).

    Breadcrumbs flow oldest-first on the wire; we store them that way so the
    caller's order is preserved.
    """

    def __init__(self, maxlen: int = MAX_BREADCRUMBS) -> None:
        self._buf: "deque[Breadcrumb]" = deque(maxlen=maxlen)
        self._lock = RLock()

    def add(self, breadcrumb: Dict[str, Any]) -> None:
        """Append a breadcrumb. Missing ``timestamp`` is filled in for the caller."""
        if not isinstance(breadcrumb, dict):
            return
        bc: Breadcrumb = dict(breadcrumb)  # type: ignore[assignment]
        if "timestamp" not in bc:
            bc["timestamp"] = _now_iso()
        with self._lock:
            self._buf.append(bc)

    def snapshot(self) -> List[Breadcrumb]:
        """Return a copy of the buffer at the moment of the call."""
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def extend(self, items: Iterable[Dict[str, Any]]) -> None:
        for i in items:
            self.add(i)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)
