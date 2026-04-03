from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.repository import Repository
from app.web.deps import get_repository


def register_auth_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/auth")

    @router.get("", response_class=HTMLResponse)
    def auth_keys(
        request: Request,
        repository: Repository = Depends(get_repository),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="auth_keys.html",
            context={
                "keys": repository.list_api_keys(),
                "generated_token": request.query_params.get("generated_token"),
            },
        )

    @router.post("/keys")
    async def create_key(
        request: Request,
        repository: Repository = Depends(get_repository),
    ) -> HTMLResponse:
        form = await request.form()
        name = str(form.get("name") or "").strip()
        notes = str(form.get("notes") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        token, _row = repository.create_api_key(name=name, notes=notes)
        return templates.TemplateResponse(
            request=request,
            name="auth_keys.html",
            context={
                "keys": repository.list_api_keys(),
                "generated_token": token,
            },
        )

    @router.post("/keys/{key_id}/revoke")
    def revoke_key(
        key_id: int,
        repository: Repository = Depends(get_repository),
    ) -> RedirectResponse:
        row = repository.revoke_api_key(key_id)
        if not row:
            raise HTTPException(status_code=404, detail="api key not found")
        return RedirectResponse(url="/auth", status_code=303)

    return router
