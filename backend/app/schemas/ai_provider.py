from datetime import datetime

from pydantic import BaseModel, Field


class AIProviderUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=4096)
    model: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class AIProviderTestRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=4096)
    model: str | None = Field(default=None, min_length=1, max_length=128)


class AIProviderRead(BaseModel):
    provider: str
    display_name: str
    base_url: str
    models: list[str]
    model: str
    enabled: bool
    has_api_key: bool
    masked_key: str | None
    last_test_ok: bool | None
    last_test_message: str | None
    last_tested_at: datetime | None


class AIProviderTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int
