"""Independent cross-checks with GeoPandas/pyogrio (GDAL) rather than our own parser."""

import pytest

gpd = pytest.importorskip("geopandas")

from geodiff.engine.compare import CompareOptions, compare_datasets  # noqa: E402
from tests.helpers import GOLDEN, load  # noqa: E402


def test_gdal_reads_the_same_features_and_ids():
    for name in ("baseline.geojson", "proposed.geojson"):
        gdf = gpd.read_file(GOLDEN / name)
        ours = load(GOLDEN / name)
        assert len(gdf) == ours.feature_count
        assert set(gdf["asset_id"]) == {f["properties"]["asset_id"] for f in ours.features}
        assert set(gdf.geometry.geom_type) == {"Point"}


def test_local_utm_distance_agrees_with_geodesic_within_point1_percent():
    base = gpd.read_file(GOLDEN / "baseline.geojson").set_index("asset_id").to_crs(32614)
    prop = gpd.read_file(GOLDEN / "proposed.geojson").set_index("asset_id").to_crs(32614)
    result = compare_datasets(
        load(GOLDEN / "baseline.geojson"),
        load(GOLDEN / "proposed.geojson"),
        CompareOptions("asset_id", "asset_id", (), 2.0),
    )
    moves = [c for a in result["assets"] for c in a["components"] if c["kind"] == "move"]
    assert moves
    for m in moves:
        aid = m["id"].split(":")[0]
        planar = base.geometry[aid].distance(prop.geometry[aid])
        # UTM scale factor near the zone edge stays well below 0.1%.
        assert planar == pytest.approx(m["evidence"]["distance_m"], rel=1e-3)
