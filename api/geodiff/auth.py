"""Anonymous workspaces with unguessable bearer credentials.

A workspace token is 256 bits from ``secrets``; only its SHA-256 is stored. Tokens travel in
the ``Authorization: Bearer`` header, never in URLs. Every query in :mod:`geodiff.store`
filters by the authenticated workspace ID, and resources from other workspaces return 404.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from geodiff.config import get_settings
from geodiff.db import get_pool
from geodiff.errors import ApiError

TOKEN_PREFIX = "gdw_"  # noqa: S105 - public prefix, not a secret


@dataclass(frozen=True)
class Workspace:
    id: uuid.UUID
    expires_at: datetime


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _unauthorized(message: str, code: str = "unauthorized") -> ApiError:
    return ApiError(
        401,
        code,
        message,
        "Create a workspace with POST /v1/workspaces and send its token as "
        "'Authorization: Bearer <token>'.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_workspace(request: Request) -> Workspace:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX):
        raise _unauthorized("A workspace token is required.")
    ttl = get_settings().workspace_ttl_hours
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            UPDATE workspaces
               SET last_seen_at = now(),
                   expires_at = now() + make_interval(hours => %s)
             WHERE token_hash = %s AND expires_at > now()
         RETURNING id, expires_at
            """,
            (ttl, hash_token(token.strip())),
        ).fetchone()
    if row is None:
        raise _unauthorized(
            "This workspace token is unknown or the workspace has expired.", "workspace_expired"
        )
    return Workspace(row["id"], row["expires_at"])
