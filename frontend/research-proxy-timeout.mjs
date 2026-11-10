/**
 * Browser `/api/*` calls are rewritten through Next.js. With `proxyTimeout`
 * unset, Next uses 30 seconds and answers HTTP 500 ("Internal Server Error")
 * while the upstream request can still finish and log HTTP 200.
 * The research batch route defaults to 120 seconds. 150 seconds covers that
 * budget plus a small margin. It does not raise the route's own 600 second cap.
 */
export const RESEARCH_PROXY_TIMEOUT_MS = 150_000;
