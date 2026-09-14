# Product scope

This application is a **self-hosted research and record-keeping dashboard for a retail investor（散戶）**. It helps you follow U.S. and Taiwan markets, watch symbols, inspect a stock, keep a personal portfolio, and review informational AI explanations. You still make any buy/sell decision and place the order somewhere else.

It is **not** a brokerage, order-entry system, automated trading engine, tax/accounting package, institutional trading desk, high-frequency platform, or a source of personalized financial advice.

Setup, env vars, APIs, and Kubernetes live in the focused docs linked below. This page describes **what the product does on the default branch today**.

## Product summary

- **Markets:** U.S. and Taiwan (TWSE / TPEx) stocks, plus a small dashboard snapshot of selected indices, USD/TWD, and WTI.
- **UI:** English and Traditional Chinese are user-selectable. Stock display names follow that choice when bilingual metadata exists. Localization is incomplete: many API error messages stay in English, and portfolio AI does not follow the selected UI language.
- **Hosting:** run locally or on your own k3d/k3s cluster. There is no hosted brokerage or cloud requirement.
- **Freshness:** quotes and rankings can be live, delayed, stale, closed, cached, or unavailable. Do not treat the app as an exchange-grade real-time terminal.

## Who it is for

A retail investor who wants one place to:

- follow U.S. and Taiwan market information;
- maintain a watchlist;
- inspect quotes, history, chart averages, fundamentals/news when available, and Taiwan-specific datasets;
- track positions, other assets, portfolio history, and realized P/L;
- set simple price-above / price-below alerts;
- view market rankings;
- request informational stock AI explanations in the selected UI language, and portfolio AI analysis when configured.

The user remains responsible for the final investment decision.

## Current user workflows

Verified on the default branch (details: [local-development.md](local-development.md)):

- **Dashboard** — market snapshot, stock search, and watchlist quotes with freshness badges.
- **Stock detail** — quote, selectable chart ranges, backend SMA overlays, key statistics (optional Finnhub enrichment), news when available, Taiwan insights / 法人 / 資券 for TW names, U.S. peer snapshot for US names, and stock AI analysis.
- **Rankings** — U.S. DJIA / NASDAQ-100 and Taiwan TWSE / TPEx universes; types include volume, movers, valuation, and Taiwan net-buy/net-sell. U.S. net-buy/net-sell is unsupported on free sources.
- **Portfolio** — share positions with unrealized P/L, non-stock assets, USD/TWD views, sampled value history, and optional portfolio AI analysis.
- **Realized P/L** — manual closed-trade records in **TWD or USD**, including cumulative dividends and period summaries. No automatic FX conversion or tax-lot accounting.
- **Alerts** — `price_above` / `price_below` rules and an in-app event history. No email or push delivery.
- **AI settings** — per-user OpenAI or Perplexity preference and API key when the user supplies one.

**AI language (current, not a long-term promise):**

- **Stock AI** follows the selected UI language (`en` or `zh-TW`) via `GET /api/stocks/{id}/analysis?language=…`.
- **Portfolio AI** does **not** follow the UI language. It uses Traditional Chinese only when every holding looks Taiwan-like; otherwise it uses English.

## Users and ownership

See [multi-user-design.md](multi-user-design.md). Short version:

| Mode | Meaning |
| --- | --- |
| `AUTH_ENABLED=false` | Compatibility/single-user: seeded `default-user`. |
| `AUTH_ENABLED=true` | Local username/password and a session cookie. |

Watchlists, portfolio data, realized trades, alerts/events, and AI keys/preferences are private to the effective `user_id` in both modes. Authentication is local-session based unless a later change on the default branch proves otherwise.

## Market data and AI sources

Adapters fetch U.S. and Taiwan quotes, history, and search. **Yahoo Finance** is the primary U.S. path; **TWSE / TPEx** (and related official pages) feed Taiwan quotes, rankings, 法人, and 資券. **Google News RSS** supplies headline metadata. Stock-AI technicals prefer the **same candles the chart plots**; **yfinance** is the fallback and still supplies company info and fundamentals. **Finnhub** may enrich key statistics when `FINNHUB_API_KEY` is set.

Free or unofficial sources can be delayed, rate-limited, incomplete, or down. Taiwan 法人 and 資券 are separate persisted datasets, not live ticks.

- **Postgres** stores users, watchlists, portfolios, realized trades, alerts, rankings snapshots, and Taiwan daily history.
- **Redis** is optional for quote caching. Quote reads still degrade to in-memory last-known values when Redis is missing or failing.
- **Stock AI narratives** persist only in Redis. Without `REDIS_URL`, a generated narrative is not kept in an in-memory narrative cache. Backend-computed metrics on the next read can be newer than that narrative.
- AI is given backend-computed facts plus optional news/fundamental context. It is **not** a market-data source and must not invent missing numbers.

## Refresh and delivery

Intervals below are **defaults** and are configurable.

| Mechanism | Product meaning |
| --- | --- |
| Request / on-demand | An API call may return a fresh cache hit, fetch upstream, or serve last-known stale data. |
| Background quote refresher | Market-aware polling (default ~45s open / ~15min closed) warms watchlist and portfolio quotes, with throttling and a circuit breaker. |
| WebSocket `/api/stream` | Pushes **cache-first** quote snapshots every **3 seconds**. Not an exchange tick stream. |
| Scheduled jobs | Portfolio history (~5 min), alert evaluation (~60s), rankings snapshots (~30 min), Taiwan market-data refresh (daily after a configured hour), optional rankings archive. |
| Manual refresh | Some screens explicitly request updated data or regenerate AI: rankings refresh, dashboard quote retry, stock AI `refresh=true`, and on-demand portfolio AI. |
| Cached AI narrative | With Redis, a normal stock-AI read may reuse a saved narrative; `refresh=true` regenerates it. Without Redis, the narrative is not retained. |

## Scope boundary

**Implemented today:** the workflows above, bilingual UI selection, freshness/stale/unavailable signaling on the relevant contracts, and self-hosted deploy.

**Conditional / optional:** OpenAI or Perplexity keys, Finnhub, Redis, Azure SQL rankings archive, observability stack, GHCR-published images.

**Intentionally out of scope:**

- institutional desks or high-frequency trading;
- buy/sell order placement, brokerage connection, or broker-account sync;
- automated trading;
- guaranteed exchange-grade real-time data;
- email/push alerts (V1 is in-app events only);
- automatic FX conversion or tax-lot / tax reporting;
- native mobile apps (this is a responsive web UI);
- AI as advice, a quote source, or a guarantee.

Open GitHub issues describe **planned** work, not current behavior. Temporary bugs are not product scope.

## Terminology

| Term | Meaning |
| --- | --- |
| Canonical stock ID | `US:AAPL`, `TW:2330` |
| TWSE / TPEx | Taiwan listing boards |
| live / delayed / stale / closed | Quote freshness labels when the contract supplies them |
| Snapshot | A stored ranking or portfolio-value sample, not a live tick |
| Unrealized vs realized | Open-position mark-to-market vs a manually recorded closed trade |
| AI narrative vs computed metrics | Model prose vs backend numbers; metrics can be newer than a cached narrative |
