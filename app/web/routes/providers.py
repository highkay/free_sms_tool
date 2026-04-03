from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.repository import Repository
from app.web.deps import get_repository


def register_provider_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/providers")

    @router.get("", response_class=HTMLResponse)
    def providers(request: Request, repository: Repository = Depends(get_repository)) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="providers.html",
            context={"providers": repository.list_provider_configs()},
        )

    @router.post("/{provider_id}")
    async def update_provider(
        provider_id: str,
        request: Request,
        repository: Repository = Depends(get_repository),
    ) -> RedirectResponse:
        form = await request.form()
        try:
            repository.update_provider_config(
                provider_id,
                enabled=str(form.get("enabled") or "").strip().lower() in {"1", "true", "on", "yes"},
                priority=int(form.get("priority") or 100),
                notes=str(form.get("notes") or "").strip(),
                user_agent=str(form.get("user_agent") or "").strip(),
                headers_json=str(form.get("headers_json") or "{}"),
                cookies_json=str(form.get("cookies_json") or "{}"),
                tokens_json=str(form.get("tokens_json") or "{}"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url="/providers", status_code=303)

    return router
