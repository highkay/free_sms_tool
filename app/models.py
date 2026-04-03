from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row

APP_STATE_STATUSES = (
    "available",
    "claimed",
    "used",
    "success",
    "blocked",
    "blacklisted",
    "ignore",
)

CLAIM_ACTIVE_STATUSES = ("watching",)
CLAIM_FINAL_STATUSES = ("completed", "released", "expired")


@dataclass(slots=True)
class DashboardStats:
    provider_count: int
    enabled_provider_count: int
    number_count: int
    active_number_count: int
    stale_number_count: int
    recent_message_count: int
    app_count: int
    active_claim_count: int


@dataclass(slots=True)
class ProviderRow:
    id: str
    name: str
    homepage_url: str
    transport_mode: str
    discovery_mode: str
    detail_mode: str
    auth_mode: str
    enabled: bool
    priority: int
    notes: str

    @classmethod
    def from_row(cls, row: Row) -> "ProviderRow":
        return cls(
            id=row["id"],
            name=row["name"],
            homepage_url=row["homepage_url"],
            transport_mode=row["transport_mode"],
            discovery_mode=row["discovery_mode"],
            detail_mode=row["detail_mode"],
            auth_mode=row["auth_mode"],
            enabled=bool(row["enabled"]),
            priority=row["priority"],
            notes=row["notes"] or "",
        )


@dataclass(slots=True)
class ProviderConfigRow:
    provider_id: str
    provider_name: str
    homepage_url: str
    transport_mode: str
    discovery_mode: str
    detail_mode: str
    auth_mode: str
    enabled: bool
    priority: int
    notes: str
    user_agent: str
    headers_json: str
    cookies_json: str
    tokens_json: str
    last_verified_at: str | None
    last_verify_status: str
    last_verify_error: str

    @classmethod
    def from_row(cls, row: Row) -> "ProviderConfigRow":
        return cls(
            provider_id=row["provider_id"],
            provider_name=row["provider_name"],
            homepage_url=row["homepage_url"],
            transport_mode=row["transport_mode"],
            discovery_mode=row["discovery_mode"],
            detail_mode=row["detail_mode"],
            auth_mode=row["auth_mode"],
            enabled=bool(row["enabled"]),
            priority=row["priority"],
            notes=row["notes"] or "",
            user_agent=row["user_agent"] or "",
            headers_json=row["headers_json"] or "{}",
            cookies_json=row["cookies_json"] or "{}",
            tokens_json=row["tokens_json"] or "{}",
            last_verified_at=row["last_verified_at"],
            last_verify_status=row["last_verify_status"] or "",
            last_verify_error=row["last_verify_error"] or "",
        )


@dataclass(slots=True)
class NumberRow:
    id: int
    e164: str
    country_name: str | None
    status: str
    activity_score: float
    last_message_age_min: int | None
    freshness_bucket: str | None
    providers: str
    blacklist_reason: str | None

    @classmethod
    def from_row(cls, row: Row) -> "NumberRow":
        return cls(
            id=row["id"],
            e164=row["e164"],
            country_name=row["country_name"],
            status=row["status"],
            activity_score=float(row["activity_score"] or 0),
            last_message_age_min=row["last_message_age_min"],
            freshness_bucket=row["freshness_bucket"],
            providers=row["providers"] or "",
            blacklist_reason=row["blacklist_reason"],
        )


@dataclass(slots=True)
class NumberSelectionFilters:
    country_name: str | None = None
    provider_id: str | None = None
    app_slug: str | None = None
    include_cooling: bool = False


@dataclass(slots=True)
class SelectedNumber:
    id: int
    e164: str
    country_name: str | None
    provider_id: str
    provider_name: str
    last_message_age_min: int | None
    freshness_bucket: str | None
    activity_score: float

    @classmethod
    def from_row(cls, row: Row) -> "SelectedNumber":
        return cls(
            id=row["id"],
            e164=row["e164"],
            country_name=row["country_name"],
            provider_id=row["provider_id"],
            provider_name=row["provider_name"],
            last_message_age_min=row["effective_age"],
            freshness_bucket=row["freshness_bucket"],
            activity_score=float(row["activity_score"] or 0),
        )


@dataclass(slots=True)
class MessageRow:
    id: int
    sender: str | None
    body: str
    received_at: str | None
    otp_code: str | None
    provider_name: str

    @classmethod
    def from_row(cls, row: Row) -> "MessageRow":
        return cls(
            id=row["id"],
            sender=row["sender"],
            body=row["body"],
            received_at=row["received_at"],
            otp_code=row["otp_code"],
            provider_name=row["provider_name"],
        )


@dataclass(slots=True)
class NumberDetail:
    id: int
    e164: str
    country_name: str | None
    status: str
    freshness_bucket: str | None
    last_message_age_min: int | None
    activity_score: float
    blacklist_reason: str | None
    last_selected_at: str | None

    @classmethod
    def from_row(cls, row: Row) -> "NumberDetail":
        return cls(
            id=row["id"],
            e164=row["e164"],
            country_name=row["country_name"],
            status=row["status"],
            freshness_bucket=row["freshness_bucket"],
            last_message_age_min=row["last_message_age_min"],
            activity_score=float(row["activity_score"] or 0),
            blacklist_reason=row["blacklist_reason"],
            last_selected_at=row["last_selected_at"],
        )


@dataclass(slots=True)
class AppRow:
    id: int
    slug: str
    name: str
    notes: str

    @classmethod
    def from_row(cls, row: Row) -> "AppRow":
        return cls(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            notes=row["notes"] or "",
        )


@dataclass(slots=True)
class AppSummaryRow:
    id: int
    slug: str
    name: str
    notes: str
    number_count: int
    used_count: int
    blocked_count: int
    active_claim_count: int

    @classmethod
    def from_row(cls, row: Row) -> "AppSummaryRow":
        return cls(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            notes=row["notes"] or "",
            number_count=row["number_count"] or 0,
            used_count=row["used_count"] or 0,
            blocked_count=row["blocked_count"] or 0,
            active_claim_count=row["active_claim_count"] or 0,
        )


@dataclass(slots=True)
class AppNumberUsageRow:
    number_id: int
    e164: str
    country_name: str | None
    number_status: str
    freshness_bucket: str | None
    last_message_age_min: int | None
    app_status: str
    use_count: int
    last_result: str | None
    last_claimed_at: str | None
    last_used_at: str | None
    notes: str

    @classmethod
    def from_row(cls, row: Row) -> "AppNumberUsageRow":
        return cls(
            number_id=row["number_id"],
            e164=row["e164"],
            country_name=row["country_name"],
            number_status=row["number_status"],
            freshness_bucket=row["freshness_bucket"],
            last_message_age_min=row["last_message_age_min"],
            app_status=row["app_status"],
            use_count=row["use_count"],
            last_result=row["last_result"],
            last_claimed_at=row["last_claimed_at"],
            last_used_at=row["last_used_at"],
            notes=row["notes"] or "",
        )


@dataclass(slots=True)
class NumberSourceRow:
    id: int
    provider_id: str
    provider_name: str
    source_status: str
    restricted: bool
    restricted_reason: str | None
    provider_country_label: str | None
    detail_url: str
    last_real_message_at: str | None
    last_real_message_age_min: int | None
    last_checked_at: str | None

    @classmethod
    def from_row(cls, row: Row) -> "NumberSourceRow":
        return cls(
            id=row["id"],
            provider_id=row["provider_id"],
            provider_name=row["provider_name"],
            source_status=row["source_status"],
            restricted=bool(row["restricted"]),
            restricted_reason=row["restricted_reason"],
            provider_country_label=row["provider_country_label"],
            detail_url=row["detail_url"],
            last_real_message_at=row["last_real_message_at"],
            last_real_message_age_min=row["last_real_message_age_min"],
            last_checked_at=row["last_checked_at"],
        )


@dataclass(slots=True)
class AppNumberStateRow:
    app_slug: str
    app_name: str
    status: str
    use_count: int
    last_result: str | None
    last_claimed_at: str | None
    last_used_at: str | None
    notes: str

    @classmethod
    def from_row(cls, row: Row) -> "AppNumberStateRow":
        return cls(
            app_slug=row["app_slug"],
            app_name=row["app_name"],
            status=row["status"],
            use_count=row["use_count"],
            last_result=row["last_result"],
            last_claimed_at=row["last_claimed_at"],
            last_used_at=row["last_used_at"],
            notes=row["notes"] or "",
        )


@dataclass(slots=True)
class ClaimRow:
    id: int
    claim_token: str
    status: str
    purpose: str
    created_at: str
    expires_at: str
    released_at: str | None
    requested_country: str | None
    requested_provider: str | None
    number_id: int
    e164: str
    country_name: str | None
    app_slug: str | None
    app_name: str | None

    @classmethod
    def from_row(cls, row: Row) -> "ClaimRow":
        return cls(
            id=row["id"],
            claim_token=row["claim_token"],
            status=row["status"],
            purpose=row["purpose"] or "",
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            released_at=row["released_at"],
            requested_country=row["requested_country"],
            requested_provider=row["requested_provider"],
            number_id=row["number_id"],
            e164=row["e164"],
            country_name=row["country_name"],
            app_slug=row["app_slug"],
            app_name=row["app_name"],
        )


@dataclass(slots=True)
class ApiKeyRow:
    id: int
    name: str
    token_prefix: str
    role: str
    is_active: bool
    created_at: str
    last_used_at: str | None
    notes: str

    @classmethod
    def from_row(cls, row: Row) -> "ApiKeyRow":
        return cls(
            id=row["id"],
            name=row["name"],
            token_prefix=row["token_prefix"],
            role=row["role"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            notes=row["notes"] or "",
        )


@dataclass(slots=True)
class JobRow:
    id: int
    job_type: str
    provider_id: str | None
    status: str
    payload_json: str
    result_json: str
    error_text: str
    worker_id: str | None
    scheduled_at: str
    started_at: str | None
    finished_at: str | None

    @classmethod
    def from_row(cls, row: Row) -> "JobRow":
        return cls(
            id=row["id"],
            job_type=row["job_type"],
            provider_id=row["provider_id"],
            status=row["status"],
            payload_json=row["payload_json"] or "{}",
            result_json=row["result_json"] or "{}",
            error_text=row["error_text"] or "",
            worker_id=row["worker_id"],
            scheduled_at=row["scheduled_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
