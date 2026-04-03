from __future__ import annotations

import base64
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.config import Settings

_PUBLIC_PREFIXES = ("/api", "/static")
_PUBLIC_PATHS = {"/healthz", "/docs", "/openapi.json", "/redoc"}


def _decode_basic_token(token: str) -> tuple[str, str] | None:
    try:
        decoded = base64.b64decode(token.encode("ascii"), validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


def install_web_ui_auth(app: FastAPI, settings: Settings) -> None:
    if not settings.web_ui_username or not settings.web_ui_password:
        return

    @app.middleware("http")
    async def require_web_ui_basic_auth(request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path in _PUBLIC_PATHS or any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        scheme, _, value = auth_header.partition(" ")
        if scheme.lower() != "basic" or not value:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Free SMS Tool"'},
            )

        credentials = _decode_basic_token(value.strip())
        if not credentials:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Free SMS Tool"'},
            )
        username, password = credentials
        if not (
            secrets.compare_digest(username, settings.web_ui_username)
            and secrets.compare_digest(password, settings.web_ui_password)
        ):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Free SMS Tool"'},
            )
        return await call_next(request)
