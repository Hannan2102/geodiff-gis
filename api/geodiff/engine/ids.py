"""Identifier policy.

Assets are matched only by an explicit ID field, never by array position.

Normalization (see docs/ENGINE.md, "ID policy"):

* Strings are NFC-normalized and trimmed of surrounding whitespace. Matching is
  case-sensitive and leading zeros are significant: ``"0142"`` and ``"142"`` are different
  assets.
* Integers (and floats with an exact integer value below 2**53) become their canonical decimal
  string, so ``142`` and ``142.0`` are both ``"142"``. A numeric ``142`` in one version matches
  the string ``"142"`` in the other (reported as an ``id_type_mismatch`` warning).
* Null, empty, boolean, fractional, object and array values are rejected.
* Within one version, two raw values that normalize to the same ID are *ambiguous*
  (``142`` and ``"142"``); identical raw values are *duplicates*. Both block comparison.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from geodiff.engine.issues import Issue, IssueLog

MAX_SAFE_INT = 2**53


class IdError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_id(raw: Any) -> str:
    """Return the canonical string form of an ID value, or raise :class:`IdError`."""
    if raw is None:
        raise IdError("null_id", "ID is null")
    if isinstance(raw, bool):
        raise IdError("unsupported_id_type", "ID is a boolean")
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        if raw.is_integer() and abs(raw) < MAX_SAFE_INT:
            return str(int(raw))
        raise IdError("unsupported_id_type", f"ID {raw!r} is not an integer")
    if isinstance(raw, str):
        s = unicodedata.normalize("NFC", raw).strip()
        if not s:
            raise IdError("empty_id", "ID is empty")
        return s
    raise IdError("unsupported_id_type", f"ID has unsupported type {type(raw).__name__}")


def id_kind(raw: Any) -> str:
    return "number" if isinstance(raw, int | float) and not isinstance(raw, bool) else "string"


def index_ids(features: list[dict[str, Any]], id_field: str) -> tuple[dict[str, int], IssueLog]:
    """Map normalized ID -> feature index, collecting ID issues for ``id_field``."""
    log = IssueLog()
    index: dict[str, int] = {}
    raw_by_id: dict[str, Any] = {}
    for i, feat in enumerate(features):
        props = feat.get("properties") or {}
        if id_field not in props:
            log.add(
                Issue(
                    "missing_id",
                    f"Feature {i} has no {id_field!r} property.",
                    feature_index=i,
                    field=id_field,
                )
            )
            continue
        raw = props[id_field]
        try:
            nid = normalize_id(raw)
        except IdError as exc:
            log.add(
                Issue(
                    exc.code,
                    f"Feature {i}: {exc} in field {id_field!r}.",
                    feature_index=i,
                    field=id_field,
                )
            )
            continue
        if nid in index:
            prev = raw_by_id[nid]
            same = type(prev) is type(raw) and prev == raw
            code = "duplicate_id" if same else "ambiguous_id"
            what = "appears again" if same else f"normalizes to the same ID as {prev!r}"
            log.add(
                Issue(
                    code,
                    f"Feature {i}: ID {raw!r} {what} (first at feature {index[nid]}).",
                    feature_index=i,
                    field=id_field,
                    value=nid,
                )
            )
            continue
        index[nid] = i
        raw_by_id[nid] = raw
    return index, log
