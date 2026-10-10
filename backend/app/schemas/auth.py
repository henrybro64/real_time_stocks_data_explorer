from pydantic import BaseModel, Field, field_validator


class AuthUserPublic(BaseModel):
    id: str
    username: str


class AuthStatusResponse(BaseModel):
    authEnabled: bool
    loggedIn: bool
    providers: dict[str, bool]
    user: AuthUserPublic | None = None


class LocalLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LocalRegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def username_is_trimmed_and_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Username is required")
        if len(cleaned) > 64:
            raise ValueError("Username must be at most 64 characters")
        return cleaned

    @field_validator("password")
    @classmethod
    def password_is_strong_enough(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Password is required")
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters")
        return value
