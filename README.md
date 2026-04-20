# alby-report

[![PyPI version](https://img.shields.io/pypi/v/alby-report.svg?color=3776ab&logo=pypi&logoColor=white)](https://pypi.org/project/alby-report/)
[![PyPI downloads](https://img.shields.io/pypi/dm/alby-report.svg?color=3776ab)](https://pypi.org/project/alby-report/)
[![Python versions](https://img.shields.io/pypi/pyversions/alby-report.svg?color=3776ab&logo=python&logoColor=white)](https://pypi.org/project/alby-report/)
[![CI](https://github.com/alby-sh/alby-python/actions/workflows/ci.yml/badge.svg)](https://github.com/alby-sh/alby-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

Official [Alby](https://alby.sh) error-tracking SDK for Python.

Captures uncaught exceptions and anything you explicitly report, then ships them to your Alby project where an AI agent can auto-open a fix task.

Zero runtime dependencies. Python 3.8+.

## Install

```bash
pip install alby-report
```

## Use

```python
import os
import alby

alby.init(
    dsn=os.environ['ALBY_DSN'],    # https://<key>@alby.sh/ingest/v1/<app-id>
    release='1.4.2',
    environment='production',
)

# Uncaught exceptions are sent automatically (sys.excepthook + threading.excepthook).

# Manual report:
try:
    do_thing()
except Exception as exc:
    alby.capture_exception(exc)

# Non-error events:
alby.capture_message('Failed to acquire lease', level='warning')

# Enrich:
alby.set_user({'id': 'u_412', 'email': 'ada@example.com'})
alby.set_tag('region', 'eu-west-3')
alby.set_context('billing_tenant', {'plan': 'pro', 'seats': 12})
alby.add_breadcrumb({'type': 'http', 'message': 'GET /api/orders/42'})

# Decorator:
@alby.monitor
def charge_customer(customer_id: str) -> None:
    ...

# Before exit (e.g. in a short-lived CLI):
alby.flush(2000)
```

## Options

| Option            | Type           | Default         | Notes |
|-------------------|----------------|-----------------|-------|
| `dsn`             | `str`          | - (required)    | The DSN from your Alby app settings. |
| `release`         | `str`          | `''`            | Your build version. Enables release tracking / auto-resolve. |
| `environment`     | `str`          | `$ALBY_ENV` or `'production'` | `production` / `staging` / `dev` / anything. |
| `sample_rate`     | `float`        | `1.0`           | Fraction of events actually sent. |
| `platform`        | `str`          | `'python'`      | Override auto-detection. |
| `server_name`     | `str`          | `socket.gethostname()` | Attached to every event. |
| `auto_register`   | `bool`         | `True`          | Install `sys.excepthook` + `threading.excepthook` handlers. |
| `transport`       | `Transport`    | `HttpTransport` | Custom delivery (tests, batching, filesystem spool). |
| `debug`           | `bool`         | `False`         | Log SDK diagnostics to stderr. |
| `max_breadcrumbs` | `int`          | `100`           | Ring buffer size. |

## Framework integrations

### Django

In `settings.py`:

```python
MIDDLEWARE = [
    # ... Django's own middleware ...
    'alby.integrations.django.AlbyMiddleware',
]
```

Initialise Alby early in your app setup (e.g. `settings.py` or `wsgi.py`).

### Flask

```python
from flask import Flask
from alby.integrations.flask import init_app as alby_init_app

app = Flask(__name__)
alby_init_app(app)
```

### FastAPI / Starlette

```python
from fastapi import FastAPI
from alby.integrations.fastapi import AlbyMiddleware

app = FastAPI()
app.add_middleware(AlbyMiddleware)
```

## Transport

* `urllib.request` under the hood (zero deps).
* Non-blocking: a bounded `queue.Queue(maxsize=100)` plus a daemon worker thread. `capture_*` only enqueues.
* Retries: 3 attempts at 1s / 5s / 15s backoff.
* Honours `Retry-After` on HTTP 429.
* Best-effort drain on interpreter exit via `atexit`.

For synchronous delivery, inject your own `Transport` via `alby.init(transport=...)`.

## Wire protocol

This SDK speaks the [Alby Ingest Protocol v1](./PROTOCOL_V1.md). If you're writing a new SDK (different runtime, different language) start there.

## Links

- Website: [alby.sh](https://alby.sh)
- Report issues: [GitHub Issues](https://github.com/alby-sh/alby-python/issues)
- Other SDKs: [alby-js](https://github.com/alby-sh/alby-js) · [alby-browser](https://github.com/alby-sh/alby-browser) · [alby-php](https://github.com/alby-sh/alby-php)

## License

MIT (c) Alby.
