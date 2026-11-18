"""The single comparison authority for GeoDiff.

Everything that decides what counts as a change lives here: parsing/validation
(:mod:`.geojson`), identifier policy (:mod:`.ids`), geodesic measurement (:mod:`.geodesy`),
change detection (:mod:`.compare`) and resolution of decisions into an export
(:mod:`.resolve`). The API, the CLI and the static demo fixture all use these functions.
"""

from geodiff.engine.compare import ComparisonError, compare_datasets
from geodiff.engine.geojson import ParsedDataset, parse_geojson
from geodiff.engine.resolve import FinalExportBlocked, resolve

SCHEMA_VERSION = "geodiff.comparison/1"

__all__ = [
    "SCHEMA_VERSION",
    "ComparisonError",
    "FinalExportBlocked",
    "ParsedDataset",
    "compare_datasets",
    "parse_geojson",
    "resolve",
]
