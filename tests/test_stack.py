"""Tests for :mod:`alby.stack`."""
from __future__ import annotations

import os
import sys

from alby.stack import exception_from_error, frames_from_traceback, is_framework_frame


def _raise_from_here() -> None:
    x = 1  # noqa: F841
    raise ValueError("bad value")


def test_exception_payload_shape() -> None:
    try:
        _raise_from_here()
    except ValueError as e:
        payload = exception_from_error(e)

    assert payload["type"] == "ValueError"
    assert payload["value"] == "bad value"
    assert isinstance(payload["frames"], list)
    assert len(payload["frames"]) >= 1


def test_frames_have_filename_and_function() -> None:
    try:
        _raise_from_here()
    except ValueError as e:
        payload = exception_from_error(e)

    last = payload["frames"][-1]
    assert last["filename"].endswith("test_stack.py")
    assert last["function"] == "_raise_from_here"
    assert isinstance(last["lineno"], int)
    assert last["lineno"] > 0
    # The context_line should be non-empty and contain the raise.
    assert "context_line" in last
    assert "raise" in last["context_line"]


def test_pre_and_post_context_bounded_at_five() -> None:
    try:
        _raise_from_here()
    except ValueError as e:
        payload = exception_from_error(e)
    for frame in payload["frames"]:
        assert len(frame.get("pre_context", [])) <= 5
        assert len(frame.get("post_context", [])) <= 5


def test_colno_on_3_11_plus() -> None:
    try:
        _raise_from_here()
    except ValueError as e:
        payload = exception_from_error(e)
    if sys.version_info >= (3, 11):
        # At least one frame should carry colno.
        assert any("colno" in f for f in payload["frames"])


def test_is_framework_frame_detects_stdlib() -> None:
    # The json module lives in the stdlib — therefore a framework path.
    import json as _json
    assert is_framework_frame(_json.__file__) is True


def test_is_framework_frame_leaves_user_code_alone() -> None:
    # This test file is user code from the SDK's POV.
    assert is_framework_frame(__file__) is False


def test_frames_from_none_traceback_is_empty() -> None:
    assert frames_from_traceback(None) == []


def test_non_exception_value() -> None:
    payload = exception_from_error("a string")  # type: ignore[arg-type]
    assert payload["type"] == "Error"
    assert payload["value"] == "a string"
    assert payload["frames"] == []
