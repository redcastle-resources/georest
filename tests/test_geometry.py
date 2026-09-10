"""Geometry conversion in both directions, and GeoJSON sanitisation."""
import json
import unittest

from georest.restesri import edw

# Esri winds exterior rings clockwise (negative signed area) and holes
# counter-clockwise; GeoJSON (RFC 7946) is the opposite.
CW_OUTER_A = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
CCW_HOLE_A = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]
CW_OUTER_B = [[20, 0], [20, 5], [25, 5], [25, 0], [20, 0]]


class RingWinding(unittest.TestCase):
    def test_signed_area_sign_convention(self):
        self.assertLess(edw._ring_signed_area(CW_OUTER_A), 0)
        self.assertGreater(edw._ring_signed_area(CCW_HOLE_A), 0)

    def test_area_is_correct_magnitude(self):
        self.assertAlmostEqual(abs(edw._ring_signed_area(CW_OUTER_A)), 100.0)

    def test_unclosed_ring_still_measurable(self):
        # The shoelace wraps modulo length, so a missing closing vertex is fine.
        self.assertAlmostEqual(abs(edw._ring_signed_area(CW_OUTER_A[:-1])), 100.0)


class EsriRingsToGeoJSON(unittest.TestCase):
    """Esri packs multipart polygons and holes into one flat ring list."""

    def test_single_ring_is_a_polygon(self):
        got = edw._esri_rings_to_geojson([CW_OUTER_A])
        self.assertEqual(got["type"], "Polygon")
        self.assertEqual(len(got["coordinates"]), 1)

    def test_hole_nests_inside_its_parent(self):
        got = edw._esri_rings_to_geojson([CW_OUTER_A, CCW_HOLE_A])
        self.assertEqual(got["type"], "Polygon")
        self.assertEqual(len(got["coordinates"]), 2)

    def test_two_exterior_rings_become_separate_parts(self):
        # The bug this replaced turned Maui's 45 islands into 44 holes.
        got = edw._esri_rings_to_geojson([CW_OUTER_A, CW_OUTER_B])
        self.assertEqual(got["type"], "MultiPolygon")
        self.assertEqual([len(part) for part in got["coordinates"]], [1, 1])

    def test_hole_attaches_to_the_correct_part(self):
        got = edw._esri_rings_to_geojson([CW_OUTER_A, CCW_HOLE_A, CW_OUTER_B])
        self.assertEqual(got["type"], "MultiPolygon")
        self.assertEqual([len(part) for part in got["coordinates"]], [2, 1])

    def test_output_follows_the_right_hand_rule(self):
        got = edw._esri_rings_to_geojson([CW_OUTER_A, CCW_HOLE_A])
        exterior, hole = got["coordinates"]
        self.assertGreater(edw._ring_signed_area(exterior), 0, "exterior must be CCW")
        self.assertLess(edw._ring_signed_area(hole), 0, "hole must be CW")

    def test_orphan_leading_hole_is_promoted_not_dropped(self):
        got = edw._esri_rings_to_geojson([CCW_HOLE_A, CW_OUTER_A])
        self.assertEqual(got["type"], "MultiPolygon")
        self.assertEqual(len(got["coordinates"]), 2)

    def test_degenerate_ring_is_kept_as_exterior(self):
        got = edw._esri_rings_to_geojson([[[0, 0], [1, 1], [0, 0]]])
        self.assertIsNotNone(got)

    def test_empty_input_is_none(self):
        self.assertIsNone(edw._esri_rings_to_geojson([]))
        self.assertIsNone(edw._esri_rings_to_geojson([[]]))


class EsriGeometryToGeoJSON(unittest.TestCase):
    def test_point(self):
        self.assertEqual(edw._esri_geometry_to_geojson({"x": 1, "y": 2}),
                         {"type": "Point", "coordinates": [1, 2]})

    def test_single_path_is_a_linestring(self):
        got = edw._esri_geometry_to_geojson({"paths": [[[0, 0], [1, 1]]]})
        self.assertEqual(got["type"], "LineString")

    def test_multiple_paths_are_a_multilinestring(self):
        got = edw._esri_geometry_to_geojson({"paths": [[[0, 0]], [[1, 1]]]})
        self.assertEqual(got["type"], "MultiLineString")

    def test_multipoint(self):
        got = edw._esri_geometry_to_geojson({"points": [[0, 0], [1, 1]]})
        self.assertEqual(got["type"], "MultiPoint")

    def test_empty_geometry_is_none(self):
        self.assertIsNone(edw._esri_geometry_to_geojson(None))
        self.assertIsNone(edw._esri_geometry_to_geojson({}))


class ConvertGeometryToEsri(unittest.TestCase):
    def test_bbox_string_becomes_an_envelope(self):
        got = json.loads(edw._convert_geometry("-106,40.4,-105.2,40.9",
                                               "esriGeometryEnvelope"))
        self.assertEqual(got, {"xmin": -106.0, "ymin": 40.4,
                               "xmax": -105.2, "ymax": 40.9})

    def test_geojson_point(self):
        got = json.loads(edw._convert_geometry({"type": "Point",
                                                "coordinates": [1, 2]},
                                               "esriGeometryPoint"))
        self.assertEqual(got, {"x": 1, "y": 2})

    def test_geojson_polygon_becomes_rings(self):
        got = json.loads(edw._convert_geometry(
            {"type": "Polygon", "coordinates": [CW_OUTER_A]},
            "esriGeometryPolygon"))
        self.assertEqual(got, {"rings": [CW_OUTER_A]})

    def test_multipolygon_rings_are_flattened(self):
        got = json.loads(edw._convert_geometry(
            {"type": "MultiPolygon", "coordinates": [[CW_OUTER_A], [CW_OUTER_B]]},
            "esriGeometryPolygon"))
        self.assertEqual(got, {"rings": [CW_OUTER_A, CW_OUTER_B]})

    def test_esri_geometry_passes_through(self):
        for geom in ({"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                     {"rings": [CW_OUTER_A]}, {"x": 1, "y": 2}):
            with self.subTest(geom=geom):
                self.assertEqual(json.loads(edw._convert_geometry(geom, "")), geom)

    def test_non_bbox_string_passes_through(self):
        self.assertEqual(edw._convert_geometry("not,a,bbox,here", ""),
                         "not,a,bbox,here")


class SanitizeGeoJSON(unittest.TestCase):
    """Identity must survive: a positional index is not a stable id."""

    def test_existing_ids_are_preserved(self):
        collection = {"features": [
            {"type": "Feature", "id": "12345", "properties": {"objectid": 12345}},
            {"type": "Feature", "id": "67890", "properties": {"objectid": 67890}},
        ]}
        got = edw._sanitize_geojson(collection)
        self.assertEqual([f["id"] for f in got["features"]], ["12345", "67890"])

    def test_missing_id_is_derived_from_the_object_id_attribute(self):
        # The f=json analytic path builds features with no id at all.
        collection = {"features": [
            {"type": "Feature", "properties": {"objectid": 555}},
            {"type": "Feature", "properties": {"OBJECTID": 777}},
        ]}
        got = edw._sanitize_geojson(collection)
        self.assertEqual([f["id"] for f in got["features"]], ["555", "777"])

    def test_positional_fallback_when_nothing_identifies_the_feature(self):
        collection = {"features": [{"type": "Feature", "properties": {"n": 1}},
                                   {"type": "Feature", "properties": {"n": 2}}]}
        got = edw._sanitize_geojson(collection)
        self.assertEqual([f["id"] for f in got["features"]], ["0", "1"])

    def test_sanitising_twice_is_stable(self):
        # _fetch_features_by_ids sanitises each chunk and then the whole set.
        first = edw._sanitize_geojson(
            {"features": [{"type": "Feature", "properties": {"objectid": 900}}]})
        second = edw._sanitize_geojson(
            {"features": [{"type": "Feature", "properties": {"objectid": 901}}]})
        combined = edw._sanitize_geojson(
            {"features": first["features"] + second["features"]})
        self.assertEqual([f["id"] for f in combined["features"]], ["900", "901"])

    def test_null_properties_does_not_raise(self):
        got = edw._sanitize_geojson({"features": [{"type": "Feature",
                                                   "properties": None}]})
        self.assertEqual(got["features"][0]["id"], "0")

    def test_dotted_property_keys_are_stripped(self):
        collection = {"features": [{"type": "Feature", "properties": {
            "SHAPE.LEN": 1, "st_area(shape)": 2, "objectid": 42, "name": "keep"}}]}
        props = edw._sanitize_geojson(collection)["features"][0]["properties"]
        self.assertNotIn("SHAPE.LEN", props)
        self.assertEqual(set(props), {"st_area(shape)", "objectid", "name"})

    def test_empty_collection_is_a_noop(self):
        self.assertEqual(edw._sanitize_geojson({}), {})


if __name__ == "__main__":
    unittest.main()
