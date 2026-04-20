"""Best-effort collection of static runtime/OS/device metadata.

All functions are pure and side-effect-free; they're called once at client
construction and cached by the caller.
"""
from __future__ import annotations

import platform as _platform
import socket
import sys
from typing import Any, Dict

__all__ = ["runtime_context", "os_context", "server_name"]


def runtime_context() -> Dict[str, Any]:
    """Return ``{"name": "cpython", "version": "3.11.4"}``-style info."""
    impl = _platform.python_implementation().lower() or "cpython"
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    return {"name": impl, "version": version}


def os_context() -> Dict[str, Any]:
    """Return ``{"name": "Darwin", "version": "23.1.0", ...}`` from :mod:`platform`."""
    out: Dict[str, Any] = {
        "name": _platform.system() or "unknown",
        "version": _platform.release() or "",
    }
    machine = _platform.machine()
    if machine:
        out["machine"] = machine
    return out


def server_name() -> str:
    """Return :func:`socket.gethostname`, or ``''`` if it fails."""
    try:
        return socket.gethostname() or ""
    except OSError:  # pragma: no cover - very unusual
        return ""
