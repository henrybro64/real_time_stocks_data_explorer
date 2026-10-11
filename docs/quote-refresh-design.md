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

- **Cadence:** fixed period measured **start to start** — 45 s while any market is open or within an hour of opening; 15 min otherwise. Wait time is `max(0, period − cycle_duration)` so a slow cycle does not push the next start past the soft TTL (#271). Symbols already refreshed within that window are skipped.
- **Batching:** each cycle prefers provider batches (Yahoo v7 chunks for US, MIS multi-symbol for TW) and falls back to per-symbol chart/Yahoo only for misses. A `429` keeps quotes already parsed from earlier chunks, records **one** market failure, and does not per-symbol-fetch the rest of that market. A Yahoo v7 `401`/`403` stops further v7 chunks and leaves those symbols for the chart fallback, because v7 is often unauthorized while chart still works (#268, #271).
- **Throttling:** at most 3 concurrent per-symbol fallbacks. Each upstream attempt in a per-symbol fallback waits for one provider slot (chart, suffix, then v7), with no extra wait in front of that fetch. Each batch chunk waits for one slot, then checks the deadline again so a slot sleep cannot start a request after the cycle budget. Locks taken for a batch are renewed until those symbols are released. Up to 2 s jitter runs **once per cycle**. The 40 s deadline covers the batch and the fallback; further chunks stop and those symbols are prioritized next cycle.
- **Tracked-set tiers:** currently viewed (immediate) and alerted symbols stay ahead of previously timed-out symbols and the watchlist/portfolio tail. Each of those groups is fetched per market, so a large U.S. block cannot run ahead of a hot Taiwan symbol. Only the cold tail rotates.
- **Immediate refreshes:** separate 1-worker pool with a bounded queue (`QUOTE_REFRESHER_IMMEDIATE_QUEUE_MAX`, default 64). Overflow is dropped so a burst of cache-miss reads cannot starve the scheduled cycle.
- **Capacity (stubbed providers):** a cycle over ~500 tracked symbols completes within the open-market poll interval when batching is used; with live upstreams, capacity is roughly `batch_size / spacing` requests per market inside the 40 s deadline.
- **Circuit breaker per market:** after 5 consecutive **transport** failures, that market pauses for 120 s. Empty / unusable symbol results back off that symbol only (#268). Provider `Retry-After` hints extend the backoff.
- **Multiple replicas:** a Redis leader lock (`SET NX`, key `quote:refresher:v1:leader`) is held for the poll interval, so a second worker does not start another cycle until that window expires. The holder renews its own lease each cycle with an owner-checked script, so its next cycle is never blocked by the key it set last time. The lock is deleted early when the cycle raises, and on shutdown (`stop_background_sampler`) when this process still owns it, so a restarted worker does not wait out the old TTL. If Redis is configured but unreachable, cycles are skipped (fail closed) instead of every replica hitting providers.
- **Skipped cycles (#291):** a cycle skipped for leadership (Redis unreachable, or the key held by another token) is retried after `QUOTE_REFRESHER_LEADER_RETRY_SECONDS` (default 5 s, plus up to 20 % jitter, capped at the poll interval) instead of a full poll. It is reported to `/health` as incomplete, not as a success. Immediate-refresh ids in `quote:immediate:v1` are drained by any live worker, leader or not: `SPOP` is atomic and each symbol has its own refresh lock.
- **`QUOTE_REFRESHER_OWNS_UPSTREAM=true`:** while the sampler thread is alive, UI read paths never call providers; they serve cache and enqueue refreshes.

**Provider-level protection:**

- TWSE MIS has its own circuit (3 network failures → 45 s pause).
- After MIS has no trustworthy price **and** Yahoo also fails, Yahoo is skipped for that Taiwan stock for 60 s.
- After MIS and Yahoo both miss, `TWProvider.get_quote` returns no quote. The shared quote cache (soft/hard TTL) serves last-known data as stale. The refresher backs off that symbol only; empty results do not open the market circuit (#263, #268).
- A Yahoo `429` / `401` / `403` is not retried inside the HTTP client for quote calls. A `429` aborts the remaining symbol-suffix and chart→v7 chain, and a batched cycle does not fall back to per-symbol fetches for the symbols still waiting. A v7 `401`/`403` skips further v7 calls for two minutes and still allows the chart fallback. `Retry-After` is applied as refresher backoff (#268).

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
4. **`isMarketOpen`** (Taiwan and U.S.) is true only while the market is open **now** *and* the quote is from today's session. The flag is derived at read time from the quote cache so a closing print cached during the session cannot stay “open” after the close (#262).
5. **Serve last-known data with a flag** rather than nothing: stale cache entries (up to the hard TTL) and stored ranking snapshots are returned with `stale` / freshness labels and their original timestamps. A Taiwan provider last-good fallback must not be re-cached as a fresh provider success (#263).
6. **Prefer unavailable over wrong.** Taiwan trades outside the regular session, stamped in the future, or outside the snapshot's own high/low are rejected.

## Price history storage (#269)

Daily OHLCV bars live in Postgres (`daily_price_bars`, keyed by market/symbol/date). A chart, key-stat, analysis, or ownership request reads that store. It backfills a closed month only when that month has no stored bars and no fetch record, spacing each Taiwan month by the upstream host interval. The current month stays eligible until its latest bar reaches the last completed session, then stays put while that session is still the latest close. Today's forming candle comes from the quote cache and is left out of the official store. While the market is closed, the reference-refresh job updates the current month for watchlist, portfolio, and alert symbols when the stored series lags the last completed session. A failed month waits 15 minutes and leaves stored bars in place. Intraday ranges (`1D`, and short-lived `1W` responses) use a 30–60 s Redis/memory cache. When Redis holds the single-flight lock, other callers wait for that cache entry. The dashboard watchlist uses one batch sparkline request (`GET /api/stocks/sparklines`). That request returns bars already stored and backfills cold symbols off the request, and one symbol's failure leaves the rest of the batch intact. A missing older month leaves the chart on the bars already stored. Taiwan and U.S. catalogs and the Taiwan holiday calendar are persisted in `reference_snapshots` and refreshed by the same background job. Request paths read the stored catalog and calendar.

## Current limitations

- The alert evaluator uses whatever quote the cache returns, which may be up to the 1-hour hard TTL old; events record that quote's timestamp.
- Without Redis and with more than one backend replica, each replica has its own memory cache and refresher.
- With Redis configured but down, refresher cycles stop (fail closed). Reads then serve memory or stale data until Redis returns. The refresher retries leadership every few seconds, so the first cycle runs within one short retry after Redis is back (#291).
- In k8s, Redis keeps RDB snapshots on a volume, so a Redis pod restart keeps the quote cache (#293). Locally without the compose file's volume, a Redis restart empties it until the next cycle.
- Market-snapshot tiles (indices, FX, commodities) use their own small cache helper rather than the quote freshness envelope.
