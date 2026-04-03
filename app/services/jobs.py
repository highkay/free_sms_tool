from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.db.core import Database
from app.db.repository import Repository
from app.models import JobRow, NumberSelectionFilters
from app.services.sync import SyncService

SYNC_PROVIDERS_JOB = "sync_providers"
TERMINAL_JOB_STATUSES = ("completed", "failed")
ACTIVE_JOB_STATUSES = ("queued", "running")
AUTO_REPLENISH_LOW_WATERMARK_REASON = "auto_replenish_low_watermark"
AUTO_REPLENISH_APP_EXHAUSTED_REASON = "auto_replenish_app_exhausted"
AUTO_REPLENISH_REASONS = (
    AUTO_REPLENISH_LOW_WATERMARK_REASON,
    AUTO_REPLENISH_APP_EXHAUSTED_REASON,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _job_row(conn, job_id: int) -> JobRow | None:
    row = conn.execute(
        """
        SELECT
            id,
            job_type,
            provider_id,
            status,
            payload_json,
            result_json,
            error_text,
            worker_id,
            scheduled_at,
            started_at,
            finished_at
        FROM jobs
        WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    return JobRow.from_row(row) if row else None


class JobExecutionError(Exception):
    def __init__(self, message: str, *, result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = result or {}


class JobService:
    def __init__(self, database: Database, settings: Settings, sync_service: SyncService) -> None:
        self.database = database
        self.settings = settings
        self.sync_service = sync_service

    def _repository(self) -> Repository:
        return Repository(database=self.database, settings=self.settings)

    def _job_payload(self, job: JobRow) -> dict[str, Any]:
        payload = json.loads(job.payload_json or "{}")
        return payload if isinstance(payload, dict) else {}

    def get_job_payload(self, job: JobRow) -> dict[str, Any]:
        return self._job_payload(job)

    def _find_active_sync_job(self, provider_id: str | None = None) -> JobRow | None:
        provider_where = "provider_id IS NULL" if provider_id is None else "provider_id = ?"
        params: tuple[object, ...] = ACTIVE_JOB_STATUSES if provider_id is None else (*ACTIVE_JOB_STATUSES, provider_id)
        with self.database.connection() as conn:
            row = conn.execute(
                f"""
                SELECT
                    id,
                    job_type,
                    provider_id,
                    status,
                    payload_json,
                    result_json,
                    error_text,
                    worker_id,
                    scheduled_at,
                    started_at,
                    finished_at
                FROM jobs
                WHERE job_type = ?
                  AND status IN (?, ?)
                  AND {provider_where}
                ORDER BY scheduled_at ASC, id ASC
                LIMIT 1
                """,
                (SYNC_PROVIDERS_JOB, *params),
            ).fetchone()
        return JobRow.from_row(row) if row else None

    def _find_recent_auto_replenish_job(self) -> JobRow | None:
        cutoff_iso = (_utc_now() - timedelta(seconds=self.settings.auto_replenish_cooldown_seconds)).isoformat()
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    job_type,
                    provider_id,
                    status,
                    payload_json,
                    result_json,
                    error_text,
                    worker_id,
                    scheduled_at,
                    started_at,
                    finished_at
                FROM jobs
                WHERE job_type = ?
                  AND provider_id IS NULL
                  AND scheduled_at >= ?
                ORDER BY scheduled_at DESC, id DESC
                LIMIT 20
                """,
                (SYNC_PROVIDERS_JOB, cutoff_iso),
            ).fetchall()
        for row in rows:
            job = JobRow.from_row(row)
            if self._job_payload(job).get("reason") in AUTO_REPLENISH_REASONS:
                return job
        return None

    def enqueue_sync(
        self,
        provider_id: str | None = None,
        *,
        limit_per_provider: int | None = None,
        reason: str = "manual",
        app_slug: str | None = None,
    ) -> JobRow:
        payload: dict[str, Any] = {"provider_id": provider_id, "reason": reason}
        if limit_per_provider is not None:
            payload["limit_per_provider"] = max(1, int(limit_per_provider))
        if app_slug:
            payload["app_slug"] = app_slug
        payload_json = json.dumps(payload, ensure_ascii=False)
        now_iso = _utc_now_iso()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    job_type, provider_id, status, payload_json, result_json,
                    error_text, worker_id, scheduled_at, started_at, finished_at
                ) VALUES (?, ?, 'queued', ?, '{}', '', NULL, ?, NULL, NULL)
                """,
                (SYNC_PROVIDERS_JOB, provider_id, payload_json, now_iso),
            )
            job_id = int(cursor.lastrowid)
            job = _job_row(conn, job_id)
        if not job:
            raise ValueError("failed to enqueue sync job")
        return job

    def ensure_auto_replenish_sync(
        self,
        *,
        reason: str,
        app_slug: str | None = None,
    ) -> JobRow | None:
        active_job = self._find_active_sync_job(provider_id=None)
        if active_job:
            return active_job
        recent_job = self._find_recent_auto_replenish_job()
        if recent_job:
            return recent_job
        return self.enqueue_sync(
            provider_id=None,
            limit_per_provider=self.settings.auto_replenish_sync_limit_per_provider,
            reason=reason,
            app_slug=app_slug,
        )

    def maybe_enqueue_auto_replenish(self) -> JobRow | None:
        if not self.settings.auto_replenish_enabled:
            return None
        inventory = self._repository().get_pool_inventory()
        if inventory.enabled_provider_count <= 0:
            return None
        if (
            inventory.total_numbers > 0
            and inventory.consumption_ratio < self.settings.auto_replenish_consumption_threshold
        ):
            return None
        return self.ensure_auto_replenish_sync(reason=AUTO_REPLENISH_LOW_WATERMARK_REASON)

    def maybe_enqueue_app_exhausted_replenish(self, app_slug: str | None) -> JobRow | None:
        normalized_slug = (app_slug or "").strip()
        if not self.settings.auto_replenish_enabled or not normalized_slug:
            return None
        repository = self._repository()
        inventory = repository.get_pool_inventory()
        if inventory.enabled_provider_count <= 0:
            return None
        eligible_numbers = repository.count_eligible_numbers(NumberSelectionFilters(app_slug=normalized_slug))
        if eligible_numbers > 0:
            return None
        return self.ensure_auto_replenish_sync(
            reason=AUTO_REPLENISH_APP_EXHAUSTED_REASON,
            app_slug=normalized_slug,
        )

    def list_jobs(self, limit: int = 20) -> list[JobRow]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    job_type,
                    provider_id,
                    status,
                    payload_json,
                    result_json,
                    error_text,
                    worker_id,
                    scheduled_at,
                    started_at,
                    finished_at
                FROM jobs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [JobRow.from_row(row) for row in rows]

    def get_job(self, job_id: int) -> JobRow | None:
        with self.database.connection() as conn:
            return _job_row(conn, job_id)

    def claim_next_job(self, worker_id: str) -> JobRow | None:
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id
                FROM jobs
                WHERE status = 'queued'
                ORDER BY scheduled_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                conn.commit()
                return None
            now_iso = _utc_now_iso()
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running', worker_id = ?, started_at = ?, finished_at = NULL, error_text = ''
                WHERE id = ?
                """,
                (worker_id, now_iso, row["id"]),
            )
            job = _job_row(conn, row["id"])
            conn.commit()
            return job
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def run_next_job(self, worker_id: str) -> JobRow | None:
        job = self.claim_next_job(worker_id)
        if not job:
            return None
        return self.run_job(job)

    def run_job(self, job: JobRow) -> JobRow:
        try:
            result = self._execute_job(job)
        except JobExecutionError as exc:
            return self._finish_job(job.id, status="failed", result=exc.result, error_text=str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._finish_job(job.id, status="failed", error_text=str(exc))
        return self._finish_job(job.id, status="completed", result=result)

    def _execute_job(self, job: JobRow) -> dict[str, Any]:
        payload = self._job_payload(job)
        if job.job_type != SYNC_PROVIDERS_JOB:
            raise ValueError(f"unsupported job type: {job.job_type}")
        limit_per_provider = payload.get("limit_per_provider")
        if limit_per_provider is not None:
            limit_per_provider = max(1, int(limit_per_provider))
        results = self.sync_service.sync_enabled_providers(
            provider_id=payload.get("provider_id"),
            limit_per_provider=limit_per_provider,
        )
        serializable = [asdict(item) if is_dataclass(item) else item for item in results]
        errors = [item for item in serializable if item.get("error")]
        result = {
            "results": serializable,
            "reason": payload.get("reason") or "manual",
            "limit_per_provider": limit_per_provider,
            "app_slug": payload.get("app_slug"),
        }
        if errors:
            summary = "; ".join(
                f"{item.get('provider_id')}: {item.get('error')}" for item in errors if item.get("provider_id")
            )
            raise JobExecutionError(summary or "job execution failed", result=result)
        return result

    def _finish_job(
        self,
        job_id: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_text: str = "",
    ) -> JobRow:
        now_iso = _utc_now_iso()
        result_json = json.dumps(result or {}, ensure_ascii=False)
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, result_json = ?, error_text = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, result_json, error_text.strip(), now_iso, job_id),
            )
            job = _job_row(conn, job_id)
        if not job:
            raise ValueError(f"job not found after finish: {job_id}")
        return job
