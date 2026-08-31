# Backend logging

Structured logs for API requests, provider calls, and background jobs.

## Configuration

```text
LOG_LEVEL=INFO
LOG_FORMAT=console   # console | json
REQUEST_ID_HEADER=X-Request-ID
```

- `console` — readable local default with key/value fields
- `json` — one JSON object per line for aggregation
- Missing `X-Request-ID` → backend generates one; echoed on responses when possible

## Common fields

`event`, `request_id`, `user_id`, `duration_ms`, `status_code`, `provider`, plus operation ids (`stock_id`, `market`, `ranking_type`, …). Failures use `error_type` / `error` without secrets.

## Tracing one request

1. Copy `X-Request-ID` from the client or browser network panel.
2. Search backend logs for that `request_id`.
3. Follow related events (`http.request.*`, `quote.fetch.*`, `rankings.*`, `portfolio_history.*`, …).

HTTP layer emits `http.request.completed` and `http.request.unhandled_error`.

## Background jobs

Portfolio history, alert evaluation, rankings/archive samplers, and Taiwan refresh emit structured `event` names with counts and durations where useful.

## Format examples

Console:

```text
2026-05-26T08:00:00.123+00:00 INFO app.main http.request.completed request_id=req123 path=/api/quotes/US:AAPL status_code=200 duration_ms=42.18
```

JSON:

```json
{"event":"http.request.completed","request_id":"req123","path":"/api/quotes/US:AAPL","status_code":200,"duration_ms":42.18}
```
