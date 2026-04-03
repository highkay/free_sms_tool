from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db.repository import Repository

bearer_scheme = HTTPBearer(auto_error=False)


def get_repository(request: Request) -> Repository:
    return request.app.state.repository  # type: ignore[no-any-return]


def require_api_key(
    repository: Repository = Depends(get_repository),
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    token = credentials.credentials if credentials else (x_api_key or "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing api key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    identity = repository.authenticate_api_key(token)
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity
