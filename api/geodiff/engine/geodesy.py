"""Geodesic measurement on the WGS84 ellipsoid.

Distances use pyproj's ``Geod(ellps="WGS84").inv``, which implements Karney's geodesic
algorithm (GeographicLib) and is accurate to a few nanometres. PostGIS
``ST_Distance(geography, geography)`` uses the same algorithm; the integration tests
cross-check the two. Classification uses the full-precision value; only display rounds.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from pyproj import Geod

WGS84 = Geod(ellps="WGS84")
DISTANCE_METHOD = "pyproj.Geod(ellps='WGS84').inv: Karney geodesic on the WGS84 ellipsoid"


def geodesic(lon1: float, lat1: float, lon2: float, lat2: float) -> tuple[float, float]:
    """Return ``(distance_m, forward_azimuth_deg)`` from point 1 to point 2.

    The azimuth is normalized to [0, 360). Longitudes on either side of the antimeridian
    are handled by the geodesic itself (179.9999 to -179.9999 is a short hop).
    """
    az, _back, dist = WGS84.inv(lon1, lat1, lon2, lat2)
    return float(dist), float(az) % 360.0


def geodesic_many(
    lon1: Sequence[float], lat1: Sequence[float], lon2: Sequence[float], lat2: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    if len(lon1) == 0:
        return np.zeros(0), np.zeros(0)
    az, _back, dist = WGS84.inv(
        np.asarray(lon1, dtype=float),
        np.asarray(lat1, dtype=float),
        np.asarray(lon2, dtype=float),
        np.asarray(lat2, dtype=float),
    )
    return np.asarray(dist, dtype=float), np.mod(np.asarray(az, dtype=float), 360.0)


def offset(lon: float, lat: float, azimuth_deg: float, distance_m: float) -> tuple[float, float]:
    """Point reached by travelling ``distance_m`` along ``azimuth_deg`` (fixture generation)."""
    lon2, lat2, _ = WGS84.fwd(lon, lat, azimuth_deg, distance_m)
    return float(lon2), float(lat2)


def display_distance(distance_m: float) -> str:
    """Human display value; never use for classification."""
    if distance_m < 10:
        return f"{distance_m:.2f} m"
    if distance_m < 1000:
        return f"{distance_m:.1f} m"
    return f"{distance_m / 1000:.2f} km"
