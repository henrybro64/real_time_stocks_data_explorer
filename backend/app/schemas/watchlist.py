from pydantic import BaseModel


class WatchlistResponse(BaseModel):
    ids: list[str]


class WatchlistItemCreate(BaseModel):
    id: str

