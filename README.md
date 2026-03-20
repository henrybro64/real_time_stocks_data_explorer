# Stock Ticker MVP

Starter monorepo for a stock ticker MVP with:

- `React + Vite + TypeScript` frontend
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

The frontend expects the API at `http://localhost:8000` by default.
