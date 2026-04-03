from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.repository import Repository
from app.models import APP_STATE_STATUSES, NumberSelectionFilters
from app.web.deps import get_repository


def register_number_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/numbers")

    @router.get("", response_class=HTMLResponse)
    def numbers(
        request: Request,
        country_name: str | None = Query(default=None),
        provider_id: str | None = Query(default=None),
        app_slug: str | None = Query(default=None),
        repository: Repository = Depends(get_repository),
    ) -> HTMLResponse:
        filters = NumberSelectionFilters(country_name=country_name, provider_id=provider_id, app_slug=app_slug)
        return templates.TemplateResponse(
            request=request,
            name="numbers.html",
            context={
                "numbers": repository.list_numbers(filters=filters),
                "providers": repository.list_providers(),
                "apps": repository.list_apps(),
                "filters": filters,
            },
        )

    @router.get("/pick", response_class=HTMLResponse)
    def pick_number(
        request: Request,
        country_name: str | None = Query(default=None),
        provider_id: str | None = Query(default=None),
        app_slug: str | None = Query(default=None),
        include_cooling: bool = Query(default=False),
        repository: Repository = Depends(get_repository),
    ) -> HTMLResponse:
        filters = NumberSelectionFilters(
            country_name=country_name,
            provider_id=provider_id,
            app_slug=app_slug,
            include_cooling=include_cooling,
        )
        selection = repository.pick_number(filters=filters)
        return templates.TemplateResponse(
            request=request,
            name="partials/selected_number.html",
            context={"selection": selection},
        )

    @router.get("/{number_id}", response_class=HTMLResponse)
    def number_detail(
        request: Request,
        number_id: int,
        repository: Repository = Depends(get_repository),
    ) -> HTMLResponse:
        number = repository.get_number_detail(number_id)
        if not number:
            raise HTTPException(status_code=404, detail="Number not found")
        return templates.TemplateResponse(
            request=request,
            name="number_detail.html",
            context={
                "number": number,
                "apps": repository.list_apps(),
                "app_state_statuses": APP_STATE_STATUSES,
                "sources": repository.list_number_sources(number_id),
                "app_states": repository.list_number_app_states(number_id),
                "messages": repository.list_messages(number_id),
            },
        )

    @router.post("/{number_id}/blacklist")
    async def blacklist_number(
        request: Request,
        number_id: int,
        repository: Repository = Depends(get_repository),
    ) -> RedirectResponse:
        if not repository.get_number_detail(number_id):
            raise HTTPException(status_code=404, detail="Number not found")
        form = await request.form()
        reason = str(form.get("reason") or "").strip() or "manual blacklist"
        repository.set_number_blacklist(number_id, reason)
        return RedirectResponse(url=f"/numbers/{number_id}", status_code=303)

    @router.post("/{number_id}/blacklist/clear")
    def clear_blacklist(
        number_id: int,
        repository: Repository = Depends(get_repository),
    ) -> RedirectResponse:
        if not repository.get_number_detail(number_id):
            raise HTTPException(status_code=404, detail="Number not found")
        repository.clear_number_blacklist(number_id)
        return RedirectResponse(url=f"/numbers/{number_id}", status_code=303)

    @router.post("/{number_id}/app-state")
    async def set_app_state(
        request: Request,
        number_id: int,
        repository: Repository = Depends(get_repository),
    ) -> RedirectResponse:
        if not repository.get_number_detail(number_id):
            raise HTTPException(status_code=404, detail="Number not found")
        form = await request.form()
        app_slug = str(form.get("app_slug") or "").strip()
        app_name = str(form.get("app_name") or "").strip()
        status = str(form.get("status") or "available").strip().lower()
        notes = str(form.get("notes") or "").strip()
        if not app_slug:
            raise HTTPException(status_code=400, detail="app_slug is required")
        try:
            repository.set_app_number_state(
                number_id=number_id,
                app_slug=app_slug,
                app_name=app_name,
                status=status,
                notes=notes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=f"/numbers/{number_id}", status_code=303)

    return router
