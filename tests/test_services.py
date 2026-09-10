"""Offline tests for `georest/restesri/services.py` — every public function.

Deterministic and network-free: the HTTP helpers are replaced on the
`services` module itself (see `support.patched_services`). The canned
responses here are trimmed copies of real payloads captured from the live
services the module targets, so the shapes being asserted are the shapes
ArcGIS actually returns.

Live counterparts live in `test_live_services.py`.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from georest.restesri import services as S

from .support import RecordingFetch, patched_services

IMG = "https://example.gov/arcgis/rest/services/Thing/ImageServer"
FEAT = "https://example.com/arcgis/rest/services/Thing/FeatureServer/0"


# ---------------------------------------------------------------------------
# getImageServiceTileUrl
# ---------------------------------------------------------------------------
class TileUrlTests(unittest.TestCase):

    def test_uses_esri_zyx_order_not_xyz(self):
        """ArcGIS puts y before x — the whole point of the helper."""
        self.assertEqual(
            S.getImageServiceTileUrl(IMG),
            f"{IMG}/tile/{{z}}/{{y}}/{{x}}",
        )

    def test_trailing_slash_is_stripped(self):
        self.assertEqual(
            S.getImageServiceTileUrl(IMG + "/"),
            f"{IMG}/tile/{{z}}/{{y}}/{{x}}",
        )

    def test_token_is_appended_and_url_encoded(self):
        url = S.getImageServiceTileUrl(IMG, token="a b/c")
        self.assertTrue(url.endswith("?token=a%20b%2Fc"))

    def test_accepts_a_portal_result_dict(self):
        self.assertEqual(
            S.getImageServiceTileUrl({"url": IMG}),
            f"{IMG}/tile/{{z}}/{{y}}/{{x}}",
        )

    def test_rejects_a_non_url_type(self):
        with self.assertRaises(TypeError):
            S.getImageServiceTileUrl(123)

    def test_rejects_a_dict_with_no_url_key(self):
        with self.assertRaises(ValueError):
            S.getImageServiceTileUrl({"title": "no url here"})


# ---------------------------------------------------------------------------
# geometry conversion helpers
# ---------------------------------------------------------------------------
class ConvertGeometryTests(unittest.TestCase):

    def test_bbox_string_becomes_an_esri_envelope(self):
        got = json.loads(S._convert_geometry("-125,32,-114,42", "esriGeometryEnvelope"))
        self.assertEqual(got, {"xmin": -125.0, "ymin": 32.0, "xmax": -114.0, "ymax": 42.0})

    def test_bbox_string_tolerates_whitespace(self):
        got = json.loads(S._convert_geometry(" -125 , 32 , -114 , 42 ", "esriGeometryEnvelope"))
        self.assertEqual(got["xmin"], -125.0)

    def test_four_part_non_numeric_string_passes_through(self):
        """Not every 4-comma string is a bbox; a non-numeric one is left alone."""
        self.assertEqual(S._convert_geometry("a,b,c,d", "esriGeometryEnvelope"), "a,b,c,d")

    def test_geojson_point_becomes_esri_xy(self):
        got = json.loads(S._convert_geometry({"type": "Point", "coordinates": [-118.5, 36.1]}, "esriGeometryPoint"))
        self.assertEqual(got, {"x": -118.5, "y": 36.1})

    def test_geojson_polygon_becomes_esri_rings(self):
        ring = [[[0, 0], [1, 0], [1, 1], [0, 0]]]
        got = json.loads(S._convert_geometry({"type": "Polygon", "coordinates": ring}, "esriGeometryPolygon"))
        self.assertEqual(got, {"rings": ring})

    def test_geojson_multipolygon_flattens_every_part_into_rings(self):
        a = [[0, 0], [1, 0], [1, 1], [0, 0]]
        b = [[5, 5], [6, 5], [6, 6], [5, 5]]
        got = json.loads(S._convert_geometry(
            {"type": "MultiPolygon", "coordinates": [[a], [b]]}, "esriGeometryPolygon"))
        self.assertEqual(got, {"rings": [a, b]})

    def test_esri_json_dicts_pass_through_untouched(self):
        for geom in ({"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                     {"rings": [[[0, 0], [1, 1], [0, 0]]]},
                     {"x": 1, "y": 2}):
            self.assertEqual(json.loads(S._convert_geometry(geom, "esriGeometryPolygon")), geom)


class EsriToGeoJsonTests(unittest.TestCase):

    def test_point(self):
        self.assertEqual(
            S._esri_geometry_to_geojson({"x": 1, "y": 2}),
            {"type": "Point", "coordinates": [1, 2]},
        )

    def test_multipoint(self):
        self.assertEqual(
            S._esri_geometry_to_geojson({"points": [[1, 2], [3, 4]]}),
            {"type": "MultiPoint", "coordinates": [[1, 2], [3, 4]]},
        )

    def test_single_path_is_a_linestring(self):
        self.assertEqual(
            S._esri_geometry_to_geojson({"paths": [[[0, 0], [1, 1]]]}),
            {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        )

    def test_multiple_paths_are_a_multilinestring(self):
        paths = [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]
        self.assertEqual(
            S._esri_geometry_to_geojson({"paths": paths}),
            {"type": "MultiLineString", "coordinates": paths},
        )

    def test_rings_are_a_flat_polygon(self):
        """Documented limitation: no hole/multipart detection."""
        rings = [[[0, 0], [9, 0], [9, 9], [0, 0]], [[1, 1], [2, 1], [2, 2], [1, 1]]]
        self.assertEqual(
            S._esri_geometry_to_geojson({"rings": rings}),
            {"type": "Polygon", "coordinates": rings},
        )

    def test_empty_and_unrecognised_geometry_are_none(self):
        self.assertIsNone(S._esri_geometry_to_geojson(None))
        self.assertIsNone(S._esri_geometry_to_geojson({}))
        self.assertIsNone(S._esri_geometry_to_geojson({"curveRings": []}))

    def test_feature_set_conversion(self):
        got = S._esri_json_to_geojson({
            "features": [{"attributes": {"OID": 7}, "geometry": {"x": 1, "y": 2}}]
        })
        self.assertEqual(got["type"], "FeatureCollection")
        self.assertEqual(got["features"][0]["properties"], {"OID": 7})
        self.assertEqual(got["features"][0]["geometry"]["type"], "Point")

    def test_feature_set_without_features_key(self):
        self.assertEqual(S._esri_json_to_geojson({}), {"type": "FeatureCollection", "features": []})


class SanitizeGeoJsonTests(unittest.TestCase):

    def test_dotted_property_names_are_dropped(self):
        got = S._sanitize_geojson({"features": [{"properties": {"SHAPE.LEN": 1, "OK": 2}}]})
        self.assertEqual(got["features"][0]["properties"], {"OK": 2})

    def test_ids_are_assigned_as_positional_strings(self):
        got = S._sanitize_geojson({"features": [{"properties": {}}, {"properties": {}}]})
        self.assertEqual([f["id"] for f in got["features"]], ["0", "1"])


class FormatEsriErrorTests(unittest.TestCase):

    def test_code_and_message(self):
        self.assertEqual(S._format_esri_error({"code": 400, "message": "Bad"}), "400 — Bad")

    def test_details_are_appended_because_message_alone_misleads(self):
        got = S._format_esri_error({
            "code": 400, "message": "Invalid or missing input parameters",
            "details": ["The requested image exceeds the size limit."],
        })
        self.assertIn("exceeds the size limit", got)


class BboxParamTests(unittest.TestCase):

    def test_string_passes_through(self):
        self.assertEqual(S._bbox_param("0,1,2,3"), "0,1,2,3")

    def test_list_and_tuple(self):
        self.assertEqual(S._bbox_param([0, 1, 2, 3]), "0,1,2,3")
        self.assertEqual(S._bbox_param((0, 1, 2, 3)), "0,1,2,3")

    def test_dict(self):
        self.assertEqual(S._bbox_param({"xmin": 0, "ymin": 1, "xmax": 2, "ymax": 3}), "0,1,2,3")

    def test_dict_missing_a_key_names_the_requirement(self):
        with self.assertRaisesRegex(ValueError, "xmin/ymin/xmax/ymax"):
            S._bbox_param({"xmin": 0})

    def test_wrong_length_sequence_is_rejected(self):
        with self.assertRaises(ValueError):
            S._bbox_param([0, 1, 2])


# ---------------------------------------------------------------------------
# queryFeatureServiceCount
# ---------------------------------------------------------------------------
class CountTests(unittest.TestCase):

    def test_returns_the_count(self):
        fake = RecordingFetch({"count": 1470})
        with patched_services(fetch_json=fake):
            self.assertEqual(S.queryFeatureServiceCount(FEAT), 1470)
        self.assertEqual(fake.last_url, f"{FEAT}/query")
        self.assertEqual(fake.last_params["returnCountOnly"], "true")
        self.assertEqual(fake.last_params["where"], "1=1")

    def test_missing_count_key_is_zero(self):
        with patched_services(fetch_json=RecordingFetch({})):
            self.assertEqual(S.queryFeatureServiceCount(FEAT), 0)

    def test_featureserver_root_gets_layer_zero_appended(self):
        fake = RecordingFetch({"count": 1})
        root = "https://example.com/arcgis/rest/services/Thing/FeatureServer"
        with patched_services(fetch_json=fake):
            S.queryFeatureServiceCount(root)
        self.assertEqual(fake.last_url, f"{root}/0/query")

    def test_spatial_filter_sends_geometry_type_rel_and_insr(self):
        fake = RecordingFetch({"count": 42})
        with patched_services(fetch_json=fake):
            S.queryFeatureServiceCount(FEAT, geometry="-125,32,-114,42", in_sr=3857)
        p = fake.last_params
        self.assertEqual(json.loads(p["geometry"])["xmin"], -125.0)
        self.assertEqual(p["geometryType"], "esriGeometryEnvelope")
        self.assertEqual(p["spatialRel"], "esriSpatialRelIntersects")
        self.assertEqual(p["inSR"], "3857")

    def test_no_geometry_means_no_spatial_params_at_all(self):
        fake = RecordingFetch({"count": 1})
        with patched_services(fetch_json=fake):
            S.queryFeatureServiceCount(FEAT)
        for key in ("geometry", "geometryType", "spatialRel", "inSR"):
            self.assertNotIn(key, fake.last_params)

    def test_token_is_forwarded(self):
        fake = RecordingFetch({"count": 1})
        with patched_services(fetch_json=fake):
            S.queryFeatureServiceCount(FEAT, token="tok")
        self.assertEqual(fake.last_params["token"], "tok")

    def test_esri_error_body_becomes_valueerror(self):
        err = {"error": {"code": 400, "message": "Unable to complete operation."}}
        with patched_services(fetch_json=RecordingFetch(err)):
            with self.assertRaisesRegex(ValueError, "400"):
                S.queryFeatureServiceCount(FEAT, where="nosuchfield=1")

    def test_transport_failure_is_a_runtimeerror_naming_the_url(self):
        """_http converts every HTTP/URL failure to RuntimeError; so do we."""
        with patched_services(fetch_json=RecordingFetch(RuntimeError("Request error: boom"))):
            with self.assertRaises(RuntimeError) as ctx:
                S.queryFeatureServiceCount(FEAT)
        self.assertIn("/query", str(ctx.exception))


# ---------------------------------------------------------------------------
# queryFeatureService
# ---------------------------------------------------------------------------
class QueryFeatureServiceTests(unittest.TestCase):

    GEOJSON = {"type": "FeatureCollection",
               "features": [{"type": "Feature", "id": 20,
                             "properties": {"OBJECTID": 20}, "geometry": None}]}

    def test_happy_path_returns_the_server_geojson_verbatim(self):
        fake = RecordingFetch({"count": 1}, self.GEOJSON)
        with patched_services(fetch_json=fake):
            got = S.queryFeatureService(FEAT)
        self.assertEqual(got, self.GEOJSON)
        self.assertEqual(fake.calls[1][1]["f"], "geojson")

    def test_count_preflight_runs_before_any_geometry_is_fetched(self):
        fake = RecordingFetch({"count": 1249})
        with patched_services(fetch_json=fake):
            with self.assertRaisesRegex(ValueError, r"1,249 matching features"):
                S.queryFeatureService(FEAT, where="magnitude>=5", max_features=50)
        self.assertEqual(len(fake.calls), 1, "must not fetch geometry after the cap trips")

    def test_the_cap_message_suggests_a_remedy(self):
        with patched_services(fetch_json=RecordingFetch({"count": 10})):
            with self.assertRaisesRegex(ValueError, "Increase max_features"):
                S.queryFeatureService(FEAT, max_features=1)

    def test_count_equal_to_max_features_is_allowed(self):
        """The cap is 'exceeds', so an exactly-full result must still fetch."""
        fake = RecordingFetch({"count": 5}, self.GEOJSON)
        with patched_services(fetch_json=fake):
            S.queryFeatureService(FEAT, max_features=5)
        self.assertEqual(len(fake.calls), 2)

    def test_preflight_uses_out_sr_as_the_geometry_input_sr(self):
        fake = RecordingFetch({"count": 1}, self.GEOJSON)
        with patched_services(fetch_json=fake):
            S.queryFeatureService(FEAT, geometry="0,0,1,1", out_sr=3857)
        self.assertEqual(fake.calls[0][1]["inSR"], "3857")
        self.assertEqual(fake.calls[1][1]["inSR"], "3857")
        self.assertEqual(fake.calls[1][1]["outSR"], "3857")

    def test_out_fields_are_forwarded(self):
        fake = RecordingFetch({"count": 1}, self.GEOJSON)
        with patched_services(fetch_json=fake):
            S.queryFeatureService(FEAT, out_fields="name,magnitude")
        self.assertEqual(fake.calls[1][1]["outFields"], "name,magnitude")

    def test_geojson_rejection_falls_back_to_esri_json(self):
        """An ImageServer's raster-catalog /query rejects f=geojson outright."""
        esri = {"features": [{"attributes": {"objectid": 76, "SHAPE.LEN": 1},
                              "geometry": {"rings": [[[0, 0], [1, 1], [0, 0]]]}}]}
        fake = RecordingFetch({"count": 1}, RuntimeError("Request failed (400)"), esri)
        with patched_services(fetch_json=fake):
            got = S.queryFeatureService(IMG, where="year=2020")
        self.assertEqual(fake.calls[2][1]["f"], "json", "fallback must re-ask as f=json")
        self.assertEqual(got["features"][0]["geometry"]["type"], "Polygon")

    def test_fallback_path_sanitises_and_renumbers_ids(self):
        """The two response paths differ here — the docstring documents it."""
        esri = {"features": [{"attributes": {"objectid": 76, "SHAPE.LEN": 1}, "geometry": None}]}
        fake = RecordingFetch({"count": 1}, RuntimeError("400"), esri)
        with patched_services(fetch_json=fake):
            got = S.queryFeatureService(IMG)
        feature = got["features"][0]
        self.assertNotIn("SHAPE.LEN", feature["properties"], "dotted keys are dropped")
        self.assertEqual(feature["id"], "0", "ids are positional strings on this path")

    def test_fallback_that_also_fails_raises_runtimeerror_naming_the_url(self):
        fake = RecordingFetch({"count": 1}, RuntimeError("boom"), RuntimeError("boom again"))
        with patched_services(fetch_json=fake):
            with self.assertRaises(RuntimeError) as ctx:
                S.queryFeatureService(FEAT)
        self.assertIn("/query", str(ctx.exception))

    def test_error_body_on_the_geojson_path_becomes_valueerror(self):
        fake = RecordingFetch({"count": 1}, {"error": {"code": 400, "message": "nope"}})
        with patched_services(fetch_json=fake):
            with self.assertRaisesRegex(ValueError, "400"):
                S.queryFeatureService(FEAT)

    def test_error_body_on_the_fallback_path_becomes_valueerror(self):
        fake = RecordingFetch({"count": 1}, RuntimeError("400"),
                              {"error": {"code": 500, "message": "nope"}})
        with patched_services(fetch_json=fake):
            with self.assertRaisesRegex(ValueError, "500"):
                S.queryFeatureService(FEAT)


# ---------------------------------------------------------------------------
# getLayerInfo
# ---------------------------------------------------------------------------
class LayerInfoTests(unittest.TestCase):

    LAYER = {
        "name": "Earthquakes1970",
        "geometryType": "esriGeometryPoint",
        "description": "quakes",
        "fields": [{"name": "magnitude", "type": "esriFieldTypeDouble", "alias": "Magnitude"}],
        "extent": {"xmin": -180},
        "maxRecordCount": 1000,
        "capabilities": "Query,Map",
        "supportedQueryFormats": "JSON",
    }

    def test_normalises_fields_and_capabilities(self):
        with patched_services(fetch_json=RecordingFetch(self.LAYER)):
            got = S.getLayerInfo(FEAT)
        self.assertEqual(got["fields"], [{"name": "magnitude", "type": "Double", "alias": "Magnitude"}])
        self.assertEqual(got["capabilities"], ["Query", "Map"])
        self.assertEqual(got["geometryType"], "esriGeometryPoint")

    def test_missing_max_record_count_falls_back_to_the_esri_default(self):
        payload = {k: v for k, v in self.LAYER.items() if k != "maxRecordCount"}
        with patched_services(fetch_json=RecordingFetch(payload)):
            self.assertEqual(S.getLayerInfo(FEAT)["maxRecordCount"], S._MAX_RECORD_COUNT)

    def test_null_fields_do_not_crash(self):
        with patched_services(fetch_json=RecordingFetch({"name": "x", "fields": None})):
            self.assertEqual(S.getLayerInfo(FEAT)["fields"], [])

    def test_group_layer_is_rejected_with_its_sublayers_named(self):
        payload = {"name": "Group", "type": "Group Layer",
                   "subLayers": [{"id": 161, "name": "Detail"}, {"id": 162, "name": "Coarse"}]}
        with patched_services(fetch_json=RecordingFetch(payload)):
            with self.assertRaises(ValueError) as ctx:
                S.getLayerInfo(FEAT)
        self.assertIn("Detail (161)", str(ctx.exception))
        self.assertIn("Coarse (162)", str(ctx.exception))

    def test_service_root_is_rejected_rather_than_returning_hollow_metadata(self):
        """A MapServer/FeatureServer root has `layers` and no `fields`."""
        payload = {"layers": [{"id": 0, "name": "states"}], "description": "a service"}
        with patched_services(fetch_json=RecordingFetch(payload)):
            with self.assertRaises(ValueError) as ctx:
                S.getLayerInfo("https://example.com/x/MapServer")
        self.assertIn("states (0)", str(ctx.exception))

    def test_imageserver_root_is_still_a_valid_target(self):
        """It carries real catalog fields and no `layers` — must not be rejected."""
        payload = {"name": "Veg/LCMS", "fields": [{"name": "objectid", "type": "esriFieldTypeOID"}],
                   "capabilities": "Image,Catalog"}
        with patched_services(fetch_json=RecordingFetch(payload)):
            got = S.getLayerInfo(IMG)
        self.assertEqual([f["name"] for f in got["fields"]], ["objectid"])
        self.assertEqual(got["geometryType"], "", "a service root has no single geometry type")

    def test_error_body_becomes_valueerror(self):
        with patched_services(fetch_json=RecordingFetch({"error": {"code": 404, "message": "not found"}})):
            with self.assertRaisesRegex(ValueError, "404"):
                S.getLayerInfo(FEAT)

    def test_transport_failure_is_a_runtimeerror(self):
        with patched_services(fetch_json=RecordingFetch(RuntimeError("boom"))):
            with self.assertRaises(RuntimeError):
                S.getLayerInfo(FEAT)


# ---------------------------------------------------------------------------
# exportImage
# ---------------------------------------------------------------------------
class _FakeBytes:
    def __init__(self, payload, content_type="image/tiff"):
        self.payload, self.content_type = payload, content_type
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload, self.content_type


class ExportImageTests(unittest.TestCase):

    TIFF = b"II*\x00rest-of-a-tiff"

    def test_builds_the_expected_parameter_set(self):
        fake = _FakeBytes(self.TIFF)
        with patched_services(fetch_bytes=fake):
            S.exportImage(IMG, bbox="-1,-2,3,4", size=(64, 32))
        url, p = fake.calls[0]
        self.assertEqual(url, f"{IMG}/exportImage")
        self.assertEqual(p["bbox"], "-1,-2,3,4")
        self.assertEqual(p["size"], "64,32")
        self.assertEqual(p["f"], "image")
        self.assertEqual(p["format"], "tiff")
        self.assertEqual(p["interpolation"], "RSP_BilinearInterpolation")

    def test_image_sr_defaults_to_the_bbox_sr(self):
        fake = _FakeBytes(self.TIFF)
        with patched_services(fetch_bytes=fake):
            S.exportImage(IMG, bbox="0,0,1,1", bbox_sr=3857)
        self.assertEqual(fake.calls[0][1]["imageSR"], "3857")

    def test_explicit_out_sr_overrides_it(self):
        fake = _FakeBytes(self.TIFF)
        with patched_services(fetch_bytes=fake):
            S.exportImage(IMG, bbox="0,0,1,1", bbox_sr=3857, out_sr=4326)
        self.assertEqual(fake.calls[0][1]["imageSR"], "4326")

    def test_rules_are_json_encoded(self):
        fake = _FakeBytes(self.TIFF)
        with patched_services(fetch_bytes=fake):
            S.exportImage(IMG, bbox="0,0,1,1",
                          mosaic_rule={"where": "year=2020"},
                          rendering_rule={"rasterFunction": "Annual_Change"})
        p = fake.calls[0][1]
        self.assertEqual(json.loads(p["mosaicRule"]), {"where": "year=2020"})
        self.assertEqual(json.loads(p["renderingRule"]), {"rasterFunction": "Annual_Change"})

    def test_optional_params_are_omitted_when_not_given(self):
        fake = _FakeBytes(self.TIFF)
        with patched_services(fetch_bytes=fake):
            S.exportImage(IMG, bbox="0,0,1,1")
        for key in ("mosaicRule", "renderingRule", "pixelType", "noData", "token"):
            self.assertNotIn(key, fake.calls[0][1])

    def test_no_data_accepts_a_scalar_or_a_per_band_list(self):
        fake = _FakeBytes(self.TIFF)
        with patched_services(fetch_bytes=fake):
            S.exportImage(IMG, bbox="0,0,1,1", no_data=0)
            S.exportImage(IMG, bbox="0,0,1,1", no_data=[0, 255])
        self.assertEqual(fake.calls[0][1]["noData"], "0")
        self.assertEqual(fake.calls[1][1]["noData"], "0,255")

    def test_returns_raw_bytes(self):
        with patched_services(fetch_bytes=_FakeBytes(self.TIFF)):
            self.assertEqual(S.exportImage(IMG, bbox="0,0,1,1"), self.TIFF)

    def test_out_path_writes_the_file_and_returns_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "out.tif")
            with patched_services(fetch_bytes=_FakeBytes(self.TIFF)):
                got = S.exportImage(IMG, bbox="0,0,1,1", out_path=dest)
            self.assertEqual(got, dest)
            with open(dest, "rb") as handle:
                self.assertEqual(handle.read(), self.TIFF)

    def test_json_error_body_is_detected_by_sniffing_the_bytes(self):
        """Content-Type is unreliable here — an error body arrived as image/tiff."""
        body = json.dumps({"error": {"code": 400, "message": "Invalid or missing input parameters",
                                     "details": ["The requested image exceeds the size limit."]}}).encode()
        with patched_services(fetch_bytes=_FakeBytes(body, content_type="image/tiff")):
            with self.assertRaises(ValueError) as ctx:
                S.exportImage(IMG, bbox="0,0,1,1")
        self.assertIn("exceeds the size limit", str(ctx.exception))

    def test_leading_whitespace_before_the_json_error_is_tolerated(self):
        body = b"\n  " + json.dumps({"error": {"code": 400, "message": "nope"}}).encode()
        with patched_services(fetch_bytes=_FakeBytes(body)):
            with self.assertRaises(ValueError):
                S.exportImage(IMG, bbox="0,0,1,1")

    def test_non_json_non_image_body_is_reported_verbatim(self):
        with patched_services(fetch_bytes=_FakeBytes(b"{not really json")):
            with self.assertRaisesRegex(ValueError, "unexpected content"):
                S.exportImage(IMG, bbox="0,0,1,1")

    def test_transport_failure_is_a_runtimeerror(self):
        with patched_services(fetch_bytes=_FakeBytes(RuntimeError("Request failed (500)"))):
            with self.assertRaises(RuntimeError):
                S.exportImage(IMG, bbox="0,0,1,1")


# ---------------------------------------------------------------------------
# getSupportedOperations
# ---------------------------------------------------------------------------
class SupportedOperationsTests(unittest.TestCase):

    PAGE = (
        "<b>Supported Operations</b>: "
        '<a href="/arcgis/rest/services/Thing/ImageServer/exportImage">Export Image</a> '
        '<a href="/arcgis/rest/services/Thing/ImageServer/computeStatisticsHistograms">'
        "Compute Statistics Histograms</a> "
        "<b>Child Resources</b>: <a href=\"/elsewhere\">Info</a>"
    )

    def test_parses_operation_links_into_name_operation_url(self):
        with patched_services(fetch_text=lambda url, params=None, timeout=None: self.PAGE):
            ops = S.getSupportedOperations(IMG)
        self.assertEqual(
            ops[0],
            {"name": "Export Image", "operation": "exportImage", "url": f"{IMG}/exportImage"},
        )
        self.assertEqual([o["operation"] for o in ops],
                         ["exportImage", "computeStatisticsHistograms"])

    def test_stops_at_the_next_bold_section(self):
        with patched_services(fetch_text=lambda url, params=None, timeout=None: self.PAGE):
            ops = S.getSupportedOperations(IMG)
        self.assertNotIn("Info", [o["name"] for o in ops])

    def test_html_entities_in_names_are_unescaped(self):
        page = ('<b>Supported Operations</b>: '
                '<a href="/x/FeatureServer/0/query">Query &amp; Filter</a>')
        with patched_services(fetch_text=lambda url, params=None, timeout=None: page):
            self.assertEqual(S.getSupportedOperations(FEAT)[0]["name"], "Query & Filter")

    def test_query_string_flag_on_the_resource_itself_uses_the_flag_name(self):
        """"Return Updates" is a query-string flag, not a sub-resource."""
        page = ('<b>Supported Operations</b>: '
                '<a href="/arcgis/rest/services/Thing/FeatureServer/0?f=pjson&returnUpdates=true&">'
                "Return Updates</a>")
        with patched_services(fetch_text=lambda url, params=None, timeout=None: page):
            op = S.getSupportedOperations(FEAT)[0]
        self.assertEqual(op["operation"], "returnUpdates",
                         "must not fall back to the layer id from the path")
        self.assertTrue(op["url"].startswith(FEAT + "?"))

    def test_no_supported_operations_section_returns_empty(self):
        with patched_services(fetch_text=lambda url, params=None, timeout=None: "<html>nothing</html>"):
            self.assertEqual(S.getSupportedOperations(IMG), [])

    def test_transport_failure_is_a_runtimeerror(self):
        def boom(url, params=None, timeout=None):
            raise RuntimeError("Request failed (404)")
        with patched_services(fetch_text=boom):
            with self.assertRaises(RuntimeError):
                S.getSupportedOperations(IMG)


# ---------------------------------------------------------------------------
# computeStatisticsHistograms
# ---------------------------------------------------------------------------
class StatisticsHistogramsTests(unittest.TestCase):

    RESPONSE = {
        "statistics": [{"min": 4, "max": 15, "mean": 14.0, "standardDeviation": 1.36,
                        "sum": 9557392.0, "median": 15, "mode": 15, "count": 681898}],
        "histograms": [{"min": -0.5, "max": 15.5, "size": 16, "counts": [0] * 16}],
    }

    def test_merges_statistics_and_histogram_per_band(self):
        with patched_services(fetch_json=RecordingFetch(self.RESPONSE)):
            got = S.computeStatisticsHistograms(IMG, geometry="0,0,1,1")
        self.assertEqual(len(got), 1)
        band = got[0]
        self.assertEqual(band["band"], 0)
        self.assertEqual(band["stddev"], 1.36, "standardDeviation is renamed to stddev")
        self.assertEqual(band["sum"], 9557392.0)
        self.assertEqual(band["histogram"]["size"], 16)

    def test_spatial_reference_is_embedded_in_the_geometry_not_sent_as_insr(self):
        """A separate inSR param is silently ignored by this operation."""
        fake = RecordingFetch(self.RESPONSE)
        with patched_services(fetch_json=fake):
            S.computeStatisticsHistograms(IMG, geometry="0,0,1,1", in_sr=3857)
        p = fake.last_params
        self.assertEqual(json.loads(p["geometry"])["spatialReference"], {"wkid": 3857})
        self.assertNotIn("inSR", p)

    def test_a_spatial_reference_already_in_the_geometry_is_preserved(self):
        fake = RecordingFetch(self.RESPONSE)
        geom = {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1, "spatialReference": {"wkid": 26910}}
        with patched_services(fetch_json=fake):
            S.computeStatisticsHistograms(IMG, geometry=geom, in_sr=4326)
        self.assertEqual(json.loads(fake.last_params["geometry"])["spatialReference"], {"wkid": 26910})

    def test_pixel_size_and_rules_are_json_encoded(self):
        fake = RecordingFetch(self.RESPONSE)
        with patched_services(fetch_json=fake):
            S.computeStatisticsHistograms(
                IMG, geometry="0,0,1,1",
                mosaic_rule={"where": "year=2020"},
                rendering_rule={"rasterFunction": "None"},
                pixel_size={"x": 30, "y": 30})
        p = fake.last_params
        self.assertEqual(json.loads(p["pixelSize"]), {"x": 30, "y": 30})
        self.assertEqual(json.loads(p["mosaicRule"]), {"where": "year=2020"})
        self.assertEqual(json.loads(p["renderingRule"]), {"rasterFunction": "None"})

    def test_empty_result_is_an_empty_list_not_an_error(self):
        """What an absurdly coarse pixel_size produces — documented behaviour."""
        with patched_services(fetch_json=RecordingFetch({"statistics": [], "histograms": []})):
            self.assertEqual(S.computeStatisticsHistograms(IMG, geometry="0,0,1,1"), [])

    def test_null_statistics_do_not_crash(self):
        with patched_services(fetch_json=RecordingFetch({"statistics": None, "histograms": None})):
            self.assertEqual(S.computeStatisticsHistograms(IMG, geometry="0,0,1,1"), [])

    def test_ungeometrifiable_input_is_rejected_before_any_request(self):
        fake = RecordingFetch(self.RESPONSE)
        with patched_services(fetch_json=fake):
            with self.assertRaisesRegex(ValueError, "could not be converted"):
                S.computeStatisticsHistograms(IMG, geometry="not,a,bbox")
        self.assertEqual(fake.calls, [], "must not hit the network with bad geometry")

    def test_error_body_becomes_valueerror(self):
        with patched_services(fetch_json=RecordingFetch({"error": {"code": 400, "message": "nope"}})):
            with self.assertRaisesRegex(ValueError, "400"):
                S.computeStatisticsHistograms(IMG, geometry="0,0,1,1")


# ---------------------------------------------------------------------------
# identifyPixelValue
# ---------------------------------------------------------------------------
class IdentifyPixelValueTests(unittest.TestCase):

    RESPONSE = {
        "value": "12",
        "properties": {"Values": ["12"]},
        "location": {"x": -13196925.6, "y": 4321280.8, "spatialReference": {"wkid": 102100}},
        "catalogItems": {"features": [{"attributes": {"objectid": 76, "year": 2020}}]},
    }

    def test_returns_value_band_values_and_location(self):
        with patched_services(fetch_json=RecordingFetch(self.RESPONSE)):
            got = S.identifyPixelValue(IMG, x=-118.55, y=36.15)
        self.assertEqual(got["value"], "12")
        self.assertEqual(got["band_values"], ["12"])
        self.assertEqual(got["location"]["spatialReference"], {"wkid": 102100})

    def test_catalog_items_are_withheld_unless_asked_for(self):
        with patched_services(fetch_json=RecordingFetch(self.RESPONSE)):
            self.assertIsNone(S.identifyPixelValue(IMG, 0, 0)["catalog_items"])
            got = S.identifyPixelValue(IMG, 0, 0, return_catalog_items=True)
        self.assertEqual(got["catalog_items"][0]["attributes"]["year"], 2020)

    def test_return_catalog_items_is_always_true_on_the_wire(self):
        """Esri only populates properties.Values when it is sent."""
        fake = RecordingFetch(self.RESPONSE)
        with patched_services(fetch_json=fake):
            S.identifyPixelValue(IMG, 0, 0, return_catalog_items=False)
        self.assertEqual(fake.last_params["returnCatalogItems"], "true")

    def test_spatial_reference_is_embedded_in_the_point_geometry(self):
        fake = RecordingFetch(self.RESPONSE)
        with patched_services(fetch_json=fake):
            S.identifyPixelValue(IMG, x=1.5, y=2.5, in_sr=3857)
        geom = json.loads(fake.last_params["geometry"])
        self.assertEqual(geom, {"x": 1.5, "y": 2.5, "spatialReference": {"wkid": 3857}})
        self.assertEqual(fake.last_params["geometryType"], "esriGeometryPoint")
        self.assertNotIn("inSR", fake.last_params)

    def test_nodata_point_yields_empty_band_values(self):
        with patched_services(fetch_json=RecordingFetch({"value": "NoData", "properties": None})):
            got = S.identifyPixelValue(IMG, 0, 0)
        self.assertEqual(got["value"], "NoData")
        self.assertEqual(got["band_values"], [])

    def test_error_body_becomes_valueerror(self):
        with patched_services(fetch_json=RecordingFetch({"error": {"code": 400, "message": "nope"}})):
            with self.assertRaisesRegex(ValueError, "400"):
                S.identifyPixelValue(IMG, 0, 0)


# ---------------------------------------------------------------------------
# getSamples
# ---------------------------------------------------------------------------
class GetSamplesTests(unittest.TestCase):

    POINTS = [(-118.55, 36.15), (-118.60, 36.10)]

    @staticmethod
    def _batch(*ids):
        return {"samples": [{"locationId": i, "location": {"x": float(i), "y": float(i)},
                             "value": f"v{i}"} for i in ids]}

    def test_batch_success_returns_every_point_from_one_call(self):
        fake = RecordingFetch(self._batch(0, 1))
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual([r["source"] for r in got], ["getSamples", "getSamples"])
        self.assertEqual([r["value"] for r in got], ["v0", "v1"])
        self.assertEqual(len(fake.calls), 1, "no fallback requests when the batch is complete")

    def test_multipoint_geometry_carries_the_spatial_reference(self):
        fake = RecordingFetch(self._batch(0, 1))
        with patched_services(fetch_json=fake):
            S.getSamples(IMG, self.POINTS, in_sr=3857)
        geom = json.loads(fake.calls[0][1]["geometry"])
        self.assertEqual(geom["points"], [[-118.55, 36.15], [-118.60, 36.10]])
        self.assertEqual(geom["spatialReference"], {"wkid": 3857})
        self.assertEqual(fake.calls[0][1]["geometryType"], "esriGeometryMultipoint")

    def test_silently_dropped_points_fall_back_individually(self):
        """The batch can return fewer samples than points, without erroring."""
        identify = {"value": "fallback", "properties": {"Values": ["fallback"]}}
        fake = RecordingFetch(self._batch(0), identify)
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual([r["source"] for r in got], ["getSamples", "identify"])
        self.assertEqual(got[1]["value"], "fallback")
        self.assertEqual(len(fake.calls), 2, "only the missing point is retried")

    def test_fallback_reports_the_input_coordinates(self):
        identify = {"value": "x", "location": {"x": 999.0, "y": 999.0}}
        fake = RecordingFetch(self._batch(), identify)
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, [(-118.55, 36.15)])
        self.assertEqual((got[0]["x"], got[0]["y"]), (-118.55, 36.15),
                         "must not surface identify's native-SR location")

    def test_batch_error_body_sends_every_point_to_the_fallback(self):
        fake = RecordingFetch({"error": {"code": 400, "message": "nope"}},
                              {"value": "a"}, {"value": "b"})
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual([r["source"] for r in got], ["identify", "identify"])

    def test_batch_transport_failure_sends_every_point_to_the_fallback(self):
        fake = RecordingFetch(RuntimeError("Request failed (500)"), {"value": "a"}, {"value": "b"})
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual([r["value"] for r in got], ["a", "b"])

    def test_batch_non_json_body_sends_every_point_to_the_fallback(self):
        fake = RecordingFetch(ValueError("Expected JSON but got: <html>"), {"value": "a"}, {"value": "b"})
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual([r["source"] for r in got], ["identify", "identify"])

    def test_a_transport_failure_in_the_fallback_is_reported_per_point(self):
        """Regression: RuntimeError used to escape and destroy every result."""
        fake = RecordingFetch(RuntimeError("batch down"), RuntimeError("identify down too"))
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual(len(got), 2, "one entry per input point, even when all fail")
        for entry in got:
            self.assertIsNone(entry["value"])
            self.assertTrue(entry["source"].startswith("error: "))

    def test_one_bad_point_does_not_cost_the_others_their_results(self):
        fake = RecordingFetch(self._batch(0), RuntimeError("this point only"))
        with patched_services(fetch_json=fake):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual(got[0]["value"], "v0")
        self.assertIsNone(got[1]["value"])
        self.assertTrue(got[1]["source"].startswith("error: "))

    def test_results_stay_in_input_order(self):
        out_of_order = {"samples": [{"locationId": 1, "location": {}, "value": "second"},
                                    {"locationId": 0, "location": {}, "value": "first"}]}
        with patched_services(fetch_json=RecordingFetch(out_of_order)):
            got = S.getSamples(IMG, self.POINTS)
        self.assertEqual([r["value"] for r in got], ["first", "second"])

    def test_pixel_size_is_sent_on_the_batch_call(self):
        fake = RecordingFetch(self._batch(0, 1))
        with patched_services(fetch_json=fake):
            S.getSamples(IMG, self.POINTS, pixel_size={"x": 30, "y": 30})
        self.assertEqual(json.loads(fake.calls[0][1]["pixelSize"]), {"x": 30, "y": 30})


# ---------------------------------------------------------------------------
# queryBoundary
# ---------------------------------------------------------------------------
class QueryBoundaryTests(unittest.TestCase):

    RESPONSE = {"shape": {"rings": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                "area": 13499501822921.756}

    def test_returns_a_geojson_polygon_plus_area(self):
        with patched_services(fetch_json=RecordingFetch(self.RESPONSE)):
            got = S.queryBoundary(IMG)
        self.assertEqual(got["type"], "Polygon")
        self.assertEqual(got["coordinates"], self.RESPONSE["shape"]["rings"])
        self.assertEqual(got["area"], 13499501822921.756)

    def test_out_sr_is_sent_as_a_plain_param(self):
        """Unlike identify/getSamples, this operation honours outSR directly."""
        fake = RecordingFetch(self.RESPONSE)
        with patched_services(fetch_json=fake):
            S.queryBoundary(IMG, out_sr=3857)
        self.assertEqual(fake.last_params["outSR"], "3857")
        self.assertEqual(fake.last_url, f"{IMG}/queryBoundary")

    def test_mosaic_rule_is_json_encoded(self):
        fake = RecordingFetch(self.RESPONSE)
        with patched_services(fetch_json=fake):
            S.queryBoundary(IMG, mosaic_rule={"where": "year=2020"})
        self.assertEqual(json.loads(fake.last_params["mosaicRule"]), {"where": "year=2020"})

    def test_missing_shape_still_returns_the_area(self):
        with patched_services(fetch_json=RecordingFetch({"area": 5})):
            self.assertEqual(S.queryBoundary(IMG), {"area": 5})

    def test_error_body_becomes_valueerror(self):
        with patched_services(fetch_json=RecordingFetch({"error": {"code": 400, "message": "nope"}})):
            with self.assertRaisesRegex(ValueError, "400"):
                S.queryBoundary(IMG)


# ---------------------------------------------------------------------------
# module-wide contracts
# ---------------------------------------------------------------------------
PUBLIC_FUNCTIONS = [
    S.getImageServiceTileUrl, S.queryFeatureServiceCount, S.queryFeatureService,
    S.getLayerInfo, S.exportImage, S.getSupportedOperations,
    S.computeStatisticsHistograms, S.identifyPixelValue, S.getSamples,
    S.queryBoundary,
]


class DocstringContractTests(unittest.TestCase):
    """The docstrings are the module's spec, so guard the claims that rotted."""

    def test_every_public_function_is_documented(self):
        for fn in PUBLIC_FUNCTIONS:
            with self.subTest(fn=fn.__name__):
                self.assertTrue((fn.__doc__ or "").strip(), f"{fn.__name__} has no docstring")

    def test_no_docstring_promises_connectionerror(self):
        """It was documented on 8 functions but unreachable: _http raises
        RuntimeError for every HTTP/URL failure, so `except URLError` never
        fired and no caller could ever have caught ConnectionError."""
        offenders = [fn.__name__ for fn in PUBLIC_FUNCTIONS
                     if "ConnectionError" in (fn.__doc__ or "")]
        self.assertEqual(offenders, [])

    def test_functions_that_hit_the_network_document_runtimeerror(self):
        networked = [fn for fn in PUBLIC_FUNCTIONS
                     if fn not in (S.getImageServiceTileUrl, S.getSamples)]
        for fn in networked:
            with self.subTest(fn=fn.__name__):
                self.assertIn("RuntimeError", fn.__doc__)

    def test_getsamples_documents_that_it_does_not_raise_per_point(self):
        self.assertIn("does not raise", S.getSamples.__doc__)


if __name__ == "__main__":
    unittest.main()
