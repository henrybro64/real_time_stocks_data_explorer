from pydantic import BaseModel


class AuthUserPublic(BaseModel):
    id: str
    username: str


class AuthStatusResponse(BaseModel):
    authEnabled: bool
    loggedIn: bool
    providers: dict[str, bool]
    user: AuthUserPublic | None = None
