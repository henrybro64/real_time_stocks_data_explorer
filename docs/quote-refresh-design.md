# Quote and ranking refresh design

How market data is fetched, cached, and exposed, and what the app does when it is late or missing. Defaults below come from `backend/app/core/config.py` and are configurable.

## Where things live

| Concern | Module(s) |
| --- | --- |
| Quote read paths, freshness envelope | `services/quote_service.py`, `api/routes/quotes.py` |
| Quote cache (Redis or per-process memory) | `services/quote_redis_cache.py` |
| Background quote refresher | `services/quote_refresher_service.py` |
| U.S. adapter | `services/providers/us_provider.py`: Yahoo chart → Yahoo v7 quote; SEC ticker catalog; Yahoo history |
| Shared Yahoo quote math | `services/providers/yahoo_chart.py`: prior-session close, coherent change, required market time |
| Taiwan adapter | `services/providers/tw_provider.py`: TWSE MIS latest trade (`services/tw_mis_trade.py`) → Yahoo fallback; TWSE / TPEx daily history; Yahoo 5-minute intraday |
| Rankings | `services/rankings_snapshot_service.py` (snapshot store), `rankings_service.py` (builders), `ranking_data_providers.py`, `rankings_external_source.py` (Yahoo pre-ranked lists), `tw_price_limit_rankings.py` (Taiwan limit-up/down) |
| EOD rankings archive (optional) | `services/rankings_archive_service.py` → [azure-rankings-archive.md](azure-rankings-archive.md) |
| Market snapshot tiles | `market_index_service.py` (Taiwan from MIS; U.S. indices, crypto, gold from Yahoo; Stooq as a price-only U.S. fallback), `fx_service.py`, `commodity_service.py`, `market_snapshot_cache.py` |
| Taiwan daily datasets (法人, 資券) | `taiwan_market_data_refresh_service.py` writing Postgres |
| Market hours, holidays, snapshot buckets | `services/market_clock.py` |

## Request-driven vs cache-backed vs scheduled

| Data | Mode | Behavior |
| --- | --- | --- |
| Watchlist / batch quotes (`GET /api/quotes?ids=`) | **Cache-first** | Returns cached quotes immediately. A miss or stale entry asks the refresher to fetch that symbol (`refreshStatus: queued` / `refreshing`). Never blocks on upstream. |
| Single quote (`GET /api/quotes/{id}`) | Cache-first while the refresher runs; otherwise **request-driven** | Fresh cache → returned. When the refresher owns upstream (default), stale or missing entries are served or reported as such and a refresh is queued. Without a running refresher, the request calls the provider. |
| Alerts, portfolio values, key stats | `QuoteService.get_quote` | Same cache-first rule; may use a stale cached quote. |
| Rankings quote enrichment | `get_quotes(allow_stale=False)` | Requires a fresh cache hit or a provider fetch; never uses hard-stale data. |
| Background refresher | **Scheduled** | Refreshes the union of all users' watchlist and portfolio symbols, plus symbols requested by reads. |
| Price history / charts | **Request-driven, uncached** | Each chart request goes to the provider (Yahoo, or TWSE/TPEx monthly files for Taiwan daily bars). During a session the daily chart merges a "forming" candle from the live quote. |
| Rankings | **Snapshot-backed** | See [Rankings](#rankings). |
| Taiwan 法人 / 資券 history | **Scheduled + stored** | Daily refresh after `TAIWAN_MARKET_DATA_REFRESH_HOUR` (18:00 Asia/Taipei) into Postgres. Detail pages read the store; when it is empty or stale they fetch only the recent window live and persist it (#105). |
| `/api/stream` WebSocket | Push of cache-first quotes every 3 s | Implemented, but the frontend does not use it; it polls instead. |

## Quote cache and refresher

**Cache** (`quote_redis_cache.py`), key `quote:v2:{stock_id}`:

- *Soft TTL* `QUOTE_CACHE_TTL_SECONDS` = 60 s. Younger entries are "fresh".
- *Hard TTL* `QUOTE_CACHE_HARD_TTL_SECONDS` = 1 h. Older-than-soft entries are still served as `stale` (stale-while-revalidate) until they expire.
- Without Redis, or after a Redis error, the same API falls back to a per-process memory cache, retrying Redis with exponential backoff (5 s → 60 s, with jitter).
- Per-symbol refresh locks (30 s) stop concurrent requests from fetching the same symbol twice.

**Refresher** (`quote_refresher_service.py`), on by default:

- **Cadence:** every 45 s while any market is open or within an hour of opening; every 15 min otherwise. Symbols already refreshed within that window are skipped.
- **Throttling:** at most 3 concurrent fetches, 0.35 s minimum spacing per provider, up to 2 s jitter, and a 40 s deadline per cycle. Timed-out symbols are prioritized next cycle.
- **Circuit breaker per market:** after 5 consecutive provider failures, that market pauses for 120 s. Provider `Retry-After` hints extend the backoff.
- **Multiple replicas:** a Redis leader lock (`SET NX`) lets one replica run each cycle. If Redis is configured but unreachable, cycles are skipped (fail closed) instead of every replica hitting providers.
- **`QUOTE_REFRESHER_OWNS_UPSTREAM=true`:** while the sampler thread is alive, UI read paths never call providers; they serve cache and enqueue refreshes.

**Provider-level protection:**

- TWSE MIS has its own circuit (3 network failures → 45 s pause).
- After MIS has no trustworthy price **and** Yahoo also fails, Yahoo is skipped for that Taiwan stock for 60 s.
- `TWProvider` keeps a 15-minute last-good quote as a final fallback.

## Freshness contract

Every quote envelope carries the provider's own timestamp plus cache metadata (`packages/shared-contracts/quote.ts`):

```text
quote:     lastPrice, previousClose, change, changePercent, timestamp, isMarketOpen, open/high/low/volume,
           priceLimitStatus (TW)
freshness: cachedAt, ageSeconds, stale, source (redis|memory|provider|none),
           refreshStatus (idle|queued|refreshing|unavailable)
```

The UI turns this into a badge (`frontend/src/services/market-data/freshness.ts`): **live** (≤ 2 min old), **delayed** (≤ 15 min), **stale**, **closed** (market not open), **refreshing**, or **unavailable** (no quote and refresh failed).

Client polling (TanStack Query, paused in background tabs):

| View | Interval |
| --- | --- |
| Watchlist quotes | 45 s while a tracked market is open (matches the refresher); 3 min closed; 5 s while a symbol is warming |
| Stock detail quote | 5 s (served from cache) |
| Stock detail chart history | 60 s while that market is open |
| Market snapshot tiles | 60 s |
| Alerts | 30 s |
| Rankings page | 30 min while open, 2 h closed; 12 h for Taiwan net-buy / net-sell (end-of-day data) |

## Rankings

`GET /api/rankings` goes through `rankings_snapshot_service`:

1. **Intraday quote-based types** (volume, movers, valuation) use **30-minute buckets** (`current_rankings_snapshot_bucket`). If Postgres already has a snapshot for the current bucket, it is served. Otherwise the provider is called once, the result is stored for that bucket, and it is returned.
2. **Providers:** Yahoo pre-ranked lists (Taiwan rank pages; U.S. `yfinance.screen`) cached for `RANKINGS_EXTERNAL_CACHE_TTL_SECONDS` (10 min). They fall back to ranking a bounded quote universe from `QuoteService` when Yahoo fails.
3. **Taiwan net-buy / net-sell** use end-of-day institutional files, which apply from 18:00 Asia/Taipei.
4. **Taiwan limit-up / limit-down** are **live-only**: built from MIS rows (latest trade vs. official `u`/`w` limits), cached for the same 10-minute TTL, never snapshotted or archived.
5. **Background sampler** captures a bounded universe every `RANKINGS_REFRESH_SECONDS` (30 min). `POST /api/rankings/refresh` forces a refresh, with a 15 s cooldown per market / universe / type.
6. **Failure:** if the live fetch fails or comes back empty, the **latest stored snapshot** is served with a provider-failure freshness label. With no snapshot at all, the response is an explicit unavailable state.
7. **History** (`/api/rankings/history*`) reads the optional Azure SQL EOD archive only. It never falls back to current rankings, and returns 503 when the archive is unreachable.

U.S. net-buy/net-sell and U.S. price-limit rankings are reported as `unsupported` (no free source).

## Known limitations of free and public providers

- **TWSE MIS** returns 5-second snapshots. The top-level `z` is usually `-` between trades, so the latest trade must be read from the row's `trade` object; `pz` is undocumented and unused. Field semantics are unofficial and can change.
- **Yahoo** is about **20 minutes delayed for Taiwan**. With `range=5d`, `chartPreviousClose` is the close before the window, not yesterday's close. Requests can be rate-limited or blocked, and U.S. screens need cookie/crumb handling.
- **Stooq** quote rows carry no previous close, so they cannot give a daily change. Stooq is used only as a price-only fallback for U.S. indices (the UI shows a dash for the change), never for stock quotes.
- **TWSE / TPEx daily files** appear after the close; the institutional files apply after 18:00 Taipei.
- No provider has an SLA. HTML-scraped sources (Yahoo TW rank pages, TWSE ISIN list) break when markup changes.

## How stale or incomplete data is handled

Rules the code follows; keep them when changing providers:

1. **Never invent a price.** A reference or previous close is never used as the current price. If no source has a trustworthy current price, the API returns `quote: null` with `refreshStatus: "unavailable"` (#258).
2. **One snapshot per quote.** `lastPrice`, `previousClose`, `change`, and `changePercent` come from the same source and price pair (`coherent_price_change` in `yahoo_chart.py`). Fields from different snapshots are never mixed. A daily change is never computed against the day's open.
3. **Timestamps describe the data, not the fetch.** A quote or index is stamped with its trade or market time. Without one it is rejected rather than stamped "now".
4. **`isMarketOpen`** (Taiwan) is true only while the market is open now *and* the quote is from today's session.
5. **Serve last-known data with a flag** rather than nothing: stale cache entries (up to the hard TTL) and stored ranking snapshots are returned with `stale` / freshness labels and their original timestamps.
6. **Prefer unavailable over wrong.** Taiwan trades outside the regular session, stamped in the future, or outside the snapshot's own high/low are rejected.

## Current limitations

- Price history is not cached. Detail-page chart polling while a market is open repeats upstream history calls.
- The alert evaluator uses whatever quote the cache returns, which may be up to the 1-hour hard TTL old; events record that quote's timestamp.
- Without Redis and with more than one backend replica, each replica has its own memory cache and refresher.
- With Redis configured but down, refresher cycles stop (fail closed). Reads then serve memory or stale data until Redis returns.
- Market-snapshot tiles (indices, FX, commodities) use their own small cache helper rather than the quote freshness envelope.
