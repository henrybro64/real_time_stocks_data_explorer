from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.history import router as history_router
from app.api.routes.markets import router as markets_router
from app.api.routes.ai import router as ai_router
from app.api.routes.quotes import router as quotes_router
from app.api.routes.search import router as search_router
from app.api.routes.stream import router as stream_router
from app.api.routes.watchlist import router as watchlist_router
from app.api.routes.portfolio import router as portfolio_router
from app.core.config import settings
from app.db.health import validate_database_connection
from app.db.init_db import seed_default_user


app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(markets_router, prefix=settings.api_prefix)
app.include_router(search_router, prefix=settings.api_prefix)
app.include_router(quotes_router, prefix=settings.api_prefix)
app.include_router(history_router, prefix=settings.api_prefix)
app.include_router(ai_router, prefix=settings.api_prefix)
app.include_router(watchlist_router, prefix=settings.api_prefix)
app.include_router(portfolio_router, prefix=settings.api_prefix)
app.include_router(stream_router, prefix=settings.api_prefix)


@app.on_event("startup")
def validate_startup_dependencies() -> None:
    validate_database_connection()
    seed_default_user()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
