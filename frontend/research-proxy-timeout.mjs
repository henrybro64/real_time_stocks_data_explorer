/**
 * Browser `/api/*` calls are rewritten through Next.js. With `proxyTimeout`
 * unset, Next uses 30 seconds and answers HTTP 500 ("Internal Server Error")
 * while the upstream request can still finish and log HTTP 200.
 *
 * Next.js (installed router-server / proxy-request) exposes only one
 * `experimental.proxyTimeout` for every rewrite proxy. There is no
 * path-scoped timeout. Setting this value therefore applies to all rewritten
 * `/api/*` traffic, including Portfolio, Rankings, and stock detail. Capture
 * routes stay blocked in `beforeFiles` and never reach this proxy.
 *
 * 150 seconds covers the default research batch budget of 120 seconds plus a
 * small margin. It does not raise the route's own 600 second cap, Traefik,
 * Cloudflare, or FastAPI settings. Shorter requests still return as soon as
 * the upstream answers.
 *
 * #328 evidence: a scan batch logged HTTP 200 after ~49.7s while the UI showed
 * Request failed: 500. No browser HAR / Next proxy log was captured for the
 * original incident, so the timeout gap remains the established mechanism,
 * not a packet-proven reproduction.
 */
export const RESEARCH_PROXY_TIMEOUT_MS = 150_000;
