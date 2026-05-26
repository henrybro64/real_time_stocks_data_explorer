# Backend Logging And Diagnostics

The backend now emits structured logs for API requests, provider calls, and background jobs.

## Configuration

Backend logging is controlled with these environment variables:

```text
LOG_LEVEL=INFO
LOG_FORMAT=console   # console | json
REQUEST_ID_HEADER=X-Request-ID
```

- `console` is the default local-dev format. It stays readable while still emitting key/value fields.
- `json` emits one JSON object per line for log aggregation systems.
- `X-Request-ID` is accepted from callers. If the header is missing, the backend generates one.

## Core Fields

Common structured fields include:

- `event`: machine-friendly event name
- `request_id`: request correlation ID
- `user_id`: authenticated or effective backend user when available
- `duration_ms`: elapsed time in milliseconds
- `status_code`: HTTP or upstream status code when relevant
- `provider`: upstream provider or subsystem
- `stock_id`, `ticker`, `market`, `ranking_type`: operation-specific identifiers
- `error_type`, `error`: failure classification without dumping secrets

## API Request Tracing

Every HTTP request emits:

- `http.request.completed`
- `http.request.unhandled_error` for uncaught server exceptions

The response also returns `X-Request-ID` when a normal FastAPI response is produced, so a client can match a failing API call to backend logs.

To trace one request:

1. Copy the `X-Request-ID` response header from the client or browser network panel.
2. Search backend logs for that `request_id`.
3. Follow related events such as `quote.fetch.*`, `stock_analysis.*`, `news.fetch.*`, `rankings.*`, or `portfolio_history.*`.

## Background Jobs

Portfolio history sampling emits structured job logs for:

- sampler start/stop
- batch capture start/end results
- per-user capture results
- quote lookup failures during snapshot generation

These events use `portfolio_history.*` event names and include counts and durations where meaningful.

## Formats

Example `console` log:

```text
2026-05-26T08:00:00.123+00:00 INFO app.main http.request.completed Request completed request_id=req123 path=/api/quotes/US:AAPL method=GET status_code=200 duration_ms=42.18
```

Example `json` log:

```json
{
  "timestamp": "2026-05-26T08:00:00.123+00:00",
  "level": "INFO",
  "logger": "app.main",
  "message": "Request completed",
  "event": "http.request.completed",
  "request_id": "req123",
  "path": "/api/quotes/US:AAPL",
  "method": "GET",
  "status_code": 200,
  "duration_ms": 42.18
}
```
