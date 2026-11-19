"""Persistence for workspaces, datasets, snapshots, comparisons and review state.

Every public function takes the authenticated workspace ID and filters by it, so a resource
from another workspace is indistinguishable from a missing one (404).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from geodiff.config import get_settings
from geodiff.engine.compare import (
    CompareOptions,
    ComparisonError,
    compare_datasets,
    suggested_compare_fields,
)
from geodiff.engine.geojson import ParsedDataset, parse_geojson
from geodiff.engine.ids import index_ids
from geodiff.engine.resolve import DISPOSITIONS, FinalExportBlocked, resolve
from geodiff.errors import ApiError, not_found

Conn = Connection[DictRow]


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _ewkt(coords: list[float]) -> str:
    return f"SRID=4326;POINT({coords[0]!r} {coords[1]!r})"


# --------------------------------------------------------------------------- workspaces


def purge_expired(conn: Conn) -> int:
    cur = conn.execute("DELETE FROM workspaces WHERE expires_at <= now()")
    return cur.rowcount


def create_workspace(conn: Conn, token_hash: bytes) -> dict[str, Any]:
    s = get_settings()
    purge_expired(conn)
    active = conn.execute("SELECT count(*) AS n FROM workspaces").fetchone()
    if active and active["n"] >= s.max_active_workspaces:
        raise ApiError(
            503,
            "capacity_reached",
            "The public demo has reached its workspace limit.",
            "Use the guided sample (no workspace needed), or try again later. Workspaces "
            f"expire {s.workspace_ttl_hours} hours after last use.",
        )
    row = conn.execute(
        """
        INSERT INTO workspaces (id, token_hash, expires_at)
        VALUES (%s, %s, now() + make_interval(hours => %s))
        RETURNING id, created_at, expires_at
        """,
        (uuid.uuid4(), token_hash, s.workspace_ttl_hours),
    ).fetchone()
    assert row is not None
    return {
        "workspace_id": str(row["id"]),
        "created_at": _iso(row["created_at"]),
        "expires_at": _iso(row["expires_at"]),
    }


def workspace_info(conn: Conn, wid: uuid.UUID) -> dict[str, Any]:
    s = get_settings()
    row = conn.execute(
        """
        SELECT w.id, w.created_at, w.expires_at,
               (SELECT count(*) FROM datasets d WHERE d.workspace_id = w.id) AS datasets,
               (SELECT count(*) FROM comparisons c WHERE c.workspace_id = w.id) AS comparisons
          FROM workspaces w WHERE w.id = %s
        """,
        (wid,),
    ).fetchone()
    if row is None:
        raise not_found("Workspace")
    return {
        "workspace_id": str(row["id"]),
        "created_at": _iso(row["created_at"]),
        "expires_at": _iso(row["expires_at"]),
        "datasets": row["datasets"],
        "comparisons": row["comparisons"],
        "limits": {
            "max_upload_bytes": s.max_upload_bytes,
            "max_features": s.max_features,
            "max_datasets": s.max_datasets_per_workspace,
            "max_comparisons": s.max_comparisons_per_workspace,
            "ttl_hours": s.workspace_ttl_hours,
            "page_max": s.page_max,
        },
    }


def delete_workspace(conn: Conn, wid: uuid.UUID) -> None:
    conn.execute("DELETE FROM workspaces WHERE id = %s", (wid,))


def reset_workspace(conn: Conn, wid: uuid.UUID) -> None:
    conn.execute("DELETE FROM comparisons WHERE workspace_id = %s", (wid,))
    conn.execute("DELETE FROM datasets WHERE workspace_id = %s", (wid,))


# --------------------------------------------------------------------------- datasets


def _dataset_out(row: DictRow) -> dict[str, Any]:
    return {
        "dataset_id": str(row["id"]),
        "name": row["name"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "feature_count": row["feature_count"],
        "valid": row["valid"],
        "created_at": _iso(row["created_at"]),
        "source": row["source"],
        "summary": row["summary"],
    }


DATASET_COLS = "id, name, sha256, size_bytes, feature_count, valid, summary, source, created_at"


def import_dataset(
    conn: Conn, wid: uuid.UUID, name: str, data: bytes, source: dict[str, Any] | None = None
) -> tuple[dict[str, Any], bool]:
    """Store an upload. Returns ``(dataset, duplicate)``; identical bytes are deduplicated."""
    s = get_settings()
    parsed = parse_geojson(data, name=name, max_features=s.max_features)
    summary = parsed.summary()
    with conn.transaction():
        conn.execute("SELECT id FROM workspaces WHERE id = %s FOR UPDATE", (wid,))
        existing = conn.execute(
            f"SELECT {DATASET_COLS} FROM datasets WHERE workspace_id = %s AND sha256 = %s",  # noqa: S608
            (wid, parsed.sha256),
        ).fetchone()
        if existing:
            return _dataset_out(existing), True
        n = conn.execute(
            "SELECT count(*) AS n FROM datasets WHERE workspace_id = %s", (wid,)
        ).fetchone()
        if n and n["n"] >= s.max_datasets_per_workspace:
            raise ApiError(
                409,
                "dataset_limit",
                f"This workspace already holds {n['n']} datasets.",
                "Reset the workspace or delete older comparisons and datasets.",
            )
        row = conn.execute(
            f"""
            INSERT INTO datasets (id, workspace_id, name, sha256, size_bytes, feature_count,
                                  valid, summary, original, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {DATASET_COLS}
            """,  # noqa: S608
            (
                uuid.uuid4(),
                wid,
                name[:200],
                parsed.sha256,
                parsed.size_bytes,
                parsed.input_count,
                parsed.valid,
                Jsonb(summary),
                data,
                Jsonb(source) if source else None,
            ),
        ).fetchone()
    assert row is not None
    return _dataset_out(row), False


def list_datasets(conn: Conn, wid: uuid.UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {DATASET_COLS} FROM datasets WHERE workspace_id = %s ORDER BY created_at",  # noqa: S608
        (wid,),
    ).fetchall()
    out = []
    for r in rows:
        d = _dataset_out(r)
        d["summary"] = {k: v for k, v in d["summary"].items() if k != "validation"} | {
            "validation": {k: v for k, v in r["summary"]["validation"].items() if k != "sample"}
        }
        out.append(d)
    return out


def get_dataset(conn: Conn, wid: uuid.UUID, dataset_id: uuid.UUID) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT {DATASET_COLS} FROM datasets WHERE id = %s AND workspace_id = %s",  # noqa: S608
        (dataset_id, wid),
    ).fetchone()
    if row is None:
        raise not_found("Dataset")
    return _dataset_out(row)


def get_original(conn: Conn, wid: uuid.UUID, dataset_id: uuid.UUID) -> tuple[str, bytes]:
    row = conn.execute(
        "SELECT name, original FROM datasets WHERE id = %s AND workspace_id = %s", (dataset_id, wid)
    ).fetchone()
    if row is None:
        raise not_found("Dataset")
    return row["name"], bytes(row["original"])


def suggest_fields(
    conn: Conn,
    wid: uuid.UUID,
    baseline_id: uuid.UUID,
    proposed_id: uuid.UUID,
    id_fields: set[str],
) -> dict[str, Any]:
    """Engine-suggested comparable fields, plus every field with its comparability."""
    _, base = _load_parsed(conn, wid, baseline_id)
    _, prop = _load_parsed(conn, wid, proposed_id)
    suggested = suggested_compare_fields(base, prop, id_fields)
    fields = []
    for name in sorted(set(base.fields) | set(prop.fields)):
        b, p = base.fields.get(name), prop.fields.get(name)
        bd = b.to_dict(base.input_count) if b else None
        pd = p.to_dict(prop.input_count) if p else None
        scalar = (bd is None or bd["scalar"]) and (pd is None or pd["scalar"])
        reason = None
        if name in id_fields:
            reason = "ID field"
        elif not scalar:
            reason = "nested values (preserved, not compared)"
        elif bd is None or pd is None:
            reason = "only in one version"
        elif name not in suggested:
            reason = "importer/editor bookkeeping"
        fields.append(
            {
                "name": name,
                "baseline_coverage": bd["coverage"] if bd else 0.0,
                "proposed_coverage": pd["coverage"] if pd else 0.0,
                "comparable": scalar and name not in id_fields,
                "suggested": name in suggested,
                "note": reason,
            }
        )
    return {"suggested": suggested, "fields": fields}


def _load_parsed(
    conn: Conn, wid: uuid.UUID, dataset_id: uuid.UUID
) -> tuple[DictRow, ParsedDataset]:
    row = conn.execute(
        "SELECT id, name, valid, original, source FROM datasets WHERE id = %s AND workspace_id = %s",
        (dataset_id, wid),
    ).fetchone()
    if row is None:
        raise not_found("Dataset")
    if not row["valid"]:
        raise ApiError(
            422,
            "dataset_invalid",
            f"Dataset {row['name']!r} has validation errors and cannot be compared.",
            "Fix the problems listed in the dataset summary, then upload the file again.",
        )
    parsed = parse_geojson(
        bytes(row["original"]), name=row["name"], max_features=get_settings().max_features
    )
    return row, parsed


def _snapshot(conn: Conn, dataset_id: uuid.UUID, parsed: ParsedDataset, id_field: str) -> uuid.UUID:
    """Get or create the immutable (dataset, id_field) snapshot with its features."""
    existing = conn.execute(
        "SELECT id FROM snapshots WHERE dataset_id = %s AND id_field = %s", (dataset_id, id_field)
    ).fetchone()
    if existing:
        return uuid.UUID(str(existing["id"]))
    idx, log = index_ids(parsed.features, id_field)
    if log.has_errors:
        raise ApiError(
            422,
            "invalid_ids",
            f"The ID field {id_field!r} in {parsed.name!r} has invalid values.",
            "Choose another ID field, or fix null, empty, duplicate or ambiguous IDs.",
            validation=log.to_dict(),
        )
    sid = uuid.uuid4()
    row = conn.execute(
        """
        INSERT INTO snapshots (id, dataset_id, id_field, feature_count) VALUES (%s, %s, %s, %s)
        ON CONFLICT (dataset_id, id_field) DO NOTHING RETURNING id
        """,
        (sid, dataset_id, id_field, len(idx)),
    ).fetchone()
    if row is None:  # created concurrently and committed by another transaction
        again = conn.execute(
            "SELECT id FROM snapshots WHERE dataset_id = %s AND id_field = %s",
            (dataset_id, id_field),
        ).fetchone()
        assert again is not None
        return uuid.UUID(str(again["id"]))
    with conn.cursor().copy(
        "COPY features (snapshot_id, asset_id, ordinal, geom, feature) FROM STDIN"
    ) as copy:
        for aid, i in idx.items():
            f = parsed.features[i]
            copy.write_row((sid, aid, i, _ewkt(f["geometry"]["coordinates"]), json.dumps(f)))
    return sid


# --------------------------------------------------------------------------- comparisons


def create_comparison(conn: Conn, wid: uuid.UUID, req: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    with conn.transaction():
        conn.execute("SELECT id FROM workspaces WHERE id = %s FOR UPDATE", (wid,))
        n = conn.execute(
            "SELECT count(*) AS n FROM comparisons WHERE workspace_id = %s", (wid,)
        ).fetchone()
        if n and n["n"] >= s.max_comparisons_per_workspace:
            raise ApiError(
                409,
                "comparison_limit",
                f"This workspace already holds {n['n']} comparison runs.",
                "Reset the workspace to start over.",
            )
        parent = req.get("parent_comparison_id")
        if parent:
            p = conn.execute(
                "SELECT 1 FROM comparisons WHERE id = %s AND workspace_id = %s", (parent, wid)
            ).fetchone()
            if p is None:
                raise not_found("Parent comparison")
        b_row, base = _load_parsed(conn, wid, req["baseline_dataset_id"])
        p_row, prop = _load_parsed(conn, wid, req["proposed_dataset_id"])
        bid_field = req["baseline_id_field"]
        pid_field = req.get("proposed_id_field") or bid_field
        opts = CompareOptions(
            bid_field, pid_field, tuple(req["compared_fields"]), float(req["tolerance_m"])
        )
        try:
            result = compare_datasets(base, prop, opts)
        except ComparisonError as exc:
            raise ApiError(
                422,
                "comparison_invalid",
                str(exc),
                "Review the listed problems; choose different ID or comparison fields.",
                validation=exc.issues.to_dict(),
            ) from exc
        bsid = _snapshot(conn, b_row["id"], base, bid_field)
        psid = _snapshot(conn, p_row["id"], prop, pid_field)
        cid = uuid.uuid4()
        inputs = result["inputs"]
        inputs["baseline"]["dataset_id"] = str(b_row["id"])
        inputs["proposed"]["dataset_id"] = str(p_row["id"])
        if b_row["source"]:
            inputs["baseline"]["source"] = b_row["source"]
        if p_row["source"]:
            inputs["proposed"]["source"] = p_row["source"]
        conn.execute(
            """
            INSERT INTO comparisons (id, workspace_id, baseline_snapshot_id, proposed_snapshot_id,
                parent_id, tolerance_m, compared_fields, parameters, inputs, summary, warnings,
                engine_version, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                cid,
                wid,
                bsid,
                psid,
                parent,
                opts.tolerance_m,
                list(opts.compare_fields),
                Jsonb(result["parameters"]),
                Jsonb(inputs),
                Jsonb(result["summary"]),
                Jsonb(result["warnings"]),
                result["engine_version"],
                result["created_at"],
            ),
        )
        cur = conn.cursor()
        with cur.copy(
            "COPY comparison_assets (comparison_id, asset_id, status, baseline, proposed) FROM STDIN"
        ) as copy:
            for a in result["assets"]:
                copy.write_row(
                    (
                        cid,
                        a["asset_id"],
                        a["status"],
                        json.dumps(a["baseline"]) if a["baseline"] else None,
                        json.dumps(a["proposed"]) if a["proposed"] else None,
                    )
                )
        with cur.copy(
            "COPY change_components (comparison_id, component_id, asset_id, asset_status, kind,"
            " field, ordinal, evidence, patch, distance_m, geom_from, geom_to) FROM STDIN"
        ) as copy:
            for a in result["assets"]:
                for k, c in enumerate(a["components"]):
                    ev = c["evidence"]
                    if c["kind"] == "move":
                        gf, gt, dist = _ewkt(ev["from"]), _ewkt(ev["to"]), ev["distance_m"]
                    elif c["kind"] == "addition":
                        gf, gt, dist = None, _ewkt(ev["coordinates"]), None
                    elif c["kind"] == "deletion":
                        gf, gt, dist = _ewkt(ev["coordinates"]), None, None
                    else:
                        loc = (a["baseline"] or a["proposed"])["coordinates"]
                        gf, gt, dist = _ewkt(loc), None, None
                    copy.write_row(
                        (
                            cid,
                            c["id"],
                            a["asset_id"],
                            a["status"],
                            c["kind"],
                            c.get("field"),
                            k,
                            json.dumps(ev),
                            json.dumps(c["patch"]),
                            dist,
                            gf,
                            gt,
                        )
                    )
        conn.execute(
            "INSERT INTO decisions (comparison_id, component_id) "
            "SELECT comparison_id, component_id FROM change_components WHERE comparison_id = %s",
            (cid,),
        )
    return get_comparison(conn, wid, cid)


def _owned_comparison(conn: Conn, wid: uuid.UUID, cid: uuid.UUID) -> DictRow:
    row = conn.execute(
        """
        SELECT c.*, bs.id_field AS baseline_id_field, ps.id_field AS proposed_id_field
          FROM comparisons c
          JOIN snapshots bs ON bs.id = c.baseline_snapshot_id
          JOIN snapshots ps ON ps.id = c.proposed_snapshot_id
         WHERE c.id = %s AND c.workspace_id = %s
        """,
        (cid, wid),
    ).fetchone()
    if row is None:
        raise not_found("Comparison")
    return row


def _progress(conn: Conn, cid: uuid.UUID) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT disposition, count(*) AS n FROM decisions WHERE comparison_id = %s GROUP BY 1",
        (cid,),
    ).fetchall()
    counts = {d: 0 for d in DISPOSITIONS} | {r["disposition"]: r["n"] for r in rows}
    total = sum(counts.values())
    resolved = counts["accepted"] + counts["rejected"]
    assets = conn.execute(
        """
        SELECT count(*) FILTER (WHERE unresolved = 0) AS done, count(*) AS total FROM (
          SELECT c.asset_id,
                 count(*) FILTER (WHERE d.disposition IN ('pending','flagged')) AS unresolved
            FROM change_components c JOIN decisions d USING (comparison_id, component_id)
           WHERE c.comparison_id = %s GROUP BY c.asset_id) t
        """,
        (cid,),
    ).fetchone()
    assert assets is not None
    return {
        "components": total,
        "by_disposition": counts,
        "resolved_components": resolved,
        "unresolved_components": total - resolved,
        "affected_assets": assets["total"],
        "resolved_assets": assets["done"],
        "complete": total == resolved,
    }


def _comparison_out(conn: Conn, row: DictRow) -> dict[str, Any]:
    return {
        "comparison_id": str(row["id"]),
        "parent_comparison_id": str(row["parent_id"]) if row["parent_id"] else None,
        "revision": row["revision"],
        "created_at": _iso(row["created_at"]),
        "engine_version": row["engine_version"],
        "parameters": row["parameters"],
        "inputs": row["inputs"],
        "summary": row["summary"],
        "warnings": row["warnings"],
        "progress": _progress(conn, row["id"]),
        "extent": _extent(conn, row),
    }


def _extent(conn: Conn, row: DictRow) -> list[float] | None:
    """[minLon, minLat, maxLon, maxLat] of changes, or of the baseline when nothing changed."""
    e = conn.execute(
        """
        SELECT ST_XMin(x) AS x0, ST_YMin(x) AS y0, ST_XMax(x) AS x1, ST_YMax(x) AS y1 FROM (
          SELECT ST_Extent(g) AS x FROM (
            SELECT geom_from AS g FROM change_components WHERE comparison_id = %s
            UNION ALL SELECT geom_to FROM change_components WHERE comparison_id = %s) s) t
        """,
        (row["id"], row["id"]),
    ).fetchone()
    if e is None or e["x0"] is None:
        e = conn.execute(
            "SELECT ST_XMin(x) AS x0, ST_YMin(x) AS y0, ST_XMax(x) AS x1, ST_YMax(x) AS y1 "
            "FROM (SELECT ST_Extent(geom) AS x FROM features WHERE snapshot_id = %s) t",
            (row["baseline_snapshot_id"],),
        ).fetchone()
    if e is None or e["x0"] is None:
        return None
    return [e["x0"], e["y0"], e["x1"], e["y1"]]


def get_comparison(conn: Conn, wid: uuid.UUID, cid: uuid.UUID) -> dict[str, Any]:
    return _comparison_out(conn, _owned_comparison(conn, wid, cid))


def list_comparisons(conn: Conn, wid: uuid.UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.*, bs.id_field AS baseline_id_field, ps.id_field AS proposed_id_field
          FROM comparisons c
          JOIN snapshots bs ON bs.id = c.baseline_snapshot_id
          JOIN snapshots ps ON ps.id = c.proposed_snapshot_id
         WHERE c.workspace_id = %s ORDER BY c.created_at DESC
        """,
        (wid,),
    ).fetchall()
    return [_comparison_out(conn, r) for r in rows]


def _components_for(
    conn: Conn, cid: uuid.UUID, asset_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT c.asset_id, c.component_id, c.kind, c.field, c.evidence, c.patch, c.distance_m,
               d.disposition, d.note, d.decided_at, d.version
          FROM change_components c JOIN decisions d USING (comparison_id, component_id)
         WHERE c.comparison_id = %s AND c.asset_id = ANY(%s)
         ORDER BY c.asset_id, c.ordinal
        """,
        (cid, asset_ids),
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["asset_id"], []).append(
            {
                "component_id": r["component_id"],
                "kind": r["kind"],
                "field": r["field"],
                "evidence": r["evidence"],
                "patch": r["patch"],
                "decision": {
                    "disposition": r["disposition"],
                    "note": r["note"],
                    "decided_at": _iso(r["decided_at"]),
                    "version": r["version"],
                },
            }
        )
    return out


def queue(
    conn: Conn,
    wid: uuid.UUID,
    cid: uuid.UUID,
    *,
    kind: str | None,
    disposition: str | None,
    status: str | None,
    q: str | None,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    row = _owned_comparison(conn, wid, cid)
    limit = max(1, min(limit, get_settings().page_max))
    where = ["c.comparison_id = %s"]
    params: list[Any] = [cid]
    if kind:
        where.append("c.kind = %s")
        params.append(kind)
    if disposition == "unresolved":
        where.append("d.disposition IN ('pending', 'flagged')")
    elif disposition:
        where.append("d.disposition = %s")
        params.append(disposition)
    if status:
        where.append("c.asset_status = %s")
        params.append(status)
    if q:
        where.append("c.asset_id ILIKE %s")
        params.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    filters = " AND ".join(where)
    total = conn.execute(
        f"SELECT count(DISTINCT c.asset_id) AS n FROM change_components c "  # noqa: S608
        f"JOIN decisions d USING (comparison_id, component_id) WHERE {filters}",
        params,
    ).fetchone()
    page_where = filters + (" AND c.asset_id > %s" if cursor else "")
    page_params = [*params, cursor] if cursor else params
    ids = [
        r["asset_id"]
        for r in conn.execute(
            f"SELECT DISTINCT c.asset_id FROM change_components c "  # noqa: S608
            f"JOIN decisions d USING (comparison_id, component_id) WHERE {page_where} "
            f"ORDER BY c.asset_id LIMIT %s",
            [*page_params, limit + 1],
        ).fetchall()
    ]
    has_more = len(ids) > limit
    ids = ids[:limit]
    comps = _components_for(conn, cid, ids)
    statuses = {
        r["asset_id"]: r
        for r in conn.execute(
            "SELECT asset_id, status, baseline, proposed FROM comparison_assets "
            "WHERE comparison_id = %s AND asset_id = ANY(%s)",
            (cid, ids),
        ).fetchall()
    }
    items = []
    for aid in ids:
        a = statuses[aid]
        loc = (a["proposed"] or a["baseline"])["coordinates"]
        items.append(
            {
                "asset_id": aid,
                "status": a["status"],
                "location": loc,
                "components": comps.get(aid, []),
            }
        )
    return {
        "comparison_id": str(cid),
        "revision": row["revision"],
        "total_assets": total["n"] if total else 0,
        "items": items,
        "next_cursor": ids[-1] if has_more and ids else None,
    }


def map_features(
    conn: Conn, wid: uuid.UUID, cid: uuid.UUID, bbox: tuple[float, float, float, float], limit: int
) -> dict[str, Any]:
    _owned_comparison(conn, wid, cid)
    limit = max(1, min(limit, get_settings().map_max_features))
    rows = conn.execute(
        """
        WITH hit AS (
          SELECT DISTINCT asset_id FROM change_components
           WHERE comparison_id = %(cid)s
             AND (geom_from && ST_MakeEnvelope(%(x0)s, %(y0)s, %(x1)s, %(y1)s, 4326)
               OR geom_to && ST_MakeEnvelope(%(x0)s, %(y0)s, %(x1)s, %(y1)s, 4326))
           ORDER BY asset_id LIMIT %(lim)s
        )
        SELECT c.asset_id, a.status,
               array_agg(c.kind ORDER BY c.ordinal) AS kinds,
               array_agg(d.disposition ORDER BY c.ordinal) AS dispositions,
               max(c.distance_m) AS distance_m,
               a.baseline -> 'coordinates' AS from_coords,
               a.proposed -> 'coordinates' AS to_coords
          FROM hit JOIN change_components c ON c.comparison_id = %(cid)s AND c.asset_id = hit.asset_id
          JOIN decisions d USING (comparison_id, component_id)
          JOIN comparison_assets a ON a.comparison_id = c.comparison_id AND a.asset_id = c.asset_id
         GROUP BY c.asset_id, a.status, a.baseline, a.proposed
         ORDER BY c.asset_id
        """,
        {"cid": cid, "x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3], "lim": limit + 1},
    ).fetchall()
    truncated = len(rows) > limit
    feats = []
    for r in rows[:limit]:
        coords = r["to_coords"] or r["from_coords"]
        feats.append(
            {
                "type": "Feature",
                "id": r["asset_id"],
                "geometry": {"type": "Point", "coordinates": coords},
                "properties": {
                    "asset_id": r["asset_id"],
                    "status": r["status"],
                    "kinds": r["kinds"],
                    "dispositions": r["dispositions"],
                    "distance_m": r["distance_m"],
                    "from": r["from_coords"],
                    "to": r["to_coords"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": feats, "truncated": truncated}


def baseline_features(
    conn: Conn, wid: uuid.UUID, cid: uuid.UUID, bbox: tuple[float, float, float, float], limit: int
) -> dict[str, Any]:
    row = _owned_comparison(conn, wid, cid)
    limit = max(1, min(limit, get_settings().map_max_features))
    rows = conn.execute(
        """
        SELECT asset_id, ST_X(geom) AS x, ST_Y(geom) AS y FROM features
         WHERE snapshot_id = %s AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
         ORDER BY asset_id LIMIT %s
        """,
        (row["baseline_snapshot_id"], *bbox, limit + 1),
    ).fetchall()
    return {
        "type": "FeatureCollection",
        "truncated": len(rows) > limit,
        "features": [
            {
                "type": "Feature",
                "id": r["asset_id"],
                "geometry": {"type": "Point", "coordinates": [r["x"], r["y"]]},
                "properties": {"asset_id": r["asset_id"]},
            }
            for r in rows[:limit]
        ],
    }


def asset_detail(conn: Conn, wid: uuid.UUID, cid: uuid.UUID, asset_id: str) -> dict[str, Any]:
    row = _owned_comparison(conn, wid, cid)
    a = conn.execute(
        "SELECT asset_id, status, baseline, proposed FROM comparison_assets WHERE comparison_id = %s AND asset_id = %s",
        (cid, asset_id),
    ).fetchone()
    if a is None:
        raise not_found("Asset")
    events = conn.execute(
        """
        SELECT batch_id, action, component_id, from_disposition, to_disposition, note,
               revision, undone_by, created_at
          FROM review_events
         WHERE comparison_id = %s AND component_id LIKE %s ESCAPE '\\'
         ORDER BY id DESC LIMIT 100
        """,
        (cid, asset_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ":%"),
    ).fetchall()
    return {
        "comparison_id": str(cid),
        "revision": row["revision"],
        "asset_id": a["asset_id"],
        "status": a["status"],
        "baseline": a["baseline"],
        "proposed": a["proposed"],
        "components": _components_for(conn, cid, [asset_id]).get(asset_id, []),
        "history": [_event_out(e) for e in events],
    }


def _event_out(e: DictRow) -> dict[str, Any]:
    return {
        "batch_id": str(e["batch_id"]),
        "action": e["action"],
        "component_id": e["component_id"],
        "from": e["from_disposition"],
        "to": e["to_disposition"],
        "note": e["note"],
        "revision": e["revision"],
        "undone": e["undone_by"] is not None,
        "at": _iso(e["created_at"]),
    }


# --------------------------------------------------------------------------- review


def _check_disposition(disposition: str, note: str | None) -> None:
    if disposition not in DISPOSITIONS:
        raise ApiError(
            422,
            "invalid_disposition",
            f"Unknown disposition {disposition!r}.",
            "Use pending, accepted, rejected or flagged.",
        )
    if disposition == "flagged" and not (note or "").strip():
        raise ApiError(
            422,
            "note_required",
            "Flagging requires a note.",
            "Say what needs follow-up, for example 'confirm location with crew'.",
        )


def _apply(
    conn: Conn,
    cid: uuid.UUID,
    batch: uuid.UUID,
    action: Literal["decide", "bulk", "undo"],
    changes: list[tuple[str, str, str | None, int | None]],
) -> int:
    """Apply ``(component_id, disposition, note, expected_version)`` changes atomically."""
    rev = conn.execute(
        "UPDATE comparisons SET revision = revision + 1 WHERE id = %s RETURNING revision", (cid,)
    ).fetchone()
    assert rev is not None
    revision = int(rev["revision"])
    conflicts = []
    for comp_id, disp, note, expected in changes:
        cur = conn.execute(
            """
            SELECT disposition, note, version FROM decisions
             WHERE comparison_id = %s AND component_id = %s FOR UPDATE
            """,
            (cid, comp_id),
        ).fetchone()
        if cur is None:
            raise not_found(f"Component {comp_id!r}")
        if expected is not None and cur["version"] != expected:
            conflicts.append(
                {
                    "component_id": comp_id,
                    "expected_version": expected,
                    "current_version": cur["version"],
                    "current_disposition": cur["disposition"],
                }
            )
            continue
        new_version = cur["version"] + 1
        conn.execute(
            """
            UPDATE decisions SET disposition = %s, note = %s, version = %s,
                   decided_at = CASE WHEN %s = 'pending' THEN NULL ELSE now() END
             WHERE comparison_id = %s AND component_id = %s
            """,
            (disp, note, new_version, disp, cid, comp_id),
        )
        conn.execute(
            """
            INSERT INTO review_events (comparison_id, batch_id, action, component_id,
                from_disposition, to_disposition, from_note, note, to_version, revision)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                cid,
                batch,
                action,
                comp_id,
                cur["disposition"],
                disp,
                cur["note"],
                note,
                new_version,
                revision,
            ),
        )
    if conflicts:
        raise ApiError(
            409,
            "stale_decision",
            f"{len(conflicts)} component(s) changed since this view was loaded.",
            "Another tab or session updated these decisions. Reload the asset and decide again.",
            conflicts=conflicts,
        )
    return revision


def decide(
    conn: Conn, wid: uuid.UUID, cid: uuid.UUID, items: list[dict[str, Any]]
) -> dict[str, Any]:
    if not items:
        raise ApiError(
            422, "empty_request", "No decisions were supplied.", "Send at least one decision."
        )
    for it in items:
        _check_disposition(it["disposition"], it.get("note"))
    batch = uuid.uuid4()
    with conn.transaction():
        _owned_comparison(conn, wid, cid)
        revision = _apply(
            conn,
            cid,
            batch,
            "decide",
            [
                (
                    it["component_id"],
                    it["disposition"],
                    (it.get("note") or None),
                    it.get("expected_version"),
                )
                for it in items
            ],
        )
    return {
        "batch_id": str(batch),
        "revision": revision,
        "applied": len(items),
        "progress": _progress(conn, cid),
    }


def _bulk_targets(conn: Conn, cid: uuid.UUID, scope: dict[str, Any]) -> list[str]:
    where = ["c.comparison_id = %s"]
    params: list[Any] = [cid]
    if scope.get("kind"):
        where.append("c.kind = %s")
        params.append(scope["kind"])
    if scope.get("field"):
        where.append("c.field = %s")
        params.append(scope["field"])
    if scope.get("current_disposition"):
        where.append("d.disposition = %s")
        params.append(scope["current_disposition"])
    if scope.get("asset_ids"):
        where.append("c.asset_id = ANY(%s)")
        params.append(list(scope["asset_ids"]))
    if scope.get("max_distance_m") is not None:
        where.append("c.distance_m <= %s")
        params.append(scope["max_distance_m"])
    rows = conn.execute(
        f"SELECT c.component_id FROM change_components c JOIN decisions d USING (comparison_id, component_id) "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY c.asset_id, c.ordinal",
        params,
    ).fetchall()
    return [r["component_id"] for r in rows]


def bulk_decide(conn: Conn, wid: uuid.UUID, cid: uuid.UUID, req: dict[str, Any]) -> dict[str, Any]:
    _check_disposition(req["disposition"], req.get("note"))
    scope = req["scope"]
    if (
        not any(scope.get(k) for k in ("kind", "field", "current_disposition", "asset_ids"))
        and scope.get("max_distance_m") is None
    ):
        raise ApiError(
            422,
            "scope_required",
            "A bulk decision needs an explicit scope.",
            "Filter by kind, field, current disposition, distance or asset IDs.",
        )
    batch = uuid.uuid4()
    with conn.transaction():
        _owned_comparison(conn, wid, cid)
        targets = _bulk_targets(conn, cid, scope)
        if len(targets) != req["expected_count"]:
            raise ApiError(
                409,
                "bulk_count_mismatch",
                f"The scope now matches {len(targets)} components, not {req['expected_count']}.",
                "Review the updated count and confirm again.",
                actual_count=len(targets),
            )
        if len(targets) > get_settings().bulk_max:
            raise ApiError(
                422,
                "bulk_too_large",
                f"Bulk decisions are limited to {get_settings().bulk_max} components.",
                "Narrow the scope.",
            )
        revision = _apply(
            conn,
            cid,
            batch,
            "bulk",
            [(t, req["disposition"], req.get("note"), None) for t in targets],
        )
    return {
        "batch_id": str(batch),
        "revision": revision,
        "applied": len(targets),
        "scope": scope,
        "progress": _progress(conn, cid),
    }


def preview_bulk(
    conn: Conn, wid: uuid.UUID, cid: uuid.UUID, scope: dict[str, Any]
) -> dict[str, Any]:
    _owned_comparison(conn, wid, cid)
    targets = _bulk_targets(conn, cid, scope)
    return {"scope": scope, "count": len(targets), "sample": targets[:20]}


def undo(conn: Conn, wid: uuid.UUID, cid: uuid.UUID, expected_revision: int) -> dict[str, Any]:
    with conn.transaction():
        row = conn.execute(
            "SELECT revision FROM comparisons WHERE id = %s AND workspace_id = %s FOR UPDATE",
            (cid, wid),
        ).fetchone()
        if row is None:
            raise not_found("Comparison")
        if row["revision"] != expected_revision:
            raise ApiError(
                409,
                "stale_revision",
                f"The comparison is at revision {row['revision']}, not {expected_revision}.",
                "Reload to see the latest decisions before undoing.",
                current_revision=row["revision"],
            )
        last = conn.execute(
            """
            SELECT batch_id FROM review_events
             WHERE comparison_id = %s AND action <> 'undo' AND undone_by IS NULL
             ORDER BY id DESC LIMIT 1
            """,
            (cid,),
        ).fetchone()
        if last is None:
            raise ApiError(409, "nothing_to_undo", "There is no decision left to undo.", "")
        events = conn.execute(
            "SELECT component_id, from_disposition, from_note FROM review_events WHERE comparison_id = %s AND batch_id = %s ORDER BY id",
            (cid, last["batch_id"]),
        ).fetchall()
        batch = uuid.uuid4()
        revision = _apply(
            conn,
            cid,
            batch,
            "undo",
            [(e["component_id"], e["from_disposition"], e["from_note"], None) for e in events],
        )
        conn.execute(
            "UPDATE review_events SET undone_by = %s WHERE comparison_id = %s AND batch_id = %s",
            (batch, cid, last["batch_id"]),
        )
    return {
        "batch_id": str(batch),
        "undid_batch_id": str(last["batch_id"]),
        "revision": revision,
        "components": [e["component_id"] for e in events],
        "progress": _progress(conn, cid),
    }


def history(
    conn: Conn, wid: uuid.UUID, cid: uuid.UUID, before: int | None, limit: int
) -> dict[str, Any]:
    _owned_comparison(conn, wid, cid)
    limit = max(1, min(limit, get_settings().page_max))
    rows = conn.execute(
        f"""
        SELECT id, batch_id, action, component_id, from_disposition, to_disposition, note,
               revision, undone_by, created_at
          FROM review_events WHERE comparison_id = %s {"AND id < %s" if before else ""}
         ORDER BY id DESC LIMIT %s
        """,  # noqa: S608
        [cid, before, limit + 1] if before else [cid, limit + 1],
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "events": [_event_out(r) | {"event_id": r["id"]} for r in rows],
        "next_before": rows[-1]["id"] if more and rows else None,
    }


# --------------------------------------------------------------------------- export


def export(
    conn: Conn, wid: uuid.UUID, cid: uuid.UUID, mode: Literal["draft", "final"]
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = _owned_comparison(conn, wid, cid)
    snap = [
        {"asset_id": r["asset_id"], "feature": r["feature"]}
        for r in conn.execute(
            "SELECT asset_id, feature FROM features WHERE snapshot_id = %s ORDER BY asset_id",
            (row["baseline_snapshot_id"],),
        ).fetchall()
    ]
    comp_rows = conn.execute(
        """
        SELECT c.asset_id, c.component_id, c.kind, c.field, c.evidence, c.patch,
               d.disposition, d.note, d.decided_at
          FROM change_components c JOIN decisions d USING (comparison_id, component_id)
         WHERE c.comparison_id = %s ORDER BY c.asset_id, c.ordinal
        """,
        (cid,),
    ).fetchall()
    assets: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    for r in comp_rows:
        a = assets.setdefault(r["asset_id"], {"asset_id": r["asset_id"], "components": []})
        comp = {
            "id": r["component_id"],
            "kind": r["kind"],
            "evidence": r["evidence"],
            "patch": r["patch"],
        }
        if r["field"]:
            comp["field"] = r["field"]
        a["components"].append(comp)
        decisions[r["component_id"]] = {
            "disposition": r["disposition"],
            "note": r["note"],
            "decided_at": _iso(r["decided_at"]),
        }
    comparison = {
        "engine_version": row["engine_version"],
        "created_at": _iso(row["created_at"]),
        "parameters": row["parameters"],
        "inputs": row["inputs"],
        "assets": [assets[k] for k in sorted(assets)],
    }
    attribution = {
        "baseline": row["inputs"]["baseline"].get("source"),
        "proposed": row["inputs"]["proposed"].get("source"),
    }
    events = [
        _event_out(e)
        for e in conn.execute(
            """
            SELECT batch_id, action, component_id, from_disposition, to_disposition, note,
                   revision, undone_by, created_at
              FROM review_events WHERE comparison_id = %s ORDER BY id
            """,
            (cid,),
        ).fetchall()
    ]
    try:
        fc, report = resolve(
            snap,
            comparison,
            decisions,
            mode=mode,
            attribution=attribution if any(attribution.values()) else None,
            history=events,
        )
    except FinalExportBlocked as exc:
        raise ApiError(
            409,
            "unresolved_components",
            str(exc),
            "Accept or reject every pending and flagged component, or download a draft export.",
            unresolved=exc.unresolved[:200],
            unresolved_count=len(exc.unresolved),
        ) from exc
    report["comparison_id"] = str(cid)
    report["revision"] = row["revision"]
    return fc, report
