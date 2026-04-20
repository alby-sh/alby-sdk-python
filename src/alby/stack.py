"""Convert a Python exception into the protocol's ``exception`` payload.

Stack frames carry the exact wire keys (``filename``, ``function``, ``lineno``,
``colno``, ``pre_context``, ``context_line``, ``post_context``). Source
snippets are read lazily from disk and cached, bounded at 5 lines on each side.
"""
from __future__ import annotations

import os
import sys
import sysconfig
import traceback
from functools import lru_cache
from types import TracebackType
from typing import List, Optional, Tuple

from .event import ExceptionPayload, StackFrame

__all__ = [
    "exception_from_error",
    "frames_from_traceback",
    "is_framework_frame",
]

_CONTEXT_LINES = 5
_MAX_SNIPPET_BYTES = 1_000_000  # don't slurp a multi-MB source file


@lru_cache(maxsize=256)
def _read_lines(filename: str) -> Tuple[str, ...]:
    try:
        if not filename or not os.path.isfile(filename):
            return ()
        if os.path.getsize(filename) > _MAX_SNIPPET_BYTES:
            return ()
        with open(filename, "rb") as f:
            data = f.read()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        return tuple(text.splitlines())
    except OSError:
        return ()


def _snippets(filename: str, lineno: Optional[int]) -> "tuple[list[str], Optional[str], list[str]]":
    if not filename or not lineno or lineno <= 0:
        return [], None, []
    lines = _read_lines(filename)
    if not lines:
        return [], None, []
    # Traceback line numbers are 1-indexed.
    idx = lineno - 1
    if idx < 0 or idx >= len(lines):
        return [], None, []
    pre = lines[max(0, idx - _CONTEXT_LINES):idx]
    ctx = lines[idx]
    post = lines[idx + 1:idx + 1 + _CONTEXT_LINES]
    return list(pre), ctx, list(post)


def _framework_prefixes() -> "tuple[str, ...]":
    """Paths we treat as "framework" — stdlib, site-packages, alby itself."""
    paths: List[str] = []
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        try:
            p = sysconfig.get_path(key)
        except KeyError:
            p = None
        if p:
            paths.append(os.path.realpath(p))
    # Our own package: callers never want alby's internal frames in fingerprint context.
    try:
        import alby as _alby  # pylint: disable=import-outside-toplevel

        alby_root = os.path.dirname(os.path.realpath(_alby.__file__))
        paths.append(alby_root)
    except Exception:  # pragma: no cover
        pass
    return tuple(sorted(set(paths)))


def is_framework_frame(filename: Optional[str]) -> bool:
    """Return True if *filename* lives in stdlib, site-packages, or alby itself."""
    if not filename:
        return False
    try:
        real = os.path.realpath(filename)
    except OSError:
        return False
    if "site-packages" in real or "dist-packages" in real:
        return True
    for prefix in _framework_prefixes():
        if real.startswith(prefix + os.sep) or real == prefix:
            return True
    return False


def frames_from_traceback(tb: Optional[TracebackType]) -> List[StackFrame]:
    """Build the :class:`StackFrame` list for *tb*.

    ``traceback.extract_tb`` yields outer-first. The protocol documents
    "innermost-last" for Python, which is the stdlib's native order, so we
    keep it as-is.
    """
    frames: List[StackFrame] = []
    if tb is None:
        return frames

    extracted = traceback.extract_tb(tb)
    for fs in extracted:
        frame: StackFrame = {}
        if fs.filename:
            frame["filename"] = fs.filename
        if fs.name:
            frame["function"] = fs.name
        if fs.lineno:
            frame["lineno"] = int(fs.lineno)
        # colno exists on FrameSummary only on 3.11+.
        colno = getattr(fs, "colno", None)
        if colno is not None:
            frame["colno"] = int(colno)

        pre, ctx, post = _snippets(fs.filename, fs.lineno)
        if pre:
            frame["pre_context"] = pre
        if ctx is not None:
            frame["context_line"] = ctx
        if post:
            frame["post_context"] = post

        frames.append(frame)
    return frames


def exception_from_error(err: BaseException) -> ExceptionPayload:
    """Build the wire-protocol ``exception`` object from a live exception.

    Works on any :class:`BaseException` (the SDK accepts these even though
    PEP 3134 prefers :class:`Exception`). Handles the caught-with-traceback
    case and the detached-exception case (no ``__traceback__``).
    """
    if not isinstance(err, BaseException):
        # Non-exception values — stringify and move on.
        return {"type": "Error", "value": _coerce_value(err), "frames": []}

    tb = getattr(err, "__traceback__", None) or sys.exc_info()[2]
    frames = frames_from_traceback(tb)
    payload: ExceptionPayload = {
        "type": type(err).__name__,
        "value": str(err),
        "frames": frames,
    }
    return payload


def _coerce_value(v: object) -> str:
    if isinstance(v, str):
        return v
    try:
        import json

        return json.dumps(v)
    except Exception:
        return repr(v)
