# Stock Ticker MVP

Starter monorepo for a stock ticker MVP with:

- `Next.js + TypeScript` frontend (Node **20+**; see `frontend/package.json` / `frontend/.nvmrc`)
- `FastAPI` backend
- mixed-language display rules
  - U.S. stocks in English
  - Taiwan stocks in Traditional Chinese

## Structure

```text
backend/   FastAPI backend
frontend/  React frontend
packages/
  shared-contracts/  shared TypeScript contracts
docs/
  mvp.md
```

## Run

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

### API URL shape (same-origin)

**Preferred:** serve the UI at `/` and the API at `/api` on **one origin** (no cross-origin
fetch). The frontend calls relative URLs like `/api/...`; in dev, Next.js rewrites proxy to
FastAPI (`BACKEND_PROXY_URL`, default `http://127.0.0.1:8000`). Copy `frontend/.env.example`
for overrides.

To call the API **directly** (no proxy), set `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`
and add your UI origin to backend `CORS_ORIGINS` (see `backend/.env.example`).

### Backend environment

- `CORS_ORIGINS` — comma-separated browser origins when UI and API are on different hosts.
- `API_PREFIX` — route prefix (default `/api`; must match `NEXT_PUBLIC_API_PREFIX` on the frontend).

### Key statistics / fundamentals (optional)

Price, volume, and ranges come from Yahoo chart (US) or local history (TW). **Market cap, beta, P/E, EPS, dividend dates,** etc. are **not** provided by those endpoints.

To fill those fields in **Key statistics**, set a [Finnhub](https://finnhub.io/) API key on the backend:

```bash
export FINNHUB_API_KEY="your_token_here"
```

Then restart the API. The backend merges Finnhub `profile2`, `metric`, `calendar/earnings`, and `dividend` data when the key is present.

### Where calculations run

- **Backend:** Price/volume series math for charts (min/max, normalized SVG points, axis tick indices, sparkline trend), key-statistics range marker percentages (0–100 within day / 52-week bands), and all quote/history/key-stats aggregation.
- **Frontend:** Renders API payloads, handles pointer hit-testing for the chart (which candle index is under the cursor), locale/date **display** formatting (`Intl`, currency labels), and i18n strings—not business formulas.
