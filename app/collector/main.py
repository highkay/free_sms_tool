from __future__ import annotations

import argparse
import os
import time

from loguru import logger

from app.config import get_settings
from app.db.bootstrap import initialize_database
from app.db.core import Database
from app.logging import configure_logging
from app.services.jobs import JobService
from app.services.sync import SyncService


def build_job_service() -> tuple[JobService, str]:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings.database_path)
    initialize_database(database, settings)
    sync_service = SyncService(database=database, settings=settings)
    worker_id = f"collector-{os.getpid()}"
    return JobService(database=database, settings=settings, sync_service=sync_service), worker_id


def run_once(job_service: JobService, worker_id: str) -> bool:
    job = job_service.run_next_job(worker_id)
    if not job:
        return False
    logger.info("collector_processed_job id={} type={} status={}", job.id, job.job_type, job.status)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Free SMS Tool collector.")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit.")
    args = parser.parse_args()

    job_service, worker_id = build_job_service()
    settings = job_service.settings
    if args.once:
        run_once(job_service, worker_id)
        return

    logger.info("collector_started worker_id={} poll_seconds={}", worker_id, settings.collector_poll_seconds)
    while True:
        processed = run_once(job_service, worker_id)
        if processed:
            continue
        auto_job = job_service.maybe_enqueue_auto_replenish()
        if auto_job:
            logger.info(
                "collector_auto_replenish_job id={} status={} reason={}",
                auto_job.id,
                auto_job.status,
                job_service.get_job_payload(auto_job).get("reason"),
            )
            continue
        time.sleep(settings.collector_poll_seconds)


if __name__ == "__main__":
    main()
