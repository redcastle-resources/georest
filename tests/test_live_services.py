"""Live (network) tests for `geoREST/RESTesri/services.py`.

Opt in with `SERVICES_LIVE=1` (or `EDW_LIVE=1`, which turns on the whole
network suite). Skipped by default.

These hit two public services rather than EDW:

* an **Image Service** — USFS LCMS Annual Change (CONUS), a mosaic whose
  catalog rows are calendar years, so a single year is selected with a
  mosaic rule rather than a URL path;
* a **Feature Service** — Esri's public Earthquakes_Since1970 sample.

Assertions are kept to properties that are structural rather than editorial:
shapes, types, spatial references, and the relationships between operations.
Pixel values and feature counts are deliberately *not* pinned, since both
services are republished independently of this repo.

Transient server trouble skips rather than fails (`support.retry`), so a red
result here means a real problem in the client.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from geoREST.RESTesri import services as S

from .support import requires_live_services, retry

IMG = ("https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/"
       "USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer")
FEAT = ("https://sampleserver6.arcgisonline.com/arcgis/rest/services/"
        "Earthquakes_Since1970/FeatureServer/0")

#: A small AOI near the 2020 Castle Fire (Sequoia NF, CA) — inside the
#: service's coverage, small enough to compute over quickly.
AOI = "-118.65,36.05,-118.45,36.25"
IN_AOI = (-118.55, 36.15)
YEAR_2020 = {"where": "year=2020"}

#: Mid-Pacific — comfortably outside a CONUS service, so it must read NoData.
OFF_COVERAGE = (-140.0, 36.15)

#: Image Service operations can take 30-45+ seconds against a large mosaic
#: catalog; that is the server working, not a hang.
SLOW = 180


@requires_live_services
class FeatureServiceTests(unittest.TestCase):
    """queryFeatureServiceCount / queryFeatureService / getLayerInfo."""

    def test_count_is_a_positive_int(self):
        total = retry(lambda: S.queryFeatureServiceCount(FEAT))
        self.assertIsInstance(total, int)
        self.assertGreater(total, 0)

    def test_a_where_clause_narrows_the_count(self):
        total = retry(lambda: S.queryFeatureServiceCount(FEAT))
        strong = retry(lambda: S.queryFeatureServiceCount(FEAT, where="magnitude>=7"))
        self.assertLess(strong, total)

    def test_a_spatial_filter_narrows_the_count(self):
        total = retry(lambda: S.queryFeatureServiceCount(FEAT))
        california = retry(lambda: S.queryFeatureServiceCount(FEAT, geometry="-125,32,-114,42"))
        self.assertLess(california, total)

    def test_featureserver_root_resolves_to_layer_zero(self):
        root = FEAT.rsplit("/", 1)[0]
        self.assertEqual(
            retry(lambda: S.queryFeatureServiceCount(root)),
            retry(lambda: S.queryFeatureServiceCount(FEAT)),
        )

    def test_an_invalid_where_clause_raises_valueerror(self):
        with self.assertRaises(ValueError):
            S.queryFeatureServiceCount(FEAT, where="no_such_field=1")

    def test_query_returns_a_geojson_feature_collection(self):
        fc = retry(lambda: S.queryFeatureService(FEAT, where="magnitude>=8",
                                                 out_fields="name,magnitude"))
        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertTrue(fc["features"])
        props = fc["features"][0]["properties"]
        self.assertIn("magnitude", props)
        self.assertNotIn("depth", props, "out_fields must be honoured server-side")

    def test_the_count_preflight_blocks_an_oversized_fetch(self):
        with self.assertRaisesRegex(ValueError, "max_features"):
            S.queryFeatureService(FEAT, where="magnitude>=5", max_features=1)

    def test_a_bbox_spatial_filter_is_applied(self):
        fc = retry(lambda: S.queryFeatureService(
            FEAT, geometry="-125,32,-114,42", geometry_type="esriGeometryEnvelope",
            where="magnitude>=6", out_fields="name,magnitude"))
        for feature in fc["features"]:
            lon, lat = feature["geometry"]["coordinates"][:2]
            self.assertTrue(-125 <= lon <= -114, f"{lon} outside the requested bbox")
            self.assertTrue(32 <= lat <= 42, f"{lat} outside the requested bbox")

    def test_layer_info_reports_fields_and_geometry_type(self):
        info = retry(lambda: S.getLayerInfo(FEAT))
        self.assertEqual(info["geometryType"], "esriGeometryPoint")
        self.assertIn("Query", info["capabilities"])
        self.assertIn("magnitude", [f["name"] for f in info["fields"]])
        self.assertGreater(info["maxRecordCount"], 0)

    def test_layer_info_rejects_a_service_root_and_names_its_layers(self):
        """A service root is a container, not a queryable layer."""
        root = "https://sampleserver6.arcgisonline.com/arcgis/rest/services/Census/MapServer"
        with self.assertRaises(ValueError) as ctx:
            S.getLayerInfo(root)
        self.assertIn("sub-layers", str(ctx.exception))

    def test_a_nonexistent_service_raises_valueerror_not_runtimeerror(self):
        """ArcGIS answers 200 with an Esri 404 error object here."""
        missing = ("https://sampleserver6.arcgisonline.com/arcgis/rest/services/"
                   "NoSuchService/FeatureServer/0")
        with self.assertRaises(ValueError):
            S.getLayerInfo(missing)

    def test_an_unreachable_host_raises_runtimeerror(self):
        """The documented transport failure mode for the whole module."""
        with self.assertRaises(RuntimeError):
            S.queryFeatureServiceCount(
                "https://no-such-host-here-xyz.invalid/x/FeatureServer/0", timeout=5)


#: A genuinely *cached* service, for the tile-template test. The LCMS Image
#: Service above has no tile cache at all (no `tileInfo`), so every tile URL
#: built from it answers 404 — see `getImageServiceTileUrl`'s docstring.
TILED = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer"


@requires_live_services
class TileUrlTests(unittest.TestCase):

    def test_the_tile_template_resolves_to_a_real_tile(self):
        """Proves the {z}/{y}/{x} ordering against a live cached service."""
        from geoREST.RESTesri._http import fetch_bytes

        template = S.getImageServiceTileUrl(TILED)
        # y before x is the whole point: these two are not interchangeable.
        url = template.format(z=6, y=25, x=12)
        raw, content_type = retry(lambda: fetch_bytes(url, timeout=60))
        self.assertGreater(len(raw), 0)
        self.assertTrue(content_type.startswith("image/"), content_type)
        self.assertNotEqual(raw.lstrip()[:1], b"{", "got a JSON error, not a tile")

    def test_an_uncached_service_yields_a_template_whose_tiles_404(self):
        """The documented trap: the template is built without validation."""
        from geoREST.RESTesri._http import fetch_bytes, fetch_json

        meta = retry(lambda: fetch_json(IMG, {"f": "json"}, timeout=60))
        self.assertNotIn("tileInfo", meta, "LCMS gained a tile cache; revisit this test")
        url = S.getImageServiceTileUrl(IMG).format(z=6, y=25, x=12)
        with self.assertRaises(RuntimeError):
            fetch_bytes(url, timeout=60)


@requires_live_services
class ImageServiceTests(unittest.TestCase):
    """exportImage / computeStatisticsHistograms / identify / getSamples / boundary."""

    def test_layer_info_on_an_imageserver_root_returns_catalog_fields(self):
        info = retry(lambda: S.getLayerInfo(IMG))
        names = [f["name"] for f in info["fields"]]
        self.assertIn("objectid", names)
        self.assertIn("Image", info["capabilities"])

    def test_mosaic_catalog_query_falls_back_to_esri_json(self):
        """An ImageServer's /query rejects f=geojson; the fallback covers it."""
        fc = retry(lambda: S.queryFeatureService(IMG, where="year=2020",
                                                 out_fields="name,year", timeout=SLOW))
        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertTrue(fc["features"])
        feature = fc["features"][0]
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertEqual(feature["properties"]["year"], 2020)
        self.assertEqual(feature["id"], "0", "the fallback path renumbers ids positionally")

    def test_export_image_returns_a_tiff(self):
        raw = retry(lambda: S.exportImage(IMG, bbox=AOI, size=(64, 64),
                                          mosaic_rule=YEAR_2020, timeout=SLOW))
        self.assertIsInstance(raw, bytes)
        self.assertIn(raw[:4], (b"II*\x00", b"MM\x00*"), "not a TIFF header")

    def test_export_image_honours_format_and_rendering_rule(self):
        raw = retry(lambda: S.exportImage(
            IMG, bbox=AOI, size=(64, 64), image_format="png", mosaic_rule=YEAR_2020,
            rendering_rule={"rasterFunction": "Annual_Change"}, timeout=SLOW))
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

    def test_export_image_writes_out_path_and_returns_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "lcms.png")
            got = retry(lambda: S.exportImage(
                IMG, bbox=AOI, size=(32, 32), image_format="png",
                mosaic_rule=YEAR_2020, out_path=dest, timeout=SLOW))
            self.assertEqual(got, dest)
            self.assertGreater(os.path.getsize(dest), 0)

    def test_every_bbox_input_form_agrees(self):
        forms = [AOI, [-118.65, 36.05, -118.45, 36.25],
                 {"xmin": -118.65, "ymin": 36.05, "xmax": -118.45, "ymax": 36.25}]
        sizes = [len(retry(lambda b=b: S.exportImage(
            IMG, bbox=b, size=(32, 32), mosaic_rule=YEAR_2020, timeout=SLOW))) for b in forms]
        self.assertEqual(len(set(sizes)), 1, f"bbox forms disagreed: {sizes}")

    def test_an_invalid_rendering_rule_raises_valueerror(self):
        """A 200 response carrying a JSON error body, sniffed from the bytes."""
        with self.assertRaises(ValueError):
            S.exportImage(IMG, bbox=AOI, size=(32, 32),
                          rendering_rule={"rasterFunction": "NoSuchFunction"}, timeout=SLOW)

    def test_supported_operations_include_the_ones_this_module_calls(self):
        ops = {o["operation"] for o in retry(lambda: S.getSupportedOperations(IMG))}
        for expected in ("exportImage", "identify", "getSamples",
                         "computeStatisticsHistograms", "queryBoundary"):
            self.assertIn(expected, ops)

    def test_supported_operations_beat_the_capabilities_string(self):
        """The reason this function parses HTML instead of reading JSON."""
        info = retry(lambda: S.getLayerInfo(IMG))
        ops = {o["operation"] for o in retry(lambda: S.getSupportedOperations(IMG))}
        self.assertIn("computeStatisticsHistograms", ops)
        self.assertNotIn("computeStatisticsHistograms",
                         [c.lower() for c in info["capabilities"]])

    def test_statistics_histograms_over_the_aoi(self):
        stats = retry(lambda: S.computeStatisticsHistograms(
            IMG, geometry=AOI, geometry_type="esriGeometryEnvelope",
            mosaic_rule=YEAR_2020, timeout=SLOW))
        self.assertTrue(stats, "expected at least one band over a covered AOI")
        band = stats[0]
        self.assertEqual(band["band"], 0)
        self.assertLessEqual(band["min"], band["max"])
        self.assertGreater(band["count"], 0)
        self.assertEqual(len(band["histogram"]["counts"]), band["histogram"]["size"])

    def test_a_coarser_pixel_size_samples_fewer_pixels(self):
        native = retry(lambda: S.computeStatisticsHistograms(
            IMG, geometry=AOI, geometry_type="esriGeometryEnvelope",
            mosaic_rule=YEAR_2020, timeout=SLOW))
        coarse = retry(lambda: S.computeStatisticsHistograms(
            IMG, geometry=AOI, geometry_type="esriGeometryEnvelope", mosaic_rule=YEAR_2020,
            pixel_size={"x": 0.001, "y": 0.001, "spatialReference": {"wkid": 4326}},
            timeout=SLOW))
        self.assertTrue(coarse)
        self.assertLess(coarse[0]["count"], native[0]["count"])

    def test_an_absurd_pixel_size_returns_empty_rather_than_erroring(self):
        """90 *degrees*, not 90 m — the documented empty-list case."""
        got = retry(lambda: S.computeStatisticsHistograms(
            IMG, geometry=AOI, geometry_type="esriGeometryEnvelope", mosaic_rule=YEAR_2020,
            pixel_size={"x": 90, "y": 90, "spatialReference": {"wkid": 4326}}, timeout=SLOW))
        self.assertEqual(got, [])

    def test_a_geojson_polygon_is_accepted_as_the_area(self):
        polygon = {"type": "Polygon", "coordinates": [[
            [-118.65, 36.05], [-118.45, 36.05], [-118.45, 36.25],
            [-118.65, 36.25], [-118.65, 36.05]]]}
        stats = retry(lambda: S.computeStatisticsHistograms(
            IMG, geometry=polygon, geometry_type="esriGeometryPolygon",
            mosaic_rule=YEAR_2020, timeout=SLOW))
        self.assertTrue(stats)

    def test_identify_returns_a_value_inside_coverage(self):
        got = retry(lambda: S.identifyPixelValue(IMG, *IN_AOI, mosaic_rule=YEAR_2020,
                                                 timeout=SLOW))
        self.assertNotEqual(got["value"], "NoData")
        self.assertTrue(got["band_values"])
        self.assertIsNone(got["catalog_items"])

    def test_identify_location_comes_back_in_the_services_native_sr(self):
        """Documented gotcha: not echoed back in in_sr."""
        got = retry(lambda: S.identifyPixelValue(IMG, *IN_AOI, mosaic_rule=YEAR_2020,
                                                 timeout=SLOW))
        wkid = got["location"]["spatialReference"]["wkid"]
        self.assertNotEqual(wkid, 4326)
        self.assertGreater(abs(got["location"]["x"]), 180,
                           "Web Mercator metres, not the degrees that were sent")

    def test_identify_reports_nodata_outside_coverage(self):
        got = retry(lambda: S.identifyPixelValue(IMG, *OFF_COVERAGE,
                                                 mosaic_rule=YEAR_2020, timeout=SLOW))
        self.assertEqual(got["value"], "NoData")
        self.assertEqual(got["band_values"], [], "empty band_values at NoData")

    def test_catalog_items_name_the_contributing_raster(self):
        got = retry(lambda: S.identifyPixelValue(
            IMG, *IN_AOI, mosaic_rule=YEAR_2020, return_catalog_items=True, timeout=SLOW))
        self.assertTrue(got["catalog_items"])
        self.assertEqual(got["catalog_items"][0]["attributes"]["year"], 2020,
                         "the mosaic rule must scope which raster contributes")

    def test_get_samples_returns_one_entry_per_point_in_order(self):
        points = [IN_AOI, (-118.60, 36.10), (-118.50, 36.20)]
        got = retry(lambda: S.getSamples(IMG, points, mosaic_rule=YEAR_2020, timeout=SLOW))
        self.assertEqual(len(got), len(points))
        for (x, y), entry in zip(points, got):
            self.assertAlmostEqual(entry["x"], x, places=4)
            self.assertAlmostEqual(entry["y"], y, places=4)
            self.assertIn(entry["source"], ("getSamples", "identify"))

    def test_get_samples_agrees_with_identify_at_the_same_point(self):
        sampled = retry(lambda: S.getSamples(IMG, [IN_AOI], mosaic_rule=YEAR_2020,
                                             timeout=SLOW))[0]
        identified = retry(lambda: S.identifyPixelValue(IMG, *IN_AOI,
                                                        mosaic_rule=YEAR_2020, timeout=SLOW))
        self.assertEqual(sampled["value"], identified["value"])

    def test_an_off_coverage_point_does_not_cost_the_others_their_values(self):
        """The per-point fallback's whole reason for existing."""
        points = [IN_AOI, OFF_COVERAGE, (-118.50, 36.20)]
        got = retry(lambda: S.getSamples(IMG, points, mosaic_rule=YEAR_2020, timeout=SLOW))
        self.assertEqual(len(got), 3)
        self.assertNotIn(got[0]["value"], (None, "NoData"))
        self.assertNotIn(got[2]["value"], (None, "NoData"))

    def test_get_samples_never_raises_for_an_unreachable_service(self):
        """Failures are reported in-band, per point."""
        got = S.getSamples("https://no-such-host-here-xyz.invalid/x/ImageServer",
                           [IN_AOI, OFF_COVERAGE], timeout=5)
        self.assertEqual(len(got), 2)
        for entry in got:
            self.assertIsNone(entry["value"])
            self.assertTrue(entry["source"].startswith("error: "))

    def test_boundary_is_a_polygon_with_an_area(self):
        boundary = retry(lambda: S.queryBoundary(IMG, timeout=SLOW))
        self.assertEqual(boundary["type"], "Polygon")
        self.assertGreater(boundary["area"], 0)
        self.assertGreater(len(boundary["coordinates"][0]), 3)

    def test_boundary_coordinates_follow_out_sr_but_area_does_not(self):
        """Documented gotcha: area stays in the service's storage SR."""
        wgs84 = retry(lambda: S.queryBoundary(IMG, out_sr=4326, timeout=SLOW))
        mercator = retry(lambda: S.queryBoundary(IMG, out_sr=3857, timeout=SLOW))
        self.assertLessEqual(abs(wgs84["coordinates"][0][0][0]), 180)
        self.assertGreater(abs(mercator["coordinates"][0][0][0]), 180)
        self.assertEqual(wgs84["area"], mercator["area"])

    def test_the_aoi_lies_inside_the_reported_boundary_extent(self):
        """queryBoundary as the pre-flight coverage check it is meant to be."""
        boundary = retry(lambda: S.queryBoundary(IMG, timeout=SLOW))
        xs = [pt[0] for ring in boundary["coordinates"] for pt in ring]
        ys = [pt[1] for ring in boundary["coordinates"] for pt in ring]
        self.assertTrue(min(xs) <= IN_AOI[0] <= max(xs))
        self.assertTrue(min(ys) <= IN_AOI[1] <= max(ys))


if __name__ == "__main__":
    unittest.main()
