from __future__ import annotations

import sys
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from loguru import logger

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        backtrace=False,
        diagnose=False,
        serialize=settings.log_json,
        enqueue=False,
    )
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        settings.log_path,
        level=settings.log_level,
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        backtrace=False,
        diagnose=False,
        serialize=settings.log_json,
        enqueue=False,
    )


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_request(request: Request, call_next):  # type: ignore[override]
        request_id = uuid4().hex[:10]
        started = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.bind(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                query=str(request.url.query),
                status_code=getattr(response, "status_code", 500),
                duration_ms=round(duration_ms, 2),
            ).info("http_request")
