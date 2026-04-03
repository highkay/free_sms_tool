from __future__ import annotations

import json

from app.config import Settings, get_settings
from app.db.bootstrap import initialize_database
from app.db.core import Database
from app.main import create_app
from app.services.jobs import JobService
from app.services.sync import SyncResult


class FakeSyncService:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def sync_enabled_providers(self, provider_id: str | None = None) -> list[SyncResult]:
        self.calls.append(provider_id)
        return [
            SyncResult(
                provider_id=provider_id or "all",
                discovered_count=3,
                synced_count=2,
                message_count=5,
            )
        ]


class FailingSyncService:
    def sync_enabled_providers(self, provider_id: str | None = None) -> list[SyncResult]:
        return [
            SyncResult(
                provider_id=provider_id or "all",
                discovered_count=1,
                synced_count=0,
                message_count=0,
                error="upstream failed",
            )
        ]


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
    assert fake_sync.calls == ["receive_smss"]

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
