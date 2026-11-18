# Independent QGIS check: BENCH-0142 geodesic distance

Status: **instructions only. This check has not been run.** QGIS is not installed on the build
machine. The automated cross-checks that did run are PostGIS geography (within 1 mm) and
GeoPandas UTM 14N (within 0.1%). Record your result below if you run it.

Expected: **119.607 m** between the approved and reported BENCH-0142 positions.

## Steps (QGIS 3.34 LTR or newer)

1. In a new project, open **Project > Properties > General**. Set **Ellipsoid** to
   `WGS 84 (EPSG:7030)` and both **Units for distance** and **Units for area** to meters.
   Leave the project CRS as EPSG:4326.
2. Add `fixtures/golden/baseline.geojson` and `fixtures/golden/proposed.geojson`
   (Layer > Add Layer > Add Vector Layer). Name them `baseline` and `proposed`.
3. Open **Processing > Toolbox > Geometry by expression**. Set Input layer = `proposed`,
   Output geometry type = Line, and use this expression:

   ```
   make_line(
     geometry(get_feature('baseline', 'asset_id', "asset_id")),
     $geometry
   )
   ```

4. In the output layer, select the row where `asset_id = 'BENCH-0142'`. Open the Field
   Calculator and create a decimal field `ellip_m` with expression `$length`. `$length` uses
   the project ellipsoid, so the result is an ellipsoidal distance in meters.
5. Expect `ellip_m` ≈ 119.61. As a second check, the **Measure Line** tool (Ctrl+Shift+M) with
   **Ellipsoidal** selected, snapping between the two points, should read about 119.6 m.
6. For contrast, `length($geometry)` returns the planar length in degrees (about 0.00108). That
   is the wrong unit for distance checks.

## Result log

| Date | QGIS version | Ellipsoid | `$length` (m) | Run by |
| --- | --- | --- | --- | --- |
| _not yet run_ | | | | |
