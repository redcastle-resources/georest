"""georest.usgs.nldi, offline: what is sent, and what comes back."""

import unittest
import urllib.parse
from unittest import mock

from georest.usgs import nldi


def _point(lon, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props}


class FakeNldi:
    """Records each request; answers from ``replies`` keyed by URL suffix."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        for suffix, reply in self.replies.items():
            if url.endswith(suffix):
                return reply
        raise AssertionError(f"unexpected URL {url}")


class NldiTest(unittest.TestCase):
    def serve(self, replies):
        fake = FakeNldi(replies)
        patcher = mock.patch.object(nldi, "fetch_json", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_friendly_mode_names_map_to_nldi_codes(self):
        fake = self.serve({"/navigation/UT/nwissite": {"features": []}})
        nldi.sites_along("comid", 1, "upstream_tributaries", distance_km=10)
        self.assertIn("/navigation/UT/nwissite", fake.calls[0][0])

    def test_a_bad_mode_fails_before_any_request(self):
        fake = self.serve({})
        with self.assertRaises(ValueError) as cm:
            nldi.flowlines("comid", 1, "sideways", distance_km=10)
        self.assertIn("upstream_tributaries", str(cm.exception))
        self.assertEqual(fake.calls, [])

    def test_navigation_needs_a_positive_distance(self):
        fake = self.serve({})
        for bad in (0, -5, None):
            with self.subTest(distance_km=bad), self.assertRaises(ValueError):
                nldi.flowlines("comid", 1, distance_km=bad)
        self.assertEqual(fake.calls, [])

    def test_find_flowline_sends_a_wkt_point_and_returns_the_comid(self):
        reach = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": []},
                 "properties": {"identifier": "18267741", "comid": 18267741}}
        fake = self.serve({"/comid/position": {"features": [reach]}})
        got = nldi.find_flowline(-111.596, 36.866)
        self.assertEqual(fake.calls[0][1]["coords"], "POINT(-111.596 36.866)")
        self.assertEqual(got["comid"], 18267741)
        self.assertEqual(got["geometry"]["type"], "LineString")

    def test_no_flowline_nearby_says_so(self):
        self.serve({"/comid/position": {"features": []}})
        with self.assertRaisesRegex(ValueError, "no NHDPlus flowline"):
            nldi.find_flowline(0, 0)

    def test_sites_come_back_as_a_frame_with_coordinates(self):
        fake = self.serve({"/navigation/UM/nwissite": {"features": [
            _point(-111.6, 36.9, identifier="USGS-1", name="A", comid=5),
            _point(-111.7, 37.0, identifier="USGS-2", name="B", comid=6),
        ]}})
        df = nldi.sites_along("nwissite", "USGS-09380000", "upstream", distance_km=25)
        self.assertEqual(list(df["identifier"]), ["USGS-1", "USGS-2"])
        self.assertEqual(list(df["lon"]), [-111.6, -111.7])
        self.assertEqual(fake.calls[0][1], {"distance": "25"})
        self.assertIn("/nwissite/USGS-09380000/navigation/UM/nwissite", fake.calls[0][0])

    def test_no_sites_still_has_the_columns(self):
        self.serve({"/navigation/DM/WQP": {"features": []}})
        df = nldi.sites_along("comid", 1, "downstream", data_source="WQP", distance_km=5)
        self.assertTrue(df.empty)
        for column in ("identifier", "name", "lon", "lat"):
            self.assertIn(column, df.columns)

    def test_identifiers_are_quoted_into_the_path(self):
        fake = self.serve({urllib.parse.quote("a b") + "/basin": {"features": []}})
        nldi.basin("ref_dams", "a b")
        self.assertTrue(fake.calls[0][0].endswith("/ref_dams/a%20b/basin"))

    def test_basin_options_are_sent_as_lowercase_booleans(self):
        fake = self.serve({"/basin": {"features": []}})
        nldi.basin("nwissite", "USGS-1", simplified=False, split_catchment=True)
        self.assertEqual(fake.calls[0][1], {"simplified": "false", "splitCatchment": "true"})

    def test_an_unknown_feature_is_a_clear_error(self):
        self.serve({"/nwissite/USGS-0": {"features": []}})
        with self.assertRaisesRegex(ValueError, "NLDI has no nwissite feature"):
            nldi.get_feature("nwissite", "USGS-0")

    def test_list_sources_is_a_frame(self):
        self.serve({"/linked-data": [{"source": "comid", "sourceName": "NHDPlus comid",
                                      "features": "https://x/comid"}]})
        df = nldi.list_sources()
        self.assertEqual(df.to_dict("records"),
                         [{"source": "comid", "name": "NHDPlus comid", "url": "https://x/comid"}])


if __name__ == "__main__":
    unittest.main()
