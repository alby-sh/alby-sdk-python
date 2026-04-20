"""End-to-end smoke test against the live alby.sh backend.

Run with::

    python examples/smoke.py

The script initialises the SDK with a known test DSN, fires one
``capture_exception`` and one ``capture_message``, flushes, and prints the
outcome. Exit code 0 means both events made it to the server with 2xx.
"""
from __future__ import annotations

import sys

import alby

TEST_DSN = (
    "https://5e21bf08520734b6734b95f80af40cba6a7efc6cebddd0df"
    "@alby.sh/ingest/v1/a195c5dc-01c3-46b3-9db4-b22334c179c9"
)


def main() -> int:
    alby.init(
        dsn=TEST_DSN,
        release="sdk-python-e2e",
        environment="test",
        auto_register=False,
        debug=True,
    )

    try:
        raise ValueError("SDK e2e: capture_exception works")
    except Exception as e:
        alby.capture_exception(e)

    alby.capture_message("SDK e2e: capture_message works", level="warning")

    # The alby.sh ingest endpoint can take 5-15 seconds per event under load;
    # two events are processed serially by the worker, so give flush a
    # generous budget.
    ok = alby.flush(60000)
    print("flushed:", ok)
    alby.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
