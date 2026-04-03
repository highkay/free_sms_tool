from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.repository import Repository
from app.web.deps import get_repository


def register_app_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/apps")

    @router.get("", response_class=HTMLResponse)
    def apps(request: Request, repository: Repository = Depends(get_repository)) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="apps.html",
            context={"apps": repository.list_app_summaries()},
        )

    @router.get("/{app_slug}", response_class=HTMLResponse)
    def app_detail(
        request: Request,
        app_slug: str,
        repository: Repository = Depends(get_repository),
    ) -> HTMLResponse:
        app = repository.get_app_summary(app_slug)
        if not app:
            raise HTTPException(status_code=404, detail="app not found")
        return templates.TemplateResponse(
            request=request,
            name="app_detail.html",
            context={
                "app": app,
                "number_usage": repository.list_app_number_usage(app_slug),
            },
        )

    @router.post("")
    async def upsert_app(request: Request, repository: Repository = Depends(get_repository)) -> RedirectResponse:
        form = await request.form()
        slug = str(form.get("slug") or "").strip()
        name = str(form.get("name") or "").strip()
        notes = str(form.get("notes") or "").strip()
        if not slug or not name:
            raise HTTPException(status_code=400, detail="slug and name are required")
        repository.upsert_app(slug, name, notes)
        return RedirectResponse(url="/apps", status_code=303)

    return router
