from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.db.repository import Repository
from app.services.jobs import JobService


def get_repository(request: Request) -> Repository:
    return request.app.state.repository  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service  # type: ignore[no-any-return]
