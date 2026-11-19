"""Versioned HTTP API (``/v1``). See docs/API.md and ``/docs`` for the OpenAPI contract."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from geodiff import ratelimit, store
from geodiff.auth import Workspace, current_workspace, hash_token, new_token
from geodiff.config import get_settings
from geodiff.db import get_pool
from geodiff.errors import ApiError

router = APIRouter(prefix="/v1")
Auth = Annotated[Workspace, Depends(current_workspace)]

Kind = Literal["addition", "deletion", "move", "attribute"]
Disposition = Literal["pending", "accepted", "rejected", "flagged"]
ACCEPTED_TYPES = {
    "application/geo+json",
    "application/json",
    "application/octet-stream",
    "text/plain",
}


# --------------------------------------------------------------------------- models


class WorkspaceCreated(BaseModel):
    workspace_id: str
    token: str = Field(description="Bearer credential. Shown once; store it client-side.")
    created_at: str
    expires_at: str


class ComparisonRequest(BaseModel):
    baseline_dataset_id: uuid.UUID
    proposed_dataset_id: uuid.UUID
    baseline_id_field: str = Field(min_length=1, max_length=200)
    proposed_id_field: str | None = Field(default=None, max_length=200)
    compared_fields: list[str] = Field(default_factory=list, max_length=100)
    tolerance_m: float = Field(default=2.0, ge=0, le=100_000)
    parent_comparison_id: uuid.UUID | None = Field(
        default=None, description="Previous run this one replaces (lineage); it is retained."
    )


class DecisionItem(BaseModel):
    component_id: str = Field(max_length=400)
    disposition: Disposition
    note: str | None = Field(default=None, max_length=2000)
    expected_version: int = Field(
        ge=0, description="Version the client last saw; a mismatch returns 409 stale_decision."
    )


class DecisionRequest(BaseModel):
    decisions: list[DecisionItem] = Field(min_length=1, max_length=200)


class BulkScope(BaseModel):
    kind: Kind | None = None
    field: str | None = None
    current_disposition: Disposition | None = None
    asset_ids: list[str] | None = Field(default=None, max_length=5000)
    max_distance_m: float | None = Field(default=None, ge=0)


class BulkRequest(BaseModel):
    scope: BulkScope
    disposition: Disposition
    note: str | None = Field(default=None, max_length=2000)
    expected_count: int = Field(ge=0, description="Count shown to the reviewer from /bulk/preview.")


class UndoRequest(BaseModel):
    expected_revision: int = Field(ge=0)


# --------------------------------------------------------------------------- helpers


def _bbox(raw: str) -> tuple[float, float, float, float]:
    try:
        x0, y0, x1, y1 = (float(v) for v in raw.split(","))
    except ValueError as exc:
        raise ApiError(
            422,
            "invalid_bbox",
            "bbox must be minLon,minLat,maxLon,maxLat.",
            "Example: -96.82,32.77,-96.77,32.80",
        ) from exc
    if not (-180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90):
        raise ApiError(
            422,
            "invalid_bbox",
            "bbox is outside WGS84 bounds or inverted.",
            "Use minLon,minLat,maxLon,maxLat in degrees.",
        )
    return x0, y0, x1, y1


def _write_guard(request: Request) -> None:
    ratelimit.writes.check(ratelimit.client_key(request))


def _safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", name.strip())[:120]
    return base or "upload.geojson"


async def read_bounded_body(request: Request, limit: int) -> bytes:
    """Read the request body, refusing anything over ``limit`` before buffering it all."""
    declared = request.headers.get("content-length")
    too_big = ApiError(
        413,
        "upload_too_large",
        f"Uploads are limited to {limit // (1024 * 1024)} MB.",
        "Split the inventory, or simplify properties before export.",
    )
    if declared and declared.isdigit() and int(declared) > limit:
        raise too_big
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise too_big
        chunks.append(chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------------- workspaces


@router.post("/workspaces", status_code=201, response_model=WorkspaceCreated, tags=["workspace"])
def create_workspace(request: Request) -> dict[str, Any]:
    """Create an anonymous, isolated workspace. The token is returned once."""
    ratelimit.workspace_creation.check(ratelimit.client_key(request))
    token = new_token()
    with get_pool().connection() as conn:
        out = store.create_workspace(conn, hash_token(token))
    return out | {"token": token}


@router.get("/workspace", tags=["workspace"])
def get_workspace(ws: Auth) -> dict[str, Any]:
    with get_pool().connection() as conn:
        return store.workspace_info(conn, ws.id)


@router.delete("/workspace", status_code=204, tags=["workspace"])
def delete_workspace(ws: Auth, request: Request) -> Response:
    """Permanently delete this workspace and all of its uploads and reviews."""
    _write_guard(request)
    with get_pool().connection() as conn:
        store.delete_workspace(conn, ws.id)
    return Response(status_code=204)


@router.post("/workspace/reset", tags=["workspace"])
def reset_workspace(ws: Auth, request: Request) -> dict[str, Any]:
    """Delete this workspace's datasets and comparisons but keep the credential."""
    _write_guard(request)
    with get_pool().connection() as conn:
        store.reset_workspace(conn, ws.id)
        return store.workspace_info(conn, ws.id)


# --------------------------------------------------------------------------- datasets


@router.post("/datasets", tags=["datasets"])
async def import_dataset(
    ws: Auth,
    request: Request,
    response: Response,
    name: Annotated[str, Query(max_length=200)] = "upload.geojson",
) -> dict[str, Any]:
    """Upload a GeoJSON FeatureCollection as the raw request body.

    The original bytes and SHA-256 are retained privately. Invalid files are stored with their
    validation summary so problems can be reviewed; they cannot be compared. Uploading the same
    bytes again returns the existing dataset (``duplicate: true``).
    """
    _write_guard(request)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype and ctype not in ACCEPTED_TYPES:
        raise ApiError(
            415,
            "unsupported_media_type",
            f"Content type {ctype!r} is not accepted.",
            "Upload a .geojson file (application/geo+json).",
        )
    data = await read_bounded_body(request, get_settings().max_upload_bytes)
    if not data:
        raise ApiError(422, "empty_upload", "The upload is empty.", "Choose a GeoJSON file.")

    def work() -> tuple[dict[str, Any], bool]:
        with get_pool().connection() as conn:
            return store.import_dataset(conn, ws.id, _safe_filename(name), data)

    dataset, duplicate = await run_in_threadpool(work)
    response.status_code = 200 if duplicate else 201
    return dataset | {"duplicate": duplicate}


@router.get("/datasets", tags=["datasets"])
def list_datasets(ws: Auth) -> dict[str, Any]:
    with get_pool().connection() as conn:
        return {"datasets": store.list_datasets(conn, ws.id)}


@router.get("/datasets/suggest-fields", tags=["datasets"])
def suggest_fields(
    ws: Auth,
    baseline: uuid.UUID,
    proposed: uuid.UUID,
    baseline_id_field: str,
    proposed_id_field: str | None = None,
) -> dict[str, Any]:
    """Fields the engine suggests comparing (scalar, in both versions, not IDs or importer
    timestamps), with a reason for every field that is not suggested."""
    with get_pool().connection() as conn:
        return store.suggest_fields(
            conn,
            ws.id,
            baseline,
            proposed,
            {baseline_id_field, proposed_id_field or baseline_id_field},
        )


@router.get("/datasets/{dataset_id}", tags=["datasets"])
def get_dataset(ws: Auth, dataset_id: uuid.UUID) -> dict[str, Any]:
    """Summary: feature count, fields and coverage, candidate ID fields, sampled validation issues."""
    with get_pool().connection() as conn:
        return store.get_dataset(conn, ws.id, dataset_id)


@router.get("/datasets/{dataset_id}/original", tags=["datasets"])
def get_original(ws: Auth, dataset_id: uuid.UUID) -> Response:
    """The exact bytes that were uploaded (only to the owning workspace)."""
    with get_pool().connection() as conn:
        name, data = store.get_original(conn, ws.id, dataset_id)
    return Response(
        data,
        media_type="application/geo+json",
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(name)}"',
            "X-Content-SHA256": hashlib.sha256(data).hexdigest(),
        },
    )


# --------------------------------------------------------------------------- comparisons


@router.post("/comparisons", status_code=201, tags=["comparisons"])
def create_comparison(ws: Auth, body: ComparisonRequest, request: Request) -> dict[str, Any]:
    """Run the comparison engine. A changed tolerance or field set is a new run; pass
    ``parent_comparison_id`` to record lineage. Earlier runs and their decisions are kept."""
    _write_guard(request)
    with get_pool().connection() as conn:
        return store.create_comparison(conn, ws.id, body.model_dump())


@router.get("/comparisons", tags=["comparisons"])
def list_comparisons(ws: Auth) -> dict[str, Any]:
    with get_pool().connection() as conn:
        return {"comparisons": store.list_comparisons(conn, ws.id)}


@router.get("/comparisons/{cid}", tags=["comparisons"])
def get_comparison(ws: Auth, cid: uuid.UUID, response: Response) -> dict[str, Any]:
    with get_pool().connection() as conn:
        out = store.get_comparison(conn, ws.id, cid)
    response.headers["ETag"] = f'"{out["revision"]}"'
    return out


@router.get("/comparisons/{cid}/queue", tags=["review"])
def get_queue(
    ws: Auth,
    cid: uuid.UUID,
    kind: Kind | None = None,
    disposition: Disposition | Literal["unresolved"] | None = None,
    status: Literal["added", "deleted", "modified"] | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
    limit: Annotated[int, Query(ge=1)] = 50,
) -> dict[str, Any]:
    """Assets with grouped components, keyset-paginated by asset ID (``next_cursor``)."""
    with get_pool().connection() as conn:
        return store.queue(
            conn,
            ws.id,
            cid,
            kind=kind,
            disposition=disposition,
            status=status,
            q=q,
            cursor=cursor,
            limit=limit,
        )


@router.get("/comparisons/{cid}/map", tags=["review"])
def get_map(
    ws: Auth, cid: uuid.UUID, bbox: str, limit: Annotated[int, Query(ge=1)] = 1000
) -> dict[str, Any]:
    """Changed assets whose old or new position falls in ``bbox`` (spatially indexed)."""
    with get_pool().connection() as conn:
        return store.map_features(conn, ws.id, cid, _bbox(bbox), limit)


@router.get("/comparisons/{cid}/baseline", tags=["review"])
def get_baseline_features(
    ws: Auth, cid: uuid.UUID, bbox: str, limit: Annotated[int, Query(ge=1)] = 1000
) -> dict[str, Any]:
    """Baseline asset positions in ``bbox`` for map context (unchanged assets included)."""
    with get_pool().connection() as conn:
        return store.baseline_features(conn, ws.id, cid, _bbox(bbox), limit)


@router.get("/comparisons/{cid}/assets/{asset_id}", tags=["review"])
def get_asset(ws: Auth, cid: uuid.UUID, asset_id: str) -> dict[str, Any]:
    """Evidence for one asset: old/new values, components, decisions and history."""
    with get_pool().connection() as conn:
        return store.asset_detail(conn, ws.id, cid, asset_id)


@router.post("/comparisons/{cid}/decisions", tags=["review"])
def post_decisions(
    ws: Auth, cid: uuid.UUID, body: DecisionRequest, request: Request
) -> dict[str, Any]:
    """Record decisions atomically. Each item carries ``expected_version``; if another tab
    changed that component first, nothing is applied and 409 ``stale_decision`` is returned."""
    _write_guard(request)
    with get_pool().connection() as conn:
        return store.decide(conn, ws.id, cid, [d.model_dump() for d in body.decisions])


@router.post("/comparisons/{cid}/bulk/preview", tags=["review"])
def bulk_preview(ws: Auth, cid: uuid.UUID, scope: BulkScope) -> dict[str, Any]:
    """Count the components a bulk decision would change, so the UI can state scope and count."""
    with get_pool().connection() as conn:
        return store.preview_bulk(conn, ws.id, cid, scope.model_dump(exclude_none=True))


@router.post("/comparisons/{cid}/bulk", tags=["review"])
def bulk_decide(ws: Auth, cid: uuid.UUID, body: BulkRequest, request: Request) -> dict[str, Any]:
    """Apply one disposition to every component in ``scope``. ``expected_count`` must equal the
    current match count (409 ``bulk_count_mismatch`` otherwise)."""
    _write_guard(request)
    req = body.model_dump()
    req["scope"] = body.scope.model_dump(exclude_none=True)
    with get_pool().connection() as conn:
        return store.bulk_decide(conn, ws.id, cid, req)


@router.post("/comparisons/{cid}/undo", tags=["review"])
def post_undo(ws: Auth, cid: uuid.UUID, body: UndoRequest, request: Request) -> dict[str, Any]:
    """Revert the most recent decision batch (recorded as an audit event)."""
    _write_guard(request)
    with get_pool().connection() as conn:
        return store.undo(conn, ws.id, cid, body.expected_revision)


@router.get("/comparisons/{cid}/history", tags=["review"])
def get_history(
    ws: Auth,
    cid: uuid.UUID,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1)] = 50,
) -> dict[str, Any]:
    with get_pool().connection() as conn:
        return store.history(conn, ws.id, cid, before, limit)


@router.get("/comparisons/{cid}/export", tags=["export"])
def get_export(
    ws: Auth,
    cid: uuid.UUID,
    mode: Literal["draft", "final"] = "draft",
    part: Literal["geojson", "report", "bundle"] = "bundle",
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Reviewed inventory (``part=geojson``), its report (``part=report``) or both (``bundle``).

    ``mode=final`` returns 409 ``unresolved_components`` until every component is accepted or
    rejected. Drafts are always available and list unresolved components in the report.
    """
    with get_pool().connection() as conn:
        fc, report = store.export(conn, ws.id, cid, mode)
    body_geojson = json.dumps(fc, ensure_ascii=False)
    report["output_sha256"] = hashlib.sha256(body_geojson.encode()).hexdigest()
    stem = f"geodiff-{mode}-{str(cid)[:8]}-r{report['revision']}"
    if part == "geojson":
        return Response(
            body_geojson,
            media_type="application/geo+json",
            headers={"Content-Disposition": f'attachment; filename="{stem}.geojson"'},
        )
    if part == "report":
        return Response(
            json.dumps(report, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{stem}.report.json"'},
        )
    return Response(
        json.dumps({"geojson": fc, "report": report}, ensure_ascii=False),
        media_type="application/json",
    )
