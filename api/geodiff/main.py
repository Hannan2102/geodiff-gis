"""FastAPI application entry point."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from geodiff import __version__
from geodiff.config import get_settings
from geodiff.db import close_pool, get_pool
from geodiff.migrate import migration_files
from geodiff.routes import router

log = logging.getLogger("geodiff.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    get_pool()  # opens in the background; readiness reports when it is usable
    yield
    close_pool()


app = FastAPI(
    title="GeoDiff API",
    version=__version__,
    description="Review point-asset changes between an approved baseline and a field inventory.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-Match", "X-Request-ID"],
    expose_headers=["X-Request-ID", "ETag"],
    max_age=600,
)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach a request ID and log aggregate timing. Never logs bodies or credentials."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error rid=%s path=%s", rid, request.url.path)
        response = JSONResponse(
            status_code=500,
            content=error_body("internal_error", "Unexpected server error.", rid),
        )
    ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    log.info(
        "rid=%s method=%s route=%s status=%s ms=%.1f",
        rid,
        request.method,
        request.url.path,
        response.status_code,
        ms,
    )
    return response


def error_body(code: str, message: str, rid: str | None, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": rid, **extra}}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        body = {"error": {**detail, "request_id": getattr(request.state, "request_id", None)}}
    else:
        body = error_body("http_error", str(detail), getattr(request.state, "request_id", None))
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body(
            "invalid_request",
            "The request did not match the API schema.",
            getattr(request.state, "request_id", None),
            details=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()[:20]],
        ),
    )


@app.get("/health", tags=["ops"], summary="Liveness: the process is serving requests")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "git_sha": get_settings().git_sha}


@app.get("/ready", tags=["ops"], summary="Readiness: database, PostGIS and migrations")
def ready(response: Response) -> dict[str, Any]:
    expected = [p.stem for p in migration_files()]
    try:
        with get_pool().connection(timeout=get_settings().db_connect_timeout_s) as conn:
            row = conn.execute(
                "SELECT version() AS pg, postgis_lib_version() AS postgis"
            ).fetchone()
            applied = [
                r["version"]
                for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
            ]
    except Exception as exc:
        response.status_code = 503
        log.warning("readiness check failed: %s", type(exc).__name__)
        return {
            "status": "unavailable",
            "database": False,
            "postgis": None,
            "message": "The API is running but cannot reach PostGIS yet.",
        }
    pending = [v for v in expected if v not in applied]
    if pending:
        response.status_code = 503
    assert row is not None
    return {
        "status": "ready" if not pending else "migrations_pending",
        "database": True,
        "postgres": row["pg"].split(" on ")[0],
        "postgis": row["postgis"],
        "migrations": {"applied": applied, "pending": pending},
        "version": __version__,
        "git_sha": get_settings().git_sha,
    }


app.include_router(router)
