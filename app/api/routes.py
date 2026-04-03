from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import require_api_key
from app.api.schemas import (
    ApiKeyCreateRequest,
    AppStateRequest,
    AppUpsertRequest,
    BlacklistRequest,
    ClaimCreateRequest,
    ClaimTransitionRequest,
    NumberPickRequest,
    ProviderConfigUpdateRequest,
)
from app.db.repository import Repository
from app.models import NumberSelectionFilters
from app.services.jobs import JobService

router = APIRouter(prefix="/api", tags=["api"])


def _repo(request: Request) -> Repository:
    return request.app.state.repository  # type: ignore[no-any-return]


def _job_service(request: Request) -> JobService:
    return request.app.state.job_service  # type: ignore[no-any-return]


def _to_dict(obj):
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def _provider_config_payload(config) -> dict:
    payload = _to_dict(config)
    payload["headers"] = json.loads(payload.pop("headers_json"))
    payload["cookies"] = json.loads(payload.pop("cookies_json"))
    payload["tokens"] = json.loads(payload.pop("tokens_json"))
    return payload


@router.get("/health", tags=["health"])
def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "app": request.app.title}


@router.get("/auth/keys", dependencies=[Depends(require_api_key)], tags=["auth"])
def list_api_keys(request: Request) -> list[dict]:
    repository = _repo(request)
    return [_to_dict(item) for item in repository.list_api_keys()]


@router.post("/auth/keys", dependencies=[Depends(require_api_key)], tags=["auth"])
def create_api_key(payload: ApiKeyCreateRequest, request: Request) -> dict:
    repository = _repo(request)
    token, row = repository.create_api_key(name=payload.name, notes=payload.notes)
    result = _to_dict(row)
    result["token"] = token
    return result


@router.post("/auth/keys/{key_id}/revoke", dependencies=[Depends(require_api_key)], tags=["auth"])
def revoke_api_key(key_id: int, request: Request) -> dict:
    repository = _repo(request)
    row = repository.revoke_api_key(key_id)
    if not row:
        raise HTTPException(status_code=404, detail="api key not found")
    return _to_dict(row)


@router.get("/providers", dependencies=[Depends(require_api_key)], tags=["providers"])
def list_providers(request: Request) -> list[dict]:
    repository = _repo(request)
    return [_provider_config_payload(item) for item in repository.list_provider_configs()]


@router.get("/providers/{provider_id}/config", dependencies=[Depends(require_api_key)], tags=["providers"])
def get_provider_config(provider_id: str, request: Request) -> dict:
    repository = _repo(request)
    config = repository.get_provider_config(provider_id)
    if not config:
        raise HTTPException(status_code=404, detail="provider not found")
    return _provider_config_payload(config)


@router.put("/providers/{provider_id}/config", dependencies=[Depends(require_api_key)], tags=["providers"])
def update_provider_config(provider_id: str, payload: ProviderConfigUpdateRequest, request: Request) -> dict:
    repository = _repo(request)
    try:
        config = repository.update_provider_config(
            provider_id,
            enabled=payload.enabled,
            priority=payload.priority,
            notes=payload.notes,
            user_agent=payload.user_agent,
            headers_json=json.dumps(payload.headers, ensure_ascii=False),
            cookies_json=json.dumps(payload.cookies, ensure_ascii=False),
            tokens_json=json.dumps(payload.tokens, ensure_ascii=False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _provider_config_payload(config)


@router.post(
    "/providers/{provider_id}/sync",
    dependencies=[Depends(require_api_key)],
    status_code=status.HTTP_202_ACCEPTED,
    tags=["providers"],
)
def sync_provider(provider_id: str, request: Request) -> dict:
    job_service = _job_service(request)
    return _to_dict(job_service.enqueue_sync(provider_id=provider_id))


@router.post("/sync/run", dependencies=[Depends(require_api_key)], status_code=status.HTTP_202_ACCEPTED, tags=["sync"])
def sync_enabled(request: Request) -> dict:
    job_service = _job_service(request)
    return _to_dict(job_service.enqueue_sync())


@router.get("/jobs", dependencies=[Depends(require_api_key)], tags=["jobs"])
def list_jobs(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> list[dict]:
    job_service = _job_service(request)
    return [_to_dict(item) for item in job_service.list_jobs(limit=limit)]


@router.get("/apps", dependencies=[Depends(require_api_key)], tags=["apps"])
def list_apps(request: Request) -> list[dict]:
    repository = _repo(request)
    return [_to_dict(item) for item in repository.list_app_summaries()]


@router.post("/apps", dependencies=[Depends(require_api_key)], tags=["apps"])
def upsert_app(payload: AppUpsertRequest, request: Request) -> dict:
    repository = _repo(request)
    app = repository.upsert_app(payload.slug, payload.name, payload.notes)
    return _to_dict(app)


@router.get("/numbers", dependencies=[Depends(require_api_key)], tags=["numbers"])
def list_numbers(
    request: Request,
    country_name: str | None = Query(default=None),
    provider_id: str | None = Query(default=None),
    app_slug: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    repository = _repo(request)
    filters = NumberSelectionFilters(country_name=country_name, provider_id=provider_id, app_slug=app_slug)
    return [_to_dict(item) for item in repository.list_numbers(filters=filters, limit=limit)]


@router.post("/numbers/pick", dependencies=[Depends(require_api_key)], tags=["numbers"])
def pick_number(payload: NumberPickRequest, request: Request) -> dict | None:
    repository = _repo(request)
    filters = NumberSelectionFilters(
        country_name=payload.country_name,
        provider_id=payload.provider_id,
        app_slug=payload.app_slug,
        include_cooling=payload.include_cooling,
    )
    selection = repository.pick_number(filters=filters)
    return _to_dict(selection) if selection else None


@router.get("/numbers/{number_id}", dependencies=[Depends(require_api_key)], tags=["numbers"])
def get_number(number_id: int, request: Request) -> dict:
    repository = _repo(request)
    number = repository.get_number_detail(number_id)
    if not number:
        raise HTTPException(status_code=404, detail="number not found")
    payload = _to_dict(number)
    payload["sources"] = [_to_dict(item) for item in repository.list_number_sources(number_id)]
    payload["app_states"] = [_to_dict(item) for item in repository.list_number_app_states(number_id)]
    return payload


@router.get("/numbers/{number_id}/messages", dependencies=[Depends(require_api_key)], tags=["numbers"])
def get_number_messages(number_id: int, request: Request, limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    repository = _repo(request)
    if not repository.get_number_detail(number_id):
        raise HTTPException(status_code=404, detail="number not found")
    return [_to_dict(item) for item in repository.list_messages(number_id, limit=limit)]


@router.post("/numbers/{number_id}/blacklist", dependencies=[Depends(require_api_key)], tags=["numbers"])
def blacklist_number(number_id: int, payload: BlacklistRequest, request: Request) -> dict:
    repository = _repo(request)
    if not repository.get_number_detail(number_id):
        raise HTTPException(status_code=404, detail="number not found")
    repository.set_number_blacklist(number_id, payload.reason)
    return {"status": "ok", "number_id": number_id, "blacklist_reason": payload.reason}


@router.post("/numbers/{number_id}/blacklist/clear", dependencies=[Depends(require_api_key)], tags=["numbers"])
def clear_number_blacklist(number_id: int, request: Request) -> dict:
    repository = _repo(request)
    if not repository.get_number_detail(number_id):
        raise HTTPException(status_code=404, detail="number not found")
    repository.clear_number_blacklist(number_id)
    return {"status": "ok", "number_id": number_id}


@router.post("/numbers/{number_id}/app-state", dependencies=[Depends(require_api_key)], tags=["numbers"])
def set_app_state(number_id: int, payload: AppStateRequest, request: Request) -> dict:
    repository = _repo(request)
    if not repository.get_number_detail(number_id):
        raise HTTPException(status_code=404, detail="number not found")
    try:
        repository.set_app_number_state(number_id, payload.app_slug, payload.app_name, payload.status, payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "number_id": number_id, "app_slug": payload.app_slug, "app_state": payload.status}


@router.get("/claims", dependencies=[Depends(require_api_key)], tags=["claims"])
def list_claims(
    request: Request,
    active_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    repository = _repo(request)
    return [_to_dict(item) for item in repository.list_claims(active_only=active_only, limit=limit)]


@router.post("/claims", dependencies=[Depends(require_api_key)], status_code=status.HTTP_201_CREATED, tags=["claims"])
def create_claim(payload: ClaimCreateRequest, request: Request) -> dict:
    repository = _repo(request)
    try:
        claim = repository.create_claim(
            app_slug=payload.app_slug,
            app_name=payload.app_name,
            country_name=payload.country_name,
            provider_id=payload.provider_id,
            purpose=payload.purpose,
            include_cooling=payload.include_cooling,
            ttl_minutes=payload.ttl_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_dict(claim)


@router.get("/claims/{claim_token}", dependencies=[Depends(require_api_key)], tags=["claims"])
def get_claim(claim_token: str, request: Request) -> dict:
    repository = _repo(request)
    claim = repository.get_claim(claim_token)
    if not claim:
        raise HTTPException(status_code=404, detail="claim not found")
    payload = _to_dict(claim)
    payload["messages"] = [_to_dict(item) for item in repository.list_claim_messages(claim_token)]
    return payload


@router.get("/claims/{claim_token}/messages", dependencies=[Depends(require_api_key)], tags=["claims"])
def get_claim_messages(claim_token: str, request: Request, limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    repository = _repo(request)
    claim = repository.get_claim(claim_token)
    if not claim:
        raise HTTPException(status_code=404, detail="claim not found")
    return [_to_dict(item) for item in repository.list_claim_messages(claim_token, limit=limit)]


@router.post("/claims/{claim_token}/release", dependencies=[Depends(require_api_key)], tags=["claims"])
def release_claim(claim_token: str, payload: ClaimTransitionRequest, request: Request) -> dict:
    repository = _repo(request)
    claim = repository.transition_claim(
        claim_token,
        status="released",
        result=payload.result,
        app_state=payload.app_state or "available",
    )
    if not claim:
        raise HTTPException(status_code=404, detail="claim not found")
    return _to_dict(claim)


@router.post("/claims/{claim_token}/complete", dependencies=[Depends(require_api_key)], tags=["claims"])
def complete_claim(claim_token: str, payload: ClaimTransitionRequest, request: Request) -> dict:
    repository = _repo(request)
    claim = repository.transition_claim(
        claim_token,
        status="completed",
        result=payload.result,
        app_state=payload.app_state or "success",
    )
    if not claim:
        raise HTTPException(status_code=404, detail="claim not found")
    return _to_dict(claim)


@router.post("/claims/{claim_token}/block", dependencies=[Depends(require_api_key)], tags=["claims"])
def block_claim(claim_token: str, payload: ClaimTransitionRequest, request: Request) -> dict:
    repository = _repo(request)
    claim = repository.transition_claim(
        claim_token,
        status="completed",
        result=payload.result,
        app_state=payload.app_state or "blocked",
    )
    if not claim:
        raise HTTPException(status_code=404, detail="claim not found")
    return _to_dict(claim)
