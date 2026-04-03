from __future__ import annotations

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = ""


class ProviderConfigUpdateRequest(BaseModel):
    enabled: bool
    priority: int = 100
    notes: str = ""
    user_agent: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    tokens: dict[str, str] = Field(default_factory=dict)


class AppUpsertRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    notes: str = ""


class NumberPickRequest(BaseModel):
    country_name: str | None = None
    provider_id: str | None = None
    app_slug: str | None = None
    include_cooling: bool = False


class BlacklistRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class AppStateRequest(BaseModel):
    app_slug: str = Field(min_length=1, max_length=100)
    app_name: str = ""
    status: str = "available"
    notes: str = ""


class ClaimCreateRequest(BaseModel):
    app_slug: str | None = None
    app_name: str = ""
    country_name: str | None = None
    provider_id: str | None = None
    purpose: str = ""
    include_cooling: bool = False
    ttl_minutes: int | None = Field(default=None, ge=1, le=120)


class ClaimTransitionRequest(BaseModel):
    result: str = ""
    app_state: str | None = None
