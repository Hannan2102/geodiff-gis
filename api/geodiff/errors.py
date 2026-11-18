"""Consistent API errors: ``{"error": {"code", "message", "guidance", ...}}``."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        guidance: str = "",
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        detail = {"code": code, "message": message, "guidance": guidance, **extra}
        super().__init__(status_code=status, detail=detail, headers=headers)


def not_found(what: str) -> ApiError:
    return ApiError(
        404,
        "not_found",
        f"{what} was not found in this workspace.",
        "Check the ID. Resources are only visible to the workspace that created them.",
    )
