"""Query geometry reaches ArcGIS the way ArcGIS reads it.

Two faults in every spatial filter that took GeoJSON:

* ``geometryType`` stayed ``esriGeometryEnvelope`` whatever the geometry,
  so a GeoJSON polygon -- converted to Esri ``rings`` -- went out labelled
  an envelope. Verified 2026-10-05 against the live USFS EDW server:
  ``queryFeatureServiceCount(url, geometry=<Utah polygon>)`` answered
  HTTP 400; with the type corrected, 8 (the same as the Utah bbox).
* Rings were copied as-is. GeoJSON winds exteriors counter-clockwise and
  holes clockwise; Esri reads clockwise rings as exteriors. So a polygon
  with a hole sent the hole as a second exterior, and the filter searched
  exactly the area meant to be excluded.
"""
from __future__ import annotations

import json
import unittest

from georest.restesri import edw
from georest.restesri import services as S
from georest.restesri._geojson import (
    _convert_query_geometry,
    _geometry_type_for,
    _ring_signed_area,
)

from .support import RecordingFetch, patched_services

FEAT = "https://example.com/arcgis/rest/services/Thing/FeatureServer/0"
CCW_OUTER = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]      # RFC 7946 exterior
CW_HOLE = [[2, 2], [2, 4], [4, 4], [4, 2], [2, 2]]             # RFC 7946 hole
POLY = {"type": "Polygon", "coordinates": [CCW_OUTER, CW_HOLE]}


def cw(ring):
    return _ring_signed_area(ring) < 0


class WindingTests(unittest.TestCase):
    def test_exterior_clockwise_hole_counter_clockwise(self):
        rings = json.loads(_convert_query_geometry(POLY))["rings"]
        self.assertTrue(cw(rings[0]), "exterior must be clockwise for Esri")
        self.assertFalse(cw(rings[1]), "hole must be counter-clockwise for Esri")

    def test_either_input_winding_gives_the_same_answer(self):
        flipped = {"type": "Polygon", "coordinates": [list(reversed(CCW_OUTER)),
                                                      list(reversed(CW_HOLE))]}
        a = json.loads(_convert_query_geometry(POLY))["rings"]
        b = json.loads(_convert_query_geometry(flipped))["rings"]
        self.assertEqual([cw(r) for r in a], [cw(r) for r in b])

    def test_every_multipolygon_part_gets_its_own_exterior(self):
        part2 = [[20, 20], [30, 20], [30, 30], [20, 20]]
        mp = {"type": "MultiPolygon", "coordinates": [[CCW_OUTER, CW_HOLE], [part2]]}
        rings = json.loads(_convert_query_geometry(mp))["rings"]
        self.assertEqual([cw(r) for r in rings], [True, False, True])

    def test_lines_and_multipoints_convert(self):
        line = json.loads(_convert_query_geometry(
            {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}))
        self.assertEqual(line, {"paths": [[[0, 0], [1, 1]]]})
        pts = json.loads(_convert_query_geometry(
            {"type": "MultiPoint", "coordinates": [[0, 0], [1, 1]]}))
        self.assertEqual(pts, {"points": [[0, 0], [1, 1]]})


class GeometryTypeTests(unittest.TestCase):
    def test_type_follows_the_converted_geometry(self):
        cases = {
            "esriGeometryPolygon": POLY,
            "esriGeometryPoint": {"type": "Point", "coordinates": [1, 2]},
            "esriGeometryPolyline": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            "esriGeometryMultipoint": {"type": "MultiPoint", "coordinates": [[0, 0]]},
            "esriGeometryEnvelope": "-1,-1,1,1",
        }
        for want, geom in cases.items():
            with self.subTest(want=want):
                got = _geometry_type_for(_convert_query_geometry(geom), "esriGeometryEnvelope")
                self.assertEqual(got, want)

    def test_an_explicit_type_is_kept(self):
        self.assertEqual(_geometry_type_for(_convert_query_geometry(POLY),
                                            "esriGeometryMultipoint"),
                         "esriGeometryMultipoint")

    def test_count_sends_a_polygon_as_a_polygon(self):
        fake = RecordingFetch({"count": 8})
        with patched_services(fetch_json=fake):
            S.queryFeatureServiceCount(FEAT, geometry=POLY)
        self.assertEqual(fake.last_params["geometryType"], "esriGeometryPolygon")

    def test_query_sends_a_polygon_as_a_polygon(self):
        fake = RecordingFetch({"count": 1},
                              {"type": "FeatureCollection", "features": []})
        with patched_services(fetch_json=fake):
            S.queryFeatureService(FEAT, geometry=POLY)
        self.assertTrue(all(p.get("geometryType") == "esriGeometryPolygon"
                            for _, p in fake.calls if "geometry" in p))

    def test_edw_and_services_convert_identically(self):
        self.assertEqual(edw._convert_geometry(POLY, ""), S._convert_geometry(POLY, ""))


if __name__ == "__main__":
    unittest.main()
