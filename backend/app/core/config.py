from pydantic import BaseModel


class AppConfig(BaseModel):
    app_name: str = "Stock Ticker API"
    api_prefix: str = "/api"
    allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


settings = AppConfig()
