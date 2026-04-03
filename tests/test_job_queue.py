from __future__ import annotations

import json

from app.config import Settings, get_settings
from app.db.bootstrap import initialize_database
from app.db.core import Database
from app.db.repository import Repository
from app.main import create_app
from app.services.jobs import JobService
from app.services.sync import SyncResult


class FakeSyncService:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, int | None]] = []

    def sync_enabled_providers(
        self,
        provider_id: str | None = None,
        limit_per_provider: int | None = None,
    ) -> list[SyncResult]:
        self.calls.append((provider_id, limit_per_provider))
        return [
            SyncResult(
                provider_id=provider_id or "all",
                discovered_count=3,
                synced_count=2,
                message_count=5,
            )
        ]


class FailingSyncService:
    def sync_enabled_providers(
        self,
        provider_id: str | None = None,
        limit_per_provider: int | None = None,
    ) -> list[SyncResult]:
        return [
            SyncResult(
                provider_id=provider_id or "all",
                discovered_count=1,
                synced_count=0,
                message_count=0,
                error="upstream failed",
            )
        ]


def _seed_pool(database: Database, *, fresh_count: int, stale_count: int = 0) -> None:
    with database.connection() as conn:
        next_id = 1
        for _ in range(fresh_count):
            conn.execute(
                """
                INSERT INTO numbers (
                    id, e164, country_name, national_number, status, activity_score,
                    first_seen_at, last_seen_at, last_message_at, last_message_age_min, freshness_bucket
                ) VALUES (
                    ?, ?, 'United States', ?, 'active', 100.0,
                    datetime('now'), datetime('now'), datetime('now'), 5, 'hot'
                )
                """,
                (next_id, f"+1555000{next_id:04d}", f"1555000{next_id:04d}"),
            )
            conn.execute(
                """
                INSERT INTO number_sources (
                    number_id, provider_id, provider_number_key, detail_url, discovery_url,
                    provider_country_label, source_status, restricted, restricted_reason,
                    last_seen_at, last_checked_at, last_success_at, last_real_message_at,
                    last_real_message_age_min, raw_snapshot_json
                ) VALUES (
                    ?, 'receive_smss', ?, ?, 'https://receive-smss.com/', 'United States',
                    'open', 0, NULL, datetime('now'), datetime('now'), datetime('now'),
                    datetime('now'), 5, '{}'
                )
                """,
                (next_id, f"1555000{next_id:04d}", f"https://receive-smss.com/sms/1555000{next_id:04d}/"),
            )
            next_id += 1
        for _ in range(stale_count):
            conn.execute(
                """
                INSERT INTO numbers (
                    id, e164, country_name, national_number, status, activity_score,
                    first_seen_at, last_seen_at, last_message_at, last_message_age_min, freshness_bucket
                ) VALUES (
                    ?, ?, 'United States', ?, 'stale', 1.0,
                    datetime('now'), datetime('now'), datetime('now', '-12 hours'), 720, 'stale'
                )
                """,
                (next_id, f"+1666000{next_id:04d}", f"1666000{next_id:04d}"),
            )
            conn.execute(
                """
                INSERT INTO number_sources (
                    number_id, provider_id, provider_number_key, detail_url, discovery_url,
                    provider_country_label, source_status, restricted, restricted_reason,
                    last_seen_at, last_checked_at, last_success_at, last_real_message_at,
                    last_real_message_age_min, raw_snapshot_json
                ) VALUES (
                    ?, 'receive_smss', ?, ?, 'https://receive-smss.com/', 'United States',
                    'open', 0, NULL, datetime('now'), datetime('now'), datetime('now'),
                    datetime('now', '-12 hours'), 720, '{}'
                )
                """,
                (next_id, f"1666000{next_id:04d}", f"https://receive-smss.com/sms/1666000{next_id:04d}/"),
            )
            next_id += 1


def test_job_service_executes_queued_sync_job(tmp_path):
    settings = Settings(database_path=tmp_path / "jobs.db")
    database = Database(settings.database_path)
    initialize_database(database, settings)
    fake_sync = FakeSyncService()
    job_service = JobService(database=database, settings=settings, sync_service=fake_sync)  # type: ignore[arg-type]

    queued_job = job_service.enqueue_sync(provider_id="receive_smss")
    assert queued_job.status == "queued"

    finished_job = job_service.run_next_job("collector-test")
    assert finished_job is not None
    assert finished_job.status == "completed"
    assert finished_job.provider_id == "receive_smss"
    assert fake_sync.calls == [("receive_smss", None)]

    result = json.loads(finished_job.result_json)
    assert result["results"][0]["provider_id"] == "receive_smss"
    assert result["results"][0]["synced_count"] == 2


def test_job_service_marks_sync_errors_as_failed(tmp_path):
    settings = Settings(database_path=tmp_path / "jobs_failed.db")
    database = Database(settings.database_path)
    initialize_database(database, settings)
    job_service = JobService(database=database, settings=settings, sync_service=FailingSyncService())  # type: ignore[arg-type]

    queued_job = job_service.enqueue_sync(provider_id="receive_smss")
    finished_job = job_service.run_job(queued_job)

    assert finished_job.status == "failed"
    assert "receive_smss" in finished_job.error_text
    result = json.loads(finished_job.result_json)
    assert result["results"][0]["error"] == "upstream failed"


def test_auto_replenish_enqueues_single_deep_sync_job(tmp_path):
    settings = Settings(
        database_path=tmp_path / "auto_replenish.db",
        auto_replenish_consumption_threshold=0.8,
        auto_replenish_sync_limit_per_provider=30,
        auto_replenish_cooldown_seconds=600,
    )
    database = Database(settings.database_path)
    initialize_database(database, settings)
    _seed_pool(database, fresh_count=1, stale_count=9)
    fake_sync = FakeSyncService()
    job_service = JobService(database=database, settings=settings, sync_service=fake_sync)  # type: ignore[arg-type]

    first_job = job_service.maybe_enqueue_auto_replenish()
    second_job = job_service.maybe_enqueue_auto_replenish()

    assert first_job is not None
    assert second_job is not None
    assert first_job.id == second_job.id
    assert len(job_service.list_jobs(limit=10)) == 1

    finished_job = job_service.run_next_job("collector-test")
    assert finished_job is not None
    assert fake_sync.calls == [(None, 30)]
    result = json.loads(finished_job.result_json)
    assert result["reason"] == "auto_replenish_low_watermark"
    assert result["limit_per_provider"] == 30


def test_auto_replenish_respects_cooldown_after_recent_job(tmp_path):
    settings = Settings(
        database_path=tmp_path / "auto_replenish_cooldown.db",
        auto_replenish_consumption_threshold=0.8,
        auto_replenish_sync_limit_per_provider=25,
        auto_replenish_cooldown_seconds=600,
    )
    database = Database(settings.database_path)
    initialize_database(database, settings)
    _seed_pool(database, fresh_count=1, stale_count=9)
    job_service = JobService(database=database, settings=settings, sync_service=FakeSyncService())  # type: ignore[arg-type]

    first_job = job_service.maybe_enqueue_auto_replenish()
    assert first_job is not None
    finished_job = job_service.run_next_job("collector-test")
    assert finished_job is not None
    second_job = job_service.maybe_enqueue_auto_replenish()

    assert second_job is not None
    assert second_job.id == finished_job.id
    assert len(job_service.list_jobs(limit=10)) == 1


def test_app_exhaustion_enqueues_replenish_job(tmp_path):
    settings = Settings(
        database_path=tmp_path / "app_exhaustion.db",
        auto_replenish_sync_limit_per_provider=20,
    )
    database = Database(settings.database_path)
    initialize_database(database, settings)
    _seed_pool(database, fresh_count=3)
    fake_sync = FakeSyncService()
    job_service = JobService(database=database, settings=settings, sync_service=fake_sync)  # type: ignore[arg-type]
    repo = Repository(database=database, settings=settings)
    app = repo.upsert_app("openai", "OpenAI", "app exhaustion test")
    with database.connection() as conn:
        for number_id in [1, 2, 3]:
            conn.execute(
                """
                INSERT INTO app_number_states (
                    app_id, number_id, status, use_count, last_result, notes
                ) VALUES (?, ?, 'used', 1, 'app exhaustion', 'app exhaustion')
                ON CONFLICT(app_id, number_id) DO UPDATE SET
                    status = 'used',
                    use_count = 1,
                    last_result = 'app exhaustion',
                    notes = 'app exhaustion'
                """,
                (app.id, number_id),
            )

    job = job_service.maybe_enqueue_app_exhausted_replenish("openai")
    assert job is not None
    payload = json.loads(job.payload_json)
    assert payload["reason"] == "auto_replenish_app_exhausted"
    assert payload["app_slug"] == "openai"


def test_create_app_is_idempotent_for_web_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "idempotent.db"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "idempotent.log"))
    monkeypatch.setenv("BOOTSTRAP_API_KEY", "bootstrap-test-token")
    get_settings.cache_clear()

    app_one = create_app()
    app_two = create_app()

    paths_one = sorted(route.path for route in app_one.routes)
    paths_two = sorted(route.path for route in app_two.routes)

    assert paths_one == paths_two
    assert paths_one.count("/sync/run") == 1
    assert paths_one.count("/providers") == 1
