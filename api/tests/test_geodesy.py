import os

import pytest

from geodiff.engine.compare import CompareOptions, compare_datasets
from geodiff.engine.geodesy import display_distance, geodesic, offset
from tests.helpers import fc, from_obj, pt


def test_zero_distance():
    d, _ = geodesic(-96.7969, 32.78434, -96.7969, 32.78434)
    assert d == 0.0


def test_small_displacement_one_centimetre():
    lon, lat = offset(-96.7969, 32.78434, 45.0, 0.01)
    d, az = geodesic(-96.7969, 32.78434, lon, lat)
    assert d == pytest.approx(0.01, abs=1e-9)
    assert az == pytest.approx(45.0, abs=1e-3)


def test_high_latitude_longitude_degree_is_short():
    # One degree of longitude near the pole is ~194 m, not ~111 km.
    d, _ = geodesic(0.0, 89.9, 1.0, 89.9)
    assert 190 < d < 200


def test_antimeridian_wraparound_is_a_short_hop():
    d, az = geodesic(179.9999, 0.0, -179.9999, 0.0)
    assert d == pytest.approx(22.264, abs=0.01)
    assert az == pytest.approx(90.0, abs=1e-6)


def test_wraparound_move_is_classified_by_geodesic_not_planar_delta():
    base = from_obj(fc(pt(179.99999, 10.0, "A")))
    prop = from_obj(fc(pt(-179.99999, 10.0, "A")))
    r = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), 1.0))
    move = r["assets"][0]["components"][0]
    assert move["kind"] == "move"
    assert move["evidence"]["distance_m"] == pytest.approx(2.19, abs=0.01)
    # With a 2.5 m tolerance the same pair is not a move, though |dlon| is ~360 degrees.
    r2 = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), 2.5))
    assert r2["assets"] == [] and r2["summary"]["within_tolerance_moves"] == 1


def test_tolerance_is_strict_and_uses_full_precision():
    lon2, lat2 = offset(-96.8, 32.78, 10.0, 2.0)
    exact, _ = geodesic(-96.8, 32.78, lon2, lat2)
    base = from_obj(fc(pt(-96.8, 32.78, "A")))
    prop = from_obj(fc(pt(lon2, lat2, "A")))
    at = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), exact))
    assert at["assets"] == [], "distance equal to tolerance is not a move"
    below = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), exact - 1e-9))
    assert below["assets"][0]["components"][0]["kind"] == "move"


def test_display_rounding_is_separate():
    assert display_distance(119.6067050342158) == "119.6 m"
    assert display_distance(2.1071111) == "2.11 m"
    assert display_distance(1534.0) == "1.53 km"


@pytest.mark.db
def test_pyproj_agrees_with_postgis_geography():
    import psycopg

    pairs = [
        (-96.7969, 32.78434, -96.79676, 32.785412),  # BENCH-0142
        (0.0, 89.9, 1.0, 89.9),
        (179.9999, 0.0, -179.9999, 0.0),
        (-96.79744, 32.78211, -96.79744, 32.782129),
    ]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for lon1, lat1, lon2, lat2 in pairs:
            row = conn.execute(
                "SELECT ST_Distance(ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,"
                " ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, true)",
                (lon1, lat1, lon2, lat2),
            ).fetchone()
            assert row is not None
            d, _ = geodesic(lon1, lat1, lon2, lat2)
            assert row[0] == pytest.approx(d, abs=1e-3), (lon1, lat1, lon2, lat2)
