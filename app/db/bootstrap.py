from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.db.core import Database
from app.providers.registry import get_provider_definitions
from app.security import hash_token, token_prefix

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    homepage_url TEXT NOT NULL,
    transport_mode TEXT NOT NULL,
    discovery_mode TEXT NOT NULL,
    detail_mode TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS provider_configs (
    provider_id TEXT PRIMARY KEY REFERENCES providers(id) ON DELETE CASCADE,
    user_agent TEXT DEFAULT '',
    headers_json TEXT DEFAULT '{}',
    cookies_json TEXT DEFAULT '{}',
    tokens_json TEXT DEFAULT '{}',
    last_verified_at TEXT,
    last_verify_status TEXT DEFAULT '',
    last_verify_error TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    e164 TEXT NOT NULL UNIQUE,
    country_code TEXT,
    country_name TEXT,
    national_number TEXT,
    status TEXT NOT NULL DEFAULT 'candidate',
    activity_score REAL NOT NULL DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_message_at TEXT,
    last_message_age_min INTEGER,
    freshness_bucket TEXT,
    last_selected_at TEXT,
    blacklist_reason TEXT,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS number_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number_id INTEGER NOT NULL REFERENCES numbers(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    provider_number_key TEXT NOT NULL,
    detail_url TEXT NOT NULL,
    discovery_url TEXT,
    provider_country_label TEXT,
    provider_status_label TEXT,
    source_status TEXT NOT NULL DEFAULT 'discovered',
    restricted INTEGER NOT NULL DEFAULT 0,
    restricted_reason TEXT,
    requires_auth INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_http_status INTEGER,
    last_error TEXT,
    last_real_message_at TEXT,
    last_real_message_age_min INTEGER,
    raw_snapshot_json TEXT DEFAULT '{}',
    UNIQUE(provider_id, provider_number_key)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number_id INTEGER NOT NULL REFERENCES numbers(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES number_sources(id) ON DELETE CASCADE,
    provider_message_key TEXT,
    sender TEXT,
    body TEXT NOT NULL,
    received_at TEXT,
    observed_at TEXT NOT NULL,
    otp_code TEXT,
    service_hint TEXT,
    language_hint TEXT,
    raw_payload_json TEXT DEFAULT '{}',
    dedupe_hash TEXT NOT NULL,
    UNIQUE(source_id, dedupe_hash)
);

CREATE TABLE IF NOT EXISTS apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_number_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    number_id INTEGER NOT NULL REFERENCES numbers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'available',
    use_count INTEGER NOT NULL DEFAULT 0,
    last_result TEXT,
    last_claimed_at TEXT,
    last_used_at TEXT,
    notes TEXT DEFAULT '',
    UNIQUE(app_id, number_id)
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_token TEXT NOT NULL UNIQUE,
    number_id INTEGER NOT NULL REFERENCES numbers(id) ON DELETE CASCADE,
    app_id INTEGER REFERENCES apps(id) ON DELETE SET NULL,
    requested_country TEXT,
    requested_provider TEXT,
    purpose TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'watching',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_prefix TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'admin',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    http_status INTEGER,
    numbers_seen INTEGER NOT NULL DEFAULT 0,
    messages_seen INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    evidence_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT DEFAULT '',
    worker_id TEXT,
    scheduled_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_numbers_status ON numbers(status);
CREATE INDEX IF NOT EXISTS idx_numbers_message_age ON numbers(last_message_age_min);
CREATE INDEX IF NOT EXISTS idx_number_sources_provider ON number_sources(provider_id);
CREATE INDEX IF NOT EXISTS idx_number_sources_message_age ON number_sources(last_real_message_age_min);
CREATE INDEX IF NOT EXISTS idx_messages_number ON messages(number_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_number_states_lookup ON app_number_states(app_id, number_id, status);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_active_number
ON claims(number_id)
WHERE status = 'watching';
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(is_active, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status_schedule ON jobs(status, scheduled_at, id);
"""


def _seed_bootstrap_api_key(conn, settings: Settings) -> None:
    bootstrap_token = settings.bootstrap_api_key.strip()
    if not bootstrap_token:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO api_keys (
            name, token_prefix, token_hash, role, is_active, created_at, notes
        ) VALUES (?, ?, ?, 'admin', 1, ?, ?)
        ON CONFLICT(token_hash) DO UPDATE SET
            name = excluded.name,
            is_active = 1,
            notes = excluded.notes
        """,
        (
            settings.bootstrap_api_key_name,
            token_prefix(bootstrap_token),
            hash_token(bootstrap_token),
            now_iso,
            "Seeded from BOOTSTRAP_API_KEY",
        ),
    )

def initialize_database(database: Database, settings: Settings) -> None:
    with database.connection() as conn:
        conn.executescript(SCHEMA_SQL)
        for definition in get_provider_definitions():
            conn.execute(
                """
                INSERT INTO providers (
                    id, name, homepage_url, transport_mode, discovery_mode,
                    detail_mode, auth_mode, enabled, priority, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    homepage_url = excluded.homepage_url,
                    transport_mode = excluded.transport_mode,
                    discovery_mode = excluded.discovery_mode,
                    detail_mode = excluded.detail_mode,
                    auth_mode = excluded.auth_mode,
                    enabled = excluded.enabled,
                    priority = excluded.priority,
                    notes = excluded.notes
                """,
                (
                    definition.id,
                    definition.name,
                    definition.homepage_url,
                    definition.transport_mode,
                    definition.discovery_mode,
                    definition.detail_mode,
                    definition.auth_mode,
                    1 if definition.enabled else 0,
                    definition.priority,
                    definition.notes,
                ),
            )
            conn.execute(
                """
                INSERT INTO provider_configs (provider_id)
                VALUES (?)
                ON CONFLICT(provider_id) DO NOTHING
                """,
                (definition.id,),
            )
        _seed_bootstrap_api_key(conn, settings)
