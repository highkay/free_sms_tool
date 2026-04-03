from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.db.core import Database
from app.models import JobRow
from app.services.sync import SyncService

SYNC_PROVIDERS_JOB = "sync_providers"
TERMINAL_JOB_STATUSES = ("completed", "failed")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def enqueue_sync(self, provider_id: str | None = None) -> JobRow:
        payload = json.dumps({"provider_id": provider_id}, ensure_ascii=False)
        now_iso = _utc_now_iso()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    job_type, provider_id, status, payload_json, result_json,
                    error_text, worker_id, scheduled_at, started_at, finished_at
                ) VALUES (?, ?, 'queued', ?, '{}', '', NULL, ?, NULL, NULL)
                """,
                (SYNC_PROVIDERS_JOB, provider_id, payload, now_iso),
            )
            job_id = int(cursor.lastrowid)
            job = _job_row(conn, job_id)
        if not job:
            raise ValueError("failed to enqueue sync job")
        return job

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
        payload = json.loads(job.payload_json or "{}")
        if job.job_type != SYNC_PROVIDERS_JOB:
            raise ValueError(f"unsupported job type: {job.job_type}")
        results = self.sync_service.sync_enabled_providers(provider_id=payload.get("provider_id"))
        serializable = [asdict(item) if is_dataclass(item) else item for item in results]
        errors = [item for item in serializable if item.get("error")]
        result = {"results": serializable}
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
