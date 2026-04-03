from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.repository import Repository
from app.web.deps import get_repository


def register_dashboard_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, repository: Repository = Depends(get_repository)) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "stats": repository.dashboard_stats(),
                "providers": repository.list_providers(),
            },
        )

    return router
