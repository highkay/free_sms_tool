from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.db.core import Database
from app.models import (
    APP_STATE_STATUSES,
    ApiKeyRow,
    AppNumberStateRow,
    AppNumberUsageRow,
    AppRow,
    AppSummaryRow,
    ClaimRow,
    DashboardStats,
    MessageRow,
    NumberDetail,
    NumberRow,
    NumberSelectionFilters,
    NumberSourceRow,
    ProviderConfigRow,
    ProviderRow,
    SelectedNumber,
)
from app.security import generate_api_key, generate_claim_token, hash_token, token_prefix
from app.services.selection import number_status_from_age

CLAIM_BLOCKING_SQL = "('watching')"
APP_BLOCKING_SQL = "('claimed', 'used', 'success', 'blocked', 'blacklisted', 'ignore')"


def _default_max_age(settings: Settings, include_cooling: bool) -> int:
    return settings.freshness_cooling_max if include_cooling else settings.freshness_warm_max


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-")


def _normalize_json_text(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "{}"
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("JSON config must be an object")
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


class Repository:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _ensure_app_conn(self, conn, slug: str, name: str, notes: str = "") -> int:
        normalized_slug = _slugify(slug)
        normalized_name = name.strip() or normalized_slug
        if not normalized_slug:
            raise ValueError("app_slug is required")
        conn.execute(
            """
            INSERT INTO apps (slug, name, notes)
            VALUES (?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE apps.name END,
                notes = CASE WHEN excluded.notes <> '' THEN excluded.notes ELSE apps.notes END
            """,
            (normalized_slug, normalized_name, notes.strip()),
        )
        row = conn.execute("SELECT id FROM apps WHERE slug = ?", (normalized_slug,)).fetchone()
        if not row:
            raise ValueError(f"failed to upsert app: {normalized_slug}")
        return row["id"]

    def _set_app_number_state_conn(
        self,
        conn,
        number_id: int,
        app_id: int,
        status: str,
        result: str = "",
        notes: str = "",
    ) -> None:
        normalized_status = status.strip().lower()
        if normalized_status not in APP_STATE_STATUSES:
            raise ValueError(f"unsupported app state: {status}")
        now_iso = _utc_now_iso()
        use_count_delta = 1 if normalized_status in {"used", "success"} else 0
        last_claimed_at = now_iso if normalized_status == "claimed" else None
        last_used_at = now_iso if normalized_status in {"used", "success"} else None
        conn.execute(
            """
            INSERT INTO app_number_states (
                app_id, number_id, status, use_count, last_result,
                last_claimed_at, last_used_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(app_id, number_id) DO UPDATE SET
                status = excluded.status,
                use_count = app_number_states.use_count + excluded.use_count,
                last_result = excluded.last_result,
                last_claimed_at = COALESCE(excluded.last_claimed_at, app_number_states.last_claimed_at),
                last_used_at = COALESCE(excluded.last_used_at, app_number_states.last_used_at),
                notes = CASE
                    WHEN excluded.notes <> '' THEN excluded.notes
                    ELSE app_number_states.notes
                END
            """,
            (
                app_id,
                number_id,
                normalized_status,
                use_count_delta,
                result.strip() or None,
                last_claimed_at,
                last_used_at,
                notes.strip(),
            ),
        )

    def _expire_claims_conn(self, conn) -> None:
        now_iso = _utc_now_iso()
        rows = conn.execute(
            f"""
            SELECT id, number_id, app_id
            FROM claims
            WHERE status IN {CLAIM_BLOCKING_SQL}
              AND expires_at <= ?
            """,
            (now_iso,),
        ).fetchall()
        if not rows:
            return
        conn.execute(
            f"""
            UPDATE claims
            SET status = 'expired', released_at = ?
            WHERE status IN {CLAIM_BLOCKING_SQL}
              AND expires_at <= ?
            """,
            (now_iso, now_iso),
        )
        for row in rows:
            if row["app_id"] is None:
                continue
            self._set_app_number_state_conn(
                conn,
                number_id=row["number_id"],
                app_id=row["app_id"],
                status="available",
                result="claim expired",
                notes="auto released after expiry",
            )

    def _try_expire_claims_conn(self, conn) -> None:
        try:
            self._expire_claims_conn(conn)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise

    def _claim_candidate_row_conn(self, conn, filters: NumberSelectionFilters) -> sqlite3.Row | None:
        now_iso = _utc_now_iso()
        where_clauses = [
            "p.enabled = 1",
            "COALESCE(TRIM(n.blacklist_reason), '') = ''",
            "COALESCE(ns.restricted, 0) = 0",
            "COALESCE(ns.last_real_message_age_min, n.last_message_age_min) IS NOT NULL",
            "COALESCE(ns.last_real_message_age_min, n.last_message_age_min) <= ?",
            f"""
            NOT EXISTS (
                SELECT 1
                FROM claims c
                WHERE c.number_id = n.id
                  AND c.status IN {CLAIM_BLOCKING_SQL}
                  AND c.expires_at > ?
            )
            """,
        ]
        params: list[object] = [_default_max_age(self.settings, filters.include_cooling), now_iso]
        if filters.country_name:
            where_clauses.append("n.country_name = ?")
            params.append(filters.country_name)
        if filters.provider_id:
            where_clauses.append("ns.provider_id = ?")
            params.append(filters.provider_id)
        if filters.app_slug:
            where_clauses.append(
                f"""
                NOT EXISTS (
                    SELECT 1
                    FROM app_number_states ans
                    JOIN apps a ON a.id = ans.app_id
                    WHERE ans.number_id = n.id
                      AND a.slug = ?
                      AND ans.status IN {APP_BLOCKING_SQL}
                )
                """
            )
            params.append(filters.app_slug)
        return conn.execute(
            f"""
            WITH ranked AS (
                SELECT
                    n.id,
                    n.e164,
                    n.country_name,
                    n.freshness_bucket,
                    n.activity_score,
                    ns.provider_id,
                    p.name AS provider_name,
                    COALESCE(ns.last_real_message_age_min, n.last_message_age_min) AS effective_age,
                    ROW_NUMBER() OVER (
                        PARTITION BY n.id
                        ORDER BY
                            COALESCE(ns.last_real_message_age_min, n.last_message_age_min) ASC,
                            p.priority ASC
                    ) AS rn
                FROM numbers n
                JOIN number_sources ns ON ns.number_id = n.id
                JOIN providers p ON p.id = ns.provider_id
                WHERE {' AND '.join(where_clauses)}
            )
            SELECT
                id, e164, country_name, provider_id, provider_name,
                effective_age, freshness_bucket, activity_score
            FROM ranked
            WHERE rn = 1
            ORDER BY effective_age ASC, activity_score DESC, RANDOM()
            LIMIT 1
            """,
            params,
        ).fetchone()

    def list_providers(self) -> list[ProviderRow]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, name, homepage_url, transport_mode, discovery_mode,
                       detail_mode, auth_mode, enabled, priority, notes
                FROM providers
                ORDER BY enabled DESC, priority ASC, name ASC
                """
            ).fetchall()
        return [ProviderRow.from_row(row) for row in rows]

    def list_provider_configs(self) -> list[ProviderConfigRow]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.id AS provider_id,
                    p.name AS provider_name,
                    p.homepage_url,
                    p.transport_mode,
                    p.discovery_mode,
                    p.detail_mode,
                    p.auth_mode,
                    p.enabled,
                    p.priority,
                    p.notes,
                    pc.user_agent,
                    pc.headers_json,
                    pc.cookies_json,
                    pc.tokens_json,
                    pc.last_verified_at,
                    pc.last_verify_status,
                    pc.last_verify_error
                FROM providers p
                JOIN provider_configs pc ON pc.provider_id = p.id
                ORDER BY p.enabled DESC, p.priority ASC, p.name ASC
                """
            ).fetchall()
        return [ProviderConfigRow.from_row(row) for row in rows]

    def get_provider_config(self, provider_id: str) -> ProviderConfigRow | None:
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    p.id AS provider_id,
                    p.name AS provider_name,
                    p.homepage_url,
                    p.transport_mode,
                    p.discovery_mode,
                    p.detail_mode,
                    p.auth_mode,
                    p.enabled,
                    p.priority,
                    p.notes,
                    pc.user_agent,
                    pc.headers_json,
                    pc.cookies_json,
                    pc.tokens_json,
                    pc.last_verified_at,
                    pc.last_verify_status,
                    pc.last_verify_error
                FROM providers p
                JOIN provider_configs pc ON pc.provider_id = p.id
                WHERE p.id = ?
                """,
                (provider_id,),
            ).fetchone()
        return ProviderConfigRow.from_row(row) if row else None

    def update_provider_config(
        self,
        provider_id: str,
        *,
        enabled: bool,
        priority: int,
        notes: str,
        user_agent: str,
        headers_json: str,
        cookies_json: str,
        tokens_json: str,
    ) -> ProviderConfigRow:
        normalized_headers = _normalize_json_text(headers_json)
        normalized_cookies = _normalize_json_text(cookies_json)
        normalized_tokens = _normalize_json_text(tokens_json)
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE providers
                SET enabled = ?, priority = ?, notes = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, priority, notes.strip(), provider_id),
            )
            conn.execute(
                """
                UPDATE provider_configs
                SET user_agent = ?, headers_json = ?, cookies_json = ?, tokens_json = ?
                WHERE provider_id = ?
                """,
                (
                    user_agent.strip(),
                    normalized_headers,
                    normalized_cookies,
                    normalized_tokens,
                    provider_id,
                ),
            )
        config = self.get_provider_config(provider_id)
        if not config:
            raise ValueError(f"provider not found: {provider_id}")
        return config

    def touch_provider_verification(self, provider_id: str, status: str, error: str = "") -> None:
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE provider_configs
                SET last_verified_at = ?, last_verify_status = ?, last_verify_error = ?
                WHERE provider_id = ?
                """,
                (_utc_now_iso(), status, error.strip(), provider_id),
            )

    def list_apps(self) -> list[AppRow]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, slug, name, notes
                FROM apps
                ORDER BY name ASC, slug ASC
                """
            ).fetchall()
        return [AppRow.from_row(row) for row in rows]

    def list_app_summaries(self) -> list[AppSummaryRow]:
        with self.database.connection() as conn:
            self._try_expire_claims_conn(conn)
            rows = conn.execute(
                f"""
                SELECT
                    a.id,
                    a.slug,
                    a.name,
                    a.notes,
                    (
                        SELECT COUNT(*)
                        FROM app_number_states ans
                        WHERE ans.app_id = a.id
                    ) AS number_count,
                    (
                        SELECT COUNT(*)
                        FROM app_number_states ans
                        WHERE ans.app_id = a.id
                          AND ans.status IN ('used', 'success')
                    ) AS used_count,
                    (
                        SELECT COUNT(*)
                        FROM app_number_states ans
                        WHERE ans.app_id = a.id
                          AND ans.status IN ('blocked', 'blacklisted')
                    ) AS blocked_count,
                    (
                        SELECT COUNT(*)
                        FROM claims c
                        WHERE c.app_id = a.id
                          AND c.status IN {CLAIM_BLOCKING_SQL}
                          AND c.expires_at > ?
                    ) AS active_claim_count
                FROM apps a
                ORDER BY a.name ASC, a.slug ASC
                """
                ,
                (_utc_now_iso(),),
            ).fetchall()
        return [AppSummaryRow.from_row(row) for row in rows]

    def get_app_summary(self, slug: str) -> AppSummaryRow | None:
        normalized_slug = _slugify(slug)
        with self.database.connection() as conn:
            self._try_expire_claims_conn(conn)
            row = conn.execute(
                f"""
                SELECT
                    a.id,
                    a.slug,
                    a.name,
                    a.notes,
                    (
                        SELECT COUNT(*)
                        FROM app_number_states ans
                        WHERE ans.app_id = a.id
                    ) AS number_count,
                    (
                        SELECT COUNT(*)
                        FROM app_number_states ans
                        WHERE ans.app_id = a.id
                          AND ans.status IN ('used', 'success')
                    ) AS used_count,
                    (
                        SELECT COUNT(*)
                        FROM app_number_states ans
                        WHERE ans.app_id = a.id
                          AND ans.status IN ('blocked', 'blacklisted')
                    ) AS blocked_count,
                    (
                        SELECT COUNT(*)
                        FROM claims c
                        WHERE c.app_id = a.id
                          AND c.status IN {CLAIM_BLOCKING_SQL}
                          AND c.expires_at > ?
                    ) AS active_claim_count
                FROM apps a
                WHERE a.slug = ?
                """,
                (_utc_now_iso(), normalized_slug),
            ).fetchone()
        return AppSummaryRow.from_row(row) if row else None

    def list_app_number_usage(self, slug: str) -> list[AppNumberUsageRow]:
        normalized_slug = _slugify(slug)
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    n.id AS number_id,
                    n.e164,
                    n.country_name,
                    n.status AS number_status,
                    n.freshness_bucket,
                    n.last_message_age_min,
                    ans.status AS app_status,
                    ans.use_count,
                    ans.last_result,
                    ans.last_claimed_at,
                    ans.last_used_at,
                    ans.notes
                FROM app_number_states ans
                JOIN apps a ON a.id = ans.app_id
                JOIN numbers n ON n.id = ans.number_id
                WHERE a.slug = ?
                ORDER BY
                    CASE WHEN n.last_message_age_min IS NULL THEN 1 ELSE 0 END ASC,
                    n.last_message_age_min ASC,
                    n.e164 ASC
                """,
                (normalized_slug,),
            ).fetchall()
        return [AppNumberUsageRow.from_row(row) for row in rows]

    def upsert_app(self, slug: str, name: str, notes: str = "") -> AppRow:
        normalized_slug = _slugify(slug)
        normalized_name = name.strip() or normalized_slug
        with self.database.connection() as conn:
            self._ensure_app_conn(conn, normalized_slug, normalized_name, notes.strip())
            row = conn.execute(
                """
                SELECT id, slug, name, notes
                FROM apps
                WHERE slug = ?
                """,
                (normalized_slug,),
            ).fetchone()
        if not row:
            raise ValueError(f"failed to upsert app: {normalized_slug}")
        return AppRow.from_row(row)

    def dashboard_stats(self) -> DashboardStats:
        with self.database.connection() as conn:
            self._try_expire_claims_conn(conn)
            provider_count = conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
            enabled_provider_count = conn.execute("SELECT COUNT(*) FROM providers WHERE enabled = 1").fetchone()[0]
            number_count = conn.execute("SELECT COUNT(*) FROM numbers").fetchone()[0]
            active_number_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM numbers
                WHERE last_message_age_min IS NOT NULL
                  AND last_message_age_min <= ?
                  AND COALESCE(TRIM(blacklist_reason), '') = ''
                """,
                (self.settings.freshness_warm_max,),
            ).fetchone()[0]
            stale_number_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM numbers
                WHERE last_message_age_min IS NOT NULL
                  AND last_message_age_min > ?
                  AND COALESCE(TRIM(blacklist_reason), '') = ''
                """,
                (self.settings.freshness_cooling_max,),
            ).fetchone()[0]
            recent_message_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            app_count = conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
            active_claim_count = conn.execute(
                f"SELECT COUNT(*) FROM claims WHERE status IN {CLAIM_BLOCKING_SQL} AND expires_at > ?",
                (_utc_now_iso(),),
            ).fetchone()[0]
        return DashboardStats(
            provider_count=provider_count,
            enabled_provider_count=enabled_provider_count,
            number_count=number_count,
            active_number_count=active_number_count,
            stale_number_count=stale_number_count,
            recent_message_count=recent_message_count,
            app_count=app_count,
            active_claim_count=active_claim_count,
        )

    def list_numbers(self, filters: NumberSelectionFilters | None = None, limit: int = 100) -> list[NumberRow]:
        filters = filters or NumberSelectionFilters()
        where_clauses: list[str] = []
        params: list[object] = []
        if filters.country_name:
            where_clauses.append("n.country_name = ?")
            params.append(filters.country_name)
        if filters.provider_id:
            where_clauses.append(
                "EXISTS (SELECT 1 FROM number_sources ns2 WHERE ns2.number_id = n.id AND ns2.provider_id = ?)"
            )
            params.append(filters.provider_id)
        if filters.app_slug:
            where_clauses.append(
                f"""
                NOT EXISTS (
                    SELECT 1
                    FROM app_number_states ans
                    JOIN apps a ON a.id = ans.app_id
                    WHERE ans.number_id = n.id
                      AND a.slug = ?
                      AND ans.status IN {APP_BLOCKING_SQL}
                )
                """
            )
            params.append(filters.app_slug)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        with self.database.connection() as conn:
            self._try_expire_claims_conn(conn)
            rows = conn.execute(
                f"""
                SELECT
                    n.id,
                    n.e164,
                    n.country_name,
                    n.status,
                    n.activity_score,
                    n.last_message_age_min,
                    n.freshness_bucket,
                    n.blacklist_reason,
                    COALESCE(GROUP_CONCAT(DISTINCT p.name), '') AS providers
                FROM numbers n
                LEFT JOIN number_sources ns ON ns.number_id = n.id
                LEFT JOIN providers p ON p.id = ns.provider_id
                {where_sql}
                GROUP BY n.id
                ORDER BY
                    CASE WHEN COALESCE(TRIM(n.blacklist_reason), '') = '' THEN 0 ELSE 1 END ASC,
                    CASE WHEN n.last_message_age_min IS NULL THEN 1 ELSE 0 END ASC,
                    n.last_message_age_min ASC,
                    n.activity_score DESC,
                    n.e164 ASC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [NumberRow.from_row(row) for row in rows]

    def pick_number(self, filters: NumberSelectionFilters | None = None) -> SelectedNumber | None:
        filters = filters or NumberSelectionFilters()
        with self.database.connection() as conn:
            self._try_expire_claims_conn(conn)
            row = self._claim_candidate_row_conn(conn, filters)
        return SelectedNumber.from_row(row) if row else None

    def get_number_detail(self, number_id: int) -> NumberDetail | None:
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    e164,
                    country_name,
                    status,
                    freshness_bucket,
                    last_message_age_min,
                    activity_score,
                    blacklist_reason,
                    last_selected_at
                FROM numbers
                WHERE id = ?
                """,
                (number_id,),
            ).fetchone()
        return NumberDetail.from_row(row) if row else None

    def list_number_sources(self, number_id: int) -> list[NumberSourceRow]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    ns.id,
                    ns.provider_id,
                    p.name AS provider_name,
                    ns.source_status,
                    ns.restricted,
                    ns.restricted_reason,
                    ns.provider_country_label,
                    ns.detail_url,
                    ns.last_real_message_at,
                    ns.last_real_message_age_min,
                    ns.last_checked_at
                FROM number_sources ns
                JOIN providers p ON p.id = ns.provider_id
                WHERE ns.number_id = ?
                ORDER BY
                    ns.restricted ASC,
                    CASE WHEN ns.last_real_message_age_min IS NULL THEN 1 ELSE 0 END ASC,
                    ns.last_real_message_age_min ASC,
                    p.priority ASC,
                    p.name ASC
                """,
                (number_id,),
            ).fetchall()
        return [NumberSourceRow.from_row(row) for row in rows]

    def list_number_app_states(self, number_id: int) -> list[AppNumberStateRow]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    a.slug AS app_slug,
                    a.name AS app_name,
                    ans.status,
                    ans.use_count,
                    ans.last_result,
                    ans.last_claimed_at,
                    ans.last_used_at,
                    ans.notes
                FROM app_number_states ans
                JOIN apps a ON a.id = ans.app_id
                WHERE ans.number_id = ?
                ORDER BY a.name ASC, a.slug ASC
                """,
                (number_id,),
            ).fetchall()
        return [AppNumberStateRow.from_row(row) for row in rows]

    def set_number_blacklist(self, number_id: int, reason: str) -> None:
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE numbers
                SET blacklist_reason = ?, status = 'blacklisted'
                WHERE id = ?
                """,
                (reason.strip(), number_id),
            )

    def clear_number_blacklist(self, number_id: int) -> None:
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT last_message_age_min
                FROM numbers
                WHERE id = ?
                """,
                (number_id,),
            ).fetchone()
            if not row:
                return
            status = number_status_from_age(row["last_message_age_min"], False, self.settings)
            conn.execute(
                """
                UPDATE numbers
                SET blacklist_reason = NULL, status = ?
                WHERE id = ?
                """,
                (status, number_id),
            )

    def set_app_number_state(
        self,
        number_id: int,
        app_slug: str,
        app_name: str,
        status: str,
        notes: str = "",
    ) -> None:
        with self.database.connection() as conn:
            app_id = self._ensure_app_conn(conn, app_slug, app_name, notes)
            self._set_app_number_state_conn(
                conn,
                number_id=number_id,
                app_id=app_id,
                status=status,
                result=notes,
                notes=notes,
            )

    def list_messages(self, number_id: int, limit: int | None = None) -> list[MessageRow]:
        message_limit = limit or self.settings.message_limit_per_number
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    m.id,
                    m.sender,
                    m.body,
                    m.received_at,
                    m.otp_code,
                    p.name AS provider_name
                FROM messages m
                JOIN number_sources ns ON ns.id = m.source_id
                JOIN providers p ON p.id = ns.provider_id
                WHERE m.number_id = ?
                ORDER BY
                    CASE WHEN m.received_at IS NULL THEN 1 ELSE 0 END ASC,
                    m.received_at DESC,
                    m.id DESC
                LIMIT ?
                """,
                (number_id, message_limit),
            ).fetchall()
        return [MessageRow.from_row(row) for row in rows]

    def create_api_key(self, name: str, notes: str = "") -> tuple[str, ApiKeyRow]:
        token = generate_api_key()
        token_hash = hash_token(token)
        now_iso = _utc_now_iso()
        with self.database.connection() as conn:
            conn.execute(
                """
                INSERT INTO api_keys (
                    name, token_prefix, token_hash, role, is_active, created_at, notes
                ) VALUES (?, ?, ?, 'admin', 1, ?, ?)
                """,
                (name.strip() or "generated", token_prefix(token), token_hash, now_iso, notes.strip()),
            )
            row = conn.execute(
                """
                SELECT id, name, token_prefix, role, is_active, created_at, last_used_at, notes
                FROM api_keys
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if not row:
            raise ValueError("failed to create api key")
        return token, ApiKeyRow.from_row(row)

    def count_api_keys(self) -> int:
        with self.database.connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM api_keys WHERE is_active = 1").fetchone()[0]

    def list_api_keys(self) -> list[ApiKeyRow]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, name, token_prefix, role, is_active, created_at, last_used_at, notes
                FROM api_keys
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return [ApiKeyRow.from_row(row) for row in rows]

    def revoke_api_key(self, key_id: int) -> ApiKeyRow | None:
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE api_keys SET is_active = 0 WHERE id = ?",
                (key_id,),
            )
            row = conn.execute(
                """
                SELECT id, name, token_prefix, role, is_active, created_at, last_used_at, notes
                FROM api_keys
                WHERE id = ?
                """,
                (key_id,),
            ).fetchone()
        return ApiKeyRow.from_row(row) if row else None

    def authenticate_api_key(self, token: str) -> ApiKeyRow | None:
        token_hash = hash_token(token)
        now_iso = _utc_now_iso()
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT id, name, token_prefix, role, is_active, created_at, last_used_at, notes
                FROM api_keys
                WHERE token_hash = ?
                  AND is_active = 1
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            try:
                conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
                refreshed = conn.execute(
                    """
                    SELECT id, name, token_prefix, role, is_active, created_at, last_used_at, notes
                    FROM api_keys
                    WHERE id = ?
                    """,
                    (row["id"],),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                refreshed = row
        return ApiKeyRow.from_row(refreshed) if refreshed else None

    def create_claim(
        self,
        *,
        app_slug: str | None,
        app_name: str = "",
        country_name: str | None = None,
        provider_id: str | None = None,
        purpose: str = "",
        include_cooling: bool = False,
        ttl_minutes: int | None = None,
    ) -> ClaimRow:
        filters = NumberSelectionFilters(
            country_name=country_name,
            provider_id=provider_id,
            app_slug=_slugify(app_slug or "") or None,
            include_cooling=include_cooling,
        )
        claim_token = generate_claim_token()
        now = _utc_now()
        now_iso = now.isoformat()
        expires_at = (now + timedelta(minutes=ttl_minutes or self.settings.default_claim_ttl_minutes)).isoformat()
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_claims_conn(conn)
            app_id = None
            normalized_slug = _slugify(app_slug or "")
            if normalized_slug:
                app_id = self._ensure_app_conn(conn, normalized_slug, app_name or normalized_slug)
            selection_row = self._claim_candidate_row_conn(conn, filters)
            if not selection_row:
                conn.rollback()
                raise ValueError("no available number matched the requested filters")
            selection = SelectedNumber.from_row(selection_row)
            conn.execute(
                """
                INSERT INTO claims (
                    claim_token, number_id, app_id, requested_country,
                    requested_provider, purpose, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'watching', ?, ?)
                """,
                (
                    claim_token,
                    selection.id,
                    app_id,
                    country_name,
                    provider_id,
                    purpose.strip(),
                    now_iso,
                    expires_at,
                ),
            )
            conn.execute(
                "UPDATE numbers SET last_selected_at = ? WHERE id = ?",
                (now_iso, selection.id),
            )
            if app_id is not None:
                self._set_app_number_state_conn(
                    conn,
                    number_id=selection.id,
                    app_id=app_id,
                    status="claimed",
                    result=purpose.strip() or "claim created",
                    notes=purpose.strip(),
                )
            row = conn.execute(
                """
                SELECT
                    c.id,
                    c.claim_token,
                    c.status,
                    c.purpose,
                    c.created_at,
                    c.expires_at,
                    c.released_at,
                    c.requested_country,
                    c.requested_provider,
                    c.number_id,
                    n.e164,
                    n.country_name,
                    a.slug AS app_slug,
                    a.name AS app_name
                FROM claims c
                JOIN numbers n ON n.id = c.number_id
                LEFT JOIN apps a ON a.id = c.app_id
                WHERE c.claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if not row:
            raise ValueError("failed to create claim")
        return ClaimRow.from_row(row)

    def list_claims(self, active_only: bool = False, limit: int = 100) -> list[ClaimRow]:
        where_sql = f"WHERE c.status IN {CLAIM_BLOCKING_SQL} AND c.expires_at > ?" if active_only else ""
        with self.database.connection() as conn:
            self._try_expire_claims_conn(conn)
            rows = conn.execute(
                f"""
                SELECT
                    c.id,
                    c.claim_token,
                    c.status,
                    c.purpose,
                    c.created_at,
                    c.expires_at,
                    c.released_at,
                    c.requested_country,
                    c.requested_provider,
                    c.number_id,
                    n.e164,
                    n.country_name,
                    a.slug AS app_slug,
                    a.name AS app_name
                FROM claims c
                JOIN numbers n ON n.id = c.number_id
                LEFT JOIN apps a ON a.id = c.app_id
                {where_sql}
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT ?
                """,
                ((_utc_now_iso(), limit) if active_only else (limit,)),
            ).fetchall()
        return [ClaimRow.from_row(row) for row in rows]

    def get_claim(self, claim_token: str) -> ClaimRow | None:
        with self.database.connection() as conn:
            self._try_expire_claims_conn(conn)
            row = conn.execute(
                """
                SELECT
                    c.id,
                    c.claim_token,
                    c.status,
                    c.purpose,
                    c.created_at,
                    c.expires_at,
                    c.released_at,
                    c.requested_country,
                    c.requested_provider,
                    c.number_id,
                    n.e164,
                    n.country_name,
                    a.slug AS app_slug,
                    a.name AS app_name
                FROM claims c
                JOIN numbers n ON n.id = c.number_id
                LEFT JOIN apps a ON a.id = c.app_id
                WHERE c.claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
        return ClaimRow.from_row(row) if row else None

    def list_claim_messages(self, claim_token: str, limit: int | None = None) -> list[MessageRow]:
        claim = self.get_claim(claim_token)
        if not claim:
            return []
        return self.list_messages(number_id=claim.number_id, limit=limit)

    def transition_claim(
        self,
        claim_token: str,
        *,
        status: str,
        result: str = "",
        app_state: str | None = None,
    ) -> ClaimRow | None:
        now_iso = _utc_now_iso()
        with self.database.connection() as conn:
            self._expire_claims_conn(conn)
            claim_row = conn.execute(
                """
                SELECT id, number_id, app_id
                FROM claims
                WHERE claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
            if not claim_row:
                return None
            conn.execute(
                """
                UPDATE claims
                SET status = ?, released_at = CASE
                    WHEN ? IN ('completed', 'released', 'expired') THEN ?
                    ELSE released_at
                END
                WHERE claim_token = ?
                """,
                (status, status, now_iso, claim_token),
            )
            if claim_row["app_id"] is not None and app_state is not None:
                self._set_app_number_state_conn(
                    conn,
                    number_id=claim_row["number_id"],
                    app_id=claim_row["app_id"],
                    status=app_state,
                    result=result,
                    notes=result,
                )
            row = conn.execute(
                """
                SELECT
                    c.id,
                    c.claim_token,
                    c.status,
                    c.purpose,
                    c.created_at,
                    c.expires_at,
                    c.released_at,
                    c.requested_country,
                    c.requested_provider,
                    c.number_id,
                    n.e164,
                    n.country_name,
                    a.slug AS app_slug,
                    a.name AS app_name
                FROM claims c
                JOIN numbers n ON n.id = c.number_id
                LEFT JOIN apps a ON a.id = c.app_id
                WHERE c.claim_token = ?
                """,
                (claim_token,),
            ).fetchone()
        return ClaimRow.from_row(row) if row else None
