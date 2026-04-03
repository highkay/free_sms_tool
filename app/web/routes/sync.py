from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.jobs import JobService
from app.web.deps import get_job_service


def register_sync_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/sync")

    @router.post("/run", response_class=HTMLResponse)
    def run_sync(
        request: Request,
        provider_id: str | None = Query(default=None),
        job_service: JobService = Depends(get_job_service),
    ) -> HTMLResponse:
        queued_job = job_service.enqueue_sync(provider_id=provider_id)
        return templates.TemplateResponse(
            request=request,
            name="partials/sync_result.html",
            context={
                "queued_job": queued_job,
                "jobs": job_service.list_jobs(limit=5),
            },
        )

    return router
