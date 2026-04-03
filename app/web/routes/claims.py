from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.repository import Repository
from app.web.deps import get_repository


def register_claim_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/claims")

    @router.get("", response_class=HTMLResponse)
    def claims(request: Request, repository: Repository = Depends(get_repository)) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="claims.html",
            context={
                "claims": repository.list_claims(limit=100),
                "providers": repository.list_providers(),
                "apps": repository.list_apps(),
            },
        )

    @router.get("/{claim_token}", response_class=HTMLResponse)
    def claim_detail(
        request: Request,
        claim_token: str,
        repository: Repository = Depends(get_repository),
    ) -> HTMLResponse:
        claim = repository.get_claim(claim_token)
        if not claim:
            raise HTTPException(status_code=404, detail="claim not found")
        return templates.TemplateResponse(
            request=request,
            name="claim_detail.html",
            context={
                "claim": claim,
                "messages": repository.list_claim_messages(claim_token),
            },
        )

    @router.post("")
    async def create_claim(request: Request, repository: Repository = Depends(get_repository)) -> RedirectResponse:
        form = await request.form()
        try:
            repository.create_claim(
                app_slug=str(form.get("app_slug") or "").strip() or None,
                app_name=str(form.get("app_name") or "").strip(),
                country_name=str(form.get("country_name") or "").strip() or None,
                provider_id=str(form.get("provider_id") or "").strip() or None,
                purpose=str(form.get("purpose") or "").strip(),
                include_cooling=str(form.get("include_cooling") or "").strip().lower() in {"1", "true", "on", "yes"},
                ttl_minutes=int(form.get("ttl_minutes") or 0) or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url="/claims", status_code=303)

    @router.post("/{claim_token}/release")
    async def release_claim(
        claim_token: str,
        request: Request,
        repository: Repository = Depends(get_repository),
    ) -> RedirectResponse:
        form = await request.form()
        claim = repository.transition_claim(
            claim_token,
            status="released",
            result=str(form.get("result") or "").strip(),
            app_state="available",
        )
        if not claim:
            raise HTTPException(status_code=404, detail="claim not found")
        return RedirectResponse(url="/claims", status_code=303)

    @router.post("/{claim_token}/complete")
    async def complete_claim(
        claim_token: str,
        request: Request,
        repository: Repository = Depends(get_repository),
    ) -> RedirectResponse:
        form = await request.form()
        claim = repository.transition_claim(
            claim_token,
            status="completed",
            result=str(form.get("result") or "").strip(),
            app_state=str(form.get("app_state") or "success").strip() or "success",
        )
        if not claim:
            raise HTTPException(status_code=404, detail="claim not found")
        return RedirectResponse(url="/claims", status_code=303)

    return router
