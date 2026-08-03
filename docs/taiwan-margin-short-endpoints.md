# Taiwan margin / short official endpoints

Verified: **2026-08-03** (public unauthenticated GET JSON; no API key, cookie, or login).

The feature uses browser-like `User-Agent` / `Accept` headers only. There is no
referer requirement observed for these endpoints. Do not scrape HTML when these
JSON contracts remain available.

## TWSE

| Dataset | URL | Params | Notes |
|---|---|---|---|
| Margin + short-margin balances | `https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN` | `response=json`, `date=YYYYMMDD`, `selectType=ALL` | Success: `stat=OK` with `tables[]`. Table 0 is market summary; the per-symbol table has `代號` / duplicate `今日餘額` (融資 then 融券). Legacy flat `fields`+`data` fixtures are still accepted. Content-Type: `application/json`. |
| Borrowed-short balances | `https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U` | `response=json`, `date=YYYYMMDD` | Success: top-level `fields` + `data`. Includes 融券 and 借券賣出 sections with repeated `前日餘額` labels. Content-Type: `application/json`. |
| Close supplement (optional) | `https://www.twse.com.tw/rwd/zh/lending/TWT72U` | `response=json`, `date=YYYYMMDD`, `selectType=SLBNLB` | Used only for borrowed-short change amount. Failure is non-fatal. |

**No-data:** `stat` contains markers such as `很抱歉，沒有符合條件的資料` / `查無資料` (weekends/holidays or unpublished dates).

**Publication:** weekday official files; probes skip weekends. Pre-publish empty responses are cached briefly (2 minutes). Historical dates generally work for prior sessions once published; probing far-future or not-yet-published weekdays returns the official no-data envelope.

**Why these endpoints:** `MI_MARGN` is the TWSE public daily margin/short-margin balance file used by the site’s 融資融券查詢. `TWT93U` is the public borrowed-short (借券賣出) daily file required by the UI. `TWT72U` is only an optional close-price supplement for amount calculations.

## TPEx

| Dataset | URL | Params | Notes |
|---|---|---|---|
| Margin balances | `https://www.tpex.org.tw/www/zh-tw/margin/balance` | `response=json`, `date=YYYY/MM/DD` | Success: `tables[0].fields` / `tables[0].data` with `代號`, `資餘額`, `券餘額`. Content-Type: `application/json`. |
| Borrowed-short balances | `https://www.tpex.org.tw/www/zh-tw/margin/sbl` | `response=json`, `date=YYYY/MM/DD` | Success: `tables[0]` with duplicate short labels (`前日餘額` / `當日餘額`) for 融券 then 借券賣出. |

**No-data:** empty `tables[0].data` and/or no-data `stat` markers.

**Why these endpoints:** TPEx publishes the same daily margin and SBL balances the UI needs via these JSON routes; no HTML scrape is required.

## Rate limits / blocking

No dedicated documented RPM quota was observed. HTTP **429** is classified as `rate_limited` (API **503**). Access-control **401/403** is classified as `auth_denied` (API **502**, FE wording makes clear it is not a site sign-in problem). Unexpected HTML / non-JSON bodies classify as `unexpected_content` and surface as schema/interpretation failures.

## Failure classification (application)

| Upstream signal | Internal status | Typical API |
|---|---|---|
| Official no-data across the window | soft empty → `emptyReason=not_published` | `200` + empty history |
| Symbol absent from valid tables | soft empty → `emptyReason=no_data_for_stock` | `200` + empty history |
| HTTP 401/403 | `auth_denied` | `502` detail: denied access |
| HTTP 429 | `rate_limited` | `503` detail: rate-limited |
| Unexpected table / unparseable matched row / unexpected content-type | `schema_error` / `parse_error` / `unexpected_content` | `502` detail: could not be interpreted |
| Other HTTP / network failure | `http_error` | `502` detail: temporarily unavailable |

Empty history is returned as a normal `200` only when the scan produced **no hard failures**. Soft-empty days (holiday / unpublished / symbol absent) count as successful probes; if any hard failure also occurred and no records were produced, the API raises instead of returning `history: []`.

The scheduled refresh path uses the same payload assessment: schema/auth/rate-limit/network failures are recorded as day errors and do not silently upsert zero rows from unparseable envelopes.
