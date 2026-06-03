"""Application entry: FastAPI app wiring (spec — Interface layer root)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from loguru import logger

from api.error_handlers import request_validation_exception_handler
from api.routes import admin, assets, auth, programs, scans, tasks
from core.auth_settings import bootstrap_superuser_enabled, get_auth_settings
from core.database import AsyncSessionLocal
from services.auth_service import ensure_bootstrap_superuser


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown hooks (extend for DB health checks)."""
    auth_cfg = get_auth_settings()
    if auth_cfg.jwt_secret_key == "dev-only-change-with-JWT_SECRET_KEY":
        logger.warning(
            "JWT_SECRET_KEY is using the default dev value — set a strong secret in production",
        )
    if bootstrap_superuser_enabled(auth_cfg):
        async with AsyncSessionLocal() as session:
            try:
                await ensure_bootstrap_superuser(
                    session,
                    (auth_cfg.superuser_email or "").strip(),
                    auth_cfg.superuser_password or "",
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Bootstrap superuser setup failed")
    logger.info("yonnn API starting")
    yield
    logger.info("yonnn API shutting down")


app = FastAPI(
    title="yonnn",
    description="Unified bug bounty / ASM framework API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

_cors = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:8080,http://127.0.0.1:8080",
)
_cors_origins = [o.strip() for o in _cors.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All JSON API routes under /api so the SPA can own /programs, /dashboard, etc. on the same host.
_API_PREFIX = "/api"
app.include_router(auth.router, prefix=_API_PREFIX)
app.include_router(admin.router, prefix=_API_PREFIX)
app.include_router(programs.router, prefix=_API_PREFIX)
app.include_router(assets.router, prefix=_API_PREFIX)
app.include_router(scans.router, prefix=_API_PREFIX)
app.include_router(tasks.router, prefix=_API_PREFIX)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for orchestrators."""
    return {"status": "ok"}
