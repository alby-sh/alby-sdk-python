# Alby Ingest Protocol — v1

Wire format that every Alby error-tracking SDK (JS/Node, Browser, PHP, Python) speaks when sending events to the backend.

**Status:** stable. Breaking changes bump the major version (v2 will live under `/ingest/v2/…`).

---

## Endpoints

| Method | URL                                   | Purpose                                     |
|-------:|---------------------------------------|---------------------------------------------|
| POST   | `https://alby.sh/api/ingest/v1/events`   | Send one event.                             |
| POST   | `https://alby.sh/api/ingest/v1/envelope` | Send up to 100 events (newline-delimited JSON). |

Staging / dev may use a different host; the DSN itself carries the host.

## DSN (Data Source Name)

The DSN is the credential users paste into their SDK's `init`. Format:

```
https://<PUBLIC_KEY>@alby.sh/ingest/v1/<APP_ID>
```

- `PUBLIC_KEY` — 48 hex chars, unique per app, **safe to ship in public JS**.
- `APP_ID` — UUID; identifies the target app (a backend entity scoped to a user/team project).

The SDK parses the DSN once and keeps the two fields. The path segment (`/ingest/v1/<APP_ID>`) is informational — servers match by public key alone — but SDKs should continue to emit the full path for human-readability.

## Authentication

Send the public key on every request. Three equivalent forms, in priority order:

1. `X-Alby-Dsn: <PUBLIC_KEY>`
2. `Authorization: Alby <PUBLIC_KEY>`
3. `?alby_key=<PUBLIC_KEY>` (query string, last-resort for browsers that strip headers)

Missing / invalid / disabled key → `401 {"error": "invalid_dsn"}`.

## Content-Type

Always `application/json` for `/events`. For `/envelope`, one JSON object per line (`application/x-ndjson` is idiomatic but `text/plain` is accepted).

## Request: single event

```json
{
  "event_id": "11111111-1111-1111-1111-111111111111",
  "timestamp": "2026-04-20T12:34:56.789Z",
  "platform": "node",
  "level": "error",
  "release": "1.4.2",
  "environment": "production",
  "server_name": "worker-07",
  "message": null,

  "exception": {
    "type": "TypeError",
    "value": "Cannot read property 'x' of undefined",
    "frames": [
      {
        "filename": "/app/src/orders.js",
        "function": "processOrder",
        "lineno": 142,
        "colno": 17,
        "pre_context": ["  const total = line.price;"],
        "context_line": "  const tax = order.tax.value;",
        "post_context": ["  return total + tax;"]
      }
    ]
  },

  "breadcrumbs": [
    {
      "timestamp": "2026-04-20T12:34:55.501Z",
      "type": "http",
      "category": "fetch",
      "message": "GET /api/orders/42",
      "data": {"status": 200, "duration_ms": 68}
    }
  ],

  "contexts": {
    "runtime": {"name": "node", "version": "20.11.1"},
    "os":      {"name": "Linux", "version": "5.15.0"},
    "user":    {"id": "u_412", "email": "ada@example.com"}
  },

  "tags":   {"region": "eu-west-3", "customer_tier": "pro"},
  "extra":  {}
}
```

### Field reference

| Field           | Type                | Required | Notes |
|-----------------|---------------------|----------|-------|
| `event_id`      | UUIDv4 string       | **no**, but **strongly recommended** | Client-generated. Used for idempotency — a retry with the same id is a no-op. If omitted, the server generates one but you lose retry safety. |
| `timestamp`     | ISO-8601 / unix     | no       | When the error occurred on the client. Server sets `received_at` independently. |
| `platform`      | string              | no       | `node`, `browser`, `php`, `python`, `other`. ≤ 32 chars. |
| `level`         | enum                | no       | `debug` / `info` / `warning` / `error` / `fatal`. Default `error`. |
| `release`       | string ≤ 100        | no       | Your app version. Used for release tracking + auto-resolve. |
| `environment`   | string ≤ 50         | no       | `production` / `staging` / `dev` / etc. Combined with release to disambiguate. |
| `server_name`   | string ≤ 255        | no       | hostname-like. Pure metadata. |
| `message`       | string              | no       | For `captureMessage()` style reporting. Mutually exclusive with `exception` in practice. |
| `exception`     | object              | no       | See below. If present, trumps `message` for fingerprinting and display. |
| `breadcrumbs`   | array of `{timestamp, type, category, message, data}` | no | Ordered oldest → newest. Cap at 100 entries client-side. |
| `contexts`      | free-form object    | no       | Conventional keys: `runtime`, `os`, `browser`, `device`, `user`. |
| `tags`          | string → string map | no       | Low-cardinality metadata for filtering. |
| `extra`         | free-form object    | no       | High-cardinality metadata that doesn't fit elsewhere. |

### `exception` sub-schema

```ts
{
  type:   string,                   // e.g. "TypeError", "ValueError"
  value?: string,                   // the message
  frames: Frame[]                   // ordered innermost-last (the language convention)
}

Frame = {
  filename?: string,
  function?: string,
  lineno?:   number,
  colno?:    number,
  pre_context?:  string[],    // up to 5 lines before context_line
  context_line?: string,      // the exact line
  post_context?: string[],    // up to 5 lines after
  vars?: object               // rarely filled; PHP/Python can attach locals
}
```

Frame ordering matters only for display. The server fingerprints on the *first non-framework* frames, not on the raw array order, so either convention is fine — but be consistent within one SDK.

## Request: envelope (batch)

Body = newline-delimited JSON:

```
{"event_id":"uuid-1","exception":{...}}
{"event_id":"uuid-2","exception":{...}}
```

Up to 100 lines. Each is validated and ingested independently. The server returns per-line results:

```json
{
  "ok": true,
  "results": [
    {"index": 0, "status": "new_issue",  "issue_id": "…", "event_id": "…"},
    {"index": 1, "status": "accepted",   "issue_id": "…", "event_id": "…"},
    {"index": 2, "error": "invalid_payload", "issues": ["..."]}
  ]
}
```

## Responses

### 202 Accepted — event was accepted

```json
{
  "ok": true,
  "status": "new_issue" | "regression" | "accepted" | "duplicate",
  "issue_id": "UUID",
  "event_id": "UUID"
}
```

- `new_issue` — fingerprint is new to this app.
- `regression` — fingerprint existed, issue had been resolved, is now re-opened.
- `accepted` — fingerprint exists, same status, occurrence counter bumped.
- `duplicate` — `event_id` was already seen; this call was a no-op.

SDKs should treat any 2xx as success and stop retrying. The specific `status` is only useful for test/debug surfaces.

### 400 — invalid payload
```json
{ "error": "invalid_payload", "issues": { "exception.frames.0.lineno": ["..."] } }
```

### 401 — invalid DSN
```json
{ "error": "missing_dsn" | "invalid_dsn", "message": "…" }
```

### 413 — payload too large
`200 KB` per event, `100` events per envelope. SDKs should drop or compress long strings client-side before this limit.

### 429 — rate limited
`Retry-After` header contains seconds. SDKs should drop silently after one retry — error tracking must never block the app.

### 5xx — server error
Treat as retryable. Backoff and try again.

## Rate limits

Per DSN: 1000 events/min. Per source IP: 300 events/min (cumulative across DSNs). Envelopes consume one rate-limit credit per contained event, not per envelope.

For clients with bursty error spikes (e.g. a broken deploy firing 1 error per request), the recommended pattern is:
- in-memory dedup by fingerprint (`type + value + top_frame`) for a rolling 60s window,
- server-side idempotency via `event_id`, so SDK retries never double-count.

## Idempotency

Every event should carry an `event_id` (UUIDv4 generated client-side). The server stores `(app_id, event_id)` uniquely and returns `duplicate` on re-submission, with the same `issue_id` / `event_id` as the original. This makes SDK retry logic safe: on network failure, just retry the same payload.

## HMAC on webhooks (out-of-band but related)

Webhook deliveries from the server include:
```
X-Alby-Event:       issue.created | issue.regression | issue.new_event | issue.resolved
X-Alby-Delivery-Id: UUID
X-Alby-Signature:   sha256=<hex(hmac_sha256(secret, raw_body))>
```

Users verify incoming payloads with the webhook's `secret` (shown once at creation, rotatable).

## Minimum behavior every SDK MUST implement

1. Parse a DSN and extract `public_key` + `app_id`.
2. Install platform-appropriate global handlers (uncaughtException/unhandledRejection, window.onerror, sys.excepthook, Laravel exception handler).
3. Build a payload that passes this schema. The only truly required field at the wire level is authentication — the backend gracefully fills defaults for missing fields — but at minimum set `platform`, `level`, and either `exception.type` + `exception.frames` or `message`.
4. Generate an `event_id` (UUIDv4) per event for retry safety.
5. Send non-blocking — **never** block the calling thread/function on an error send. Queue and flush on process exit (or page unload for browsers).
6. Retry: up to 3 attempts, exponential backoff (1s, 5s, 15s), then drop + log locally.
7. Respect `Retry-After` on 429.

## Minimum SDK ergonomics

```
Alby.init({ dsn: '…' , release?, environment?, sampleRate? })
Alby.captureException(err)
Alby.captureMessage(msg, level?)
Alby.setUser({ id, email, name, ip_address })
Alby.setTag(key, value)
Alby.setContext(key, obj)
Alby.addBreadcrumb({ type, category, message, data })
Alby.flush(timeoutMs?)   // flush queue before shutdown; resolves true if all sent
```

Each SDK adapts the naming to the ecosystem (`snake_case` for Python, `camelCase` for JS, `static` methods on `Alby` class for PHP, etc.).

---

*Last revised: 2026-04-20. Owner: alby-sh.*
