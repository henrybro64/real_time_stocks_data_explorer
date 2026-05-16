from pydantic import BaseModel


class ApiKeyStatusResponse(BaseModel):
    provider: str
    configured: bool
    keyLast4: str | None = None


class ApiKeyUpsertRequest(BaseModel):
    apiKey: str


class AiProviderPreferenceResponse(BaseModel):
    preferredProvider: str | None = None


class AiProviderPreferenceRequest(BaseModel):
    preferredProvider: str


class AiInsightResponse(BaseModel):
    provider: str
    stockId: str
    content: str
