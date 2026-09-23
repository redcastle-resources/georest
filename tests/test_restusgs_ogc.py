"""`restusgs/_ogc.py`: cursor paging, `max_items`, and the parameter builders.

The `FakeOgcServer` in `support` models the one behaviour that matters for
paging: the server caps every page regardless of `limit`, reports no
`numberMatched`, and hands back a `next` link that must be followed
verbatim until it stops appearing.
"""
from __future__ import annotations

import datetime as dt
import unittest

from georest.restusgs import _ogc

from .support import FakeOgcServer, observation, patched_ogc

BASE = "https://api.example/ogcapi/v1"


def features(n):
    return [observation(i, time=f"2024-01-{i + 1:02d}") for i in range(n)]


class PagingTests(unittest.TestCase):
    def test_follows_next_links_to_the_end(self):
        server = FakeOgcServer(features(25), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=None)
        self.assertEqual(len(fc["features"]), 25)
        self.assertEqual(len(server.urls), 3)
        self.assertEqual([server.query(i).get("cursor") for i in range(3)], [None, "10", "20"])
        self.assertNotIn("truncated", fc)
        self.assertEqual(fc["type"], "FeatureCollection")

    def test_next_link_is_followed_verbatim(self):
        """The server preserves every original param in `next`; we must not rebuild it."""
        server = FakeOgcServer(features(12), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            _ogc.get_items(BASE, "daily", max_items=None, parameter_code="00060",
                           datetime="2024-01-01/2024-01-31")
        second = server.query(1)
        self.assertEqual(second["parameter_code"], "00060")
        self.assertEqual(second["datetime"], "2024-01-01/2024-01-31")
        self.assertEqual(second["cursor"], "10")

    def test_max_items_trims_mid_page_and_flags_truncated(self):
        server = FakeOgcServer(features(25), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=15)
        self.assertEqual(len(fc["features"]), 15)
        self.assertTrue(fc["truncated"])
        self.assertEqual(len(server.urls), 2, "must stop fetching once max_items is met")

    def test_max_items_on_a_page_boundary_with_more_behind_is_truncated(self):
        server = FakeOgcServer(features(25), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=10)
        self.assertEqual(len(fc["features"]), 10)
        self.assertTrue(fc["truncated"])
        self.assertEqual(len(server.urls), 1)

    def test_max_items_equal_to_total_is_not_truncated(self):
        server = FakeOgcServer(features(10), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=10)
        self.assertEqual(len(fc["features"]), 10)
        self.assertNotIn("truncated", fc)

    def test_max_items_larger_than_total_is_not_truncated(self):
        server = FakeOgcServer(features(7), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=100)
        self.assertEqual(len(fc["features"]), 7)
        self.assertNotIn("truncated", fc)

    def test_empty_collection(self):
        server = FakeOgcServer([])
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=None)
        self.assertEqual(fc, {"type": "FeatureCollection", "features": []})

    def test_a_next_link_pointing_back_at_itself_terminates(self):
        page = {"features": features(1),
                "links": [{"rel": "next", "href": f"{BASE}/collections/daily/items?limit=10000&f=json"}]}
        calls = []

        def fetch(url, params=None, timeout=None, headers=None):
            calls.append(url)
            return page, {}

        with patched_ogc(fetch_json_with_headers=fetch):
            fc = _ogc.get_items(BASE, "daily", max_items=None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(fc["features"]), 1)

    def test_headers_are_sent_on_every_hop(self):
        server = FakeOgcServer(features(25), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            _ogc.get_items(BASE, "daily", max_items=None, headers={"X-Api-Key": "k"})
        self.assertEqual(server.headers, [{"X-Api-Key": "k"}] * 3)

    def test_page_without_features_key_is_a_valueerror(self):
        with patched_ogc(fetch_json_with_headers=lambda *a, **k: ({"error": "nope"}, {})):
            with self.assertRaises(ValueError) as ctx:
                _ogc.get_items(BASE, "daily", max_items=None)
        self.assertIn("features", str(ctx.exception))

    def test_bad_max_items(self):
        for bad in (0, -1, 1.5, True, "10"):
            with self.subTest(max_items=bad):
                with self.assertRaises(ValueError):
                    _ogc.get_items(BASE, "daily", max_items=bad)

    def test_iter_items_streams_and_stops_at_max(self):
        server = FakeOgcServer(features(25), page_cap=10)
        with patched_ogc(fetch_json_with_headers=server):
            params = _ogc.build_item_params(limit=10)
            got = list(_ogc.iter_items(BASE, "daily", params, max_items=12))
        self.assertEqual(len(got), 12)
        self.assertEqual(len(server.urls), 2)

    def test_first_url_keeps_commas_and_slashes_readable(self):
        server = FakeOgcServer(features(1))
        with patched_ogc(fetch_json_with_headers=server):
            _ogc.get_items(BASE, "daily", max_items=None, bbox=(-1, 2, 3, 4),
                           datetime="2020-01-01/2020-01-31")
        url = server.urls[0]
        self.assertIn("bbox=-1,2,3,4", url)
        self.assertIn("datetime=2020-01-01/2020-01-31", url)
        self.assertTrue(url.startswith(f"{BASE}/collections/daily/items?"))


class RateLimitMemberTests(unittest.TestCase):
    def test_rate_limit_from_headers(self):
        server = FakeOgcServer(features(3), rate_limit=(1000, 998))
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=None)
        self.assertEqual(fc["rate_limit"], {"limit": 1000, "remaining": 998})

    def test_absent_when_no_headers(self):
        server = FakeOgcServer(features(3))
        with patched_ogc(fetch_json_with_headers=server):
            fc = _ogc.get_items(BASE, "daily", max_items=None)
        self.assertNotIn("rate_limit", fc)

    def test_parser_is_case_insensitive_and_tolerant(self):
        self.assertEqual(_ogc.rate_limit_from_headers({"x-ratelimit-limit": "5"}), {"limit": 5})
        self.assertIsNone(_ogc.rate_limit_from_headers({"X-RateLimit-Limit": "lots"}))
        self.assertIsNone(_ogc.rate_limit_from_headers(None))


class CatalogTests(unittest.TestCase):
    def test_list_collections(self):
        server = FakeOgcServer(collections=[{"id": "a"}, {"id": "b"}])
        with patched_ogc(fetch_json_with_headers=server):
            got = _ogc.list_collections(BASE)
        self.assertEqual([c["id"] for c in got], ["a", "b"])
        self.assertEqual(server.urls[0], f"{BASE}/collections?f=json")

    def test_get_queryables_returns_the_properties_map(self):
        server = FakeOgcServer(queryables={"time": {"type": "string"}})
        with patched_ogc(fetch_json_with_headers=server):
            got = _ogc.get_queryables(BASE, "daily")
        self.assertEqual(got, {"time": {"type": "string"}})
        self.assertEqual(server.urls[0], f"{BASE}/collections/daily/queryables?f=json")

    def test_get_item_quotes_the_id(self):
        feat = observation(0)
        feat["id"] = "USGS-0938 0000"
        server = FakeOgcServer([feat])
        with patched_ogc(fetch_json_with_headers=server):
            got = _ogc.get_item(BASE, "monitoring-locations", "USGS-0938 0000")
        self.assertIs(got, feat)
        self.assertIn("/items/USGS-0938%200000?", server.urls[0])

    def test_base_url_trailing_slash_is_tolerated(self):
        server = FakeOgcServer()
        with patched_ogc(fetch_json_with_headers=server):
            _ogc.list_collections(BASE + "/")
        self.assertEqual(server.urls[0], f"{BASE}/collections?f=json")


class BuildItemParamsTests(unittest.TestCase):
    def test_reserved_params_raise_typeerror(self):
        for name in ("f", "limit", "cursor", "offset"):
            with self.subTest(name=name):
                with self.assertRaises(TypeError) as ctx:
                    _ogc.build_item_params(**{name: "1"}) if name != "limit" else \
                        _ogc.get_items(BASE, "daily", max_items=None, **{"limit": 1, "cursor": "x"})
                self.assertIn("set by the client", str(ctx.exception))

    def test_limit_bounds(self):
        for bad in (0, -1, _ogc.MAX_LIMIT + 1, 1.0, True, "10"):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    _ogc.build_item_params(limit=bad)
        self.assertEqual(_ogc.build_item_params(limit=_ogc.MAX_LIMIT)["limit"], str(_ogc.MAX_LIMIT))

    def test_shapes(self):
        params = _ogc.build_item_params(
            bbox=[-112.5, 36.5, -111.5, 37.5], datetime="P7D", properties=["time", "value"],
            skip_geometry=True, filter="statistic_id='00003'", limit=5,
            parameter_code="00060", monitoring_location_id=None, year=2020)
        self.assertEqual(params, {
            "bbox": "-112.5,36.5,-111.5,37.5",
            "datetime": "P7D",
            "properties": "time,value",
            "skipGeometry": "true",
            "filter": "statistic_id='00003'",
            "filter-lang": "cql2-text",
            "parameter_code": "00060",
            "year": "2020",
            "limit": "5",
            "f": "json",
        })

    def test_defaults_are_minimal(self):
        self.assertEqual(_ogc.build_item_params(),
                         {"limit": str(_ogc.DEFAULT_PAGE_SIZE), "f": "json"})

    def test_properties_string_passes_through(self):
        self.assertEqual(_ogc.build_item_params(properties="a,b")["properties"], "a,b")


class BboxParamTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(_ogc.bbox_param("1,2,3,4"), "1,2,3,4")
        self.assertEqual(_ogc.bbox_param((1, 2, 3, 4)), "1,2,3,4")
        self.assertEqual(_ogc.bbox_param([1.5, 2, 3, 4.25]), "1.5,2,3,4.25")
        self.assertEqual(_ogc.bbox_param((1, 2, 0, 3, 4, 10)), "1,2,0,3,4,10")
        self.assertEqual(_ogc.bbox_param({"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}), "1,2,3,4")
        self.assertEqual(_ogc.bbox_param(("1", "2", "3", "4")), "1.0,2.0,3.0,4.0")

    def test_bad_shapes(self):
        for bad in ((1, 2, 3), (1, 2, 3, 4, 5), {"xmin": 1}, 5, None, ("a", 2, 3, 4)):
            with self.subTest(bbox=bad):
                with self.assertRaises(ValueError):
                    _ogc.bbox_param(bad)


class DatetimeParamTests(unittest.TestCase):
    def test_matrix(self):
        cases = [
            ((None, None), None),
            (("2020-01-01", "2020-01-31"), "2020-01-01/2020-01-31"),
            (("2020-01-01", None), "2020-01-01/.."),
            ((None, "2020-01-31"), "../2020-01-31"),
            (("2020-01-01/2020-01-31", None), "2020-01-01/2020-01-31"),
            (("../2020-12-31", None), "../2020-12-31"),
            ((" 2020-01-01 ", " 2020-01-31 "), "2020-01-01/2020-01-31"),
            ((dt.date(2020, 1, 1), dt.date(2020, 1, 31)), "2020-01-01/2020-01-31"),
            ((dt.datetime(2020, 1, 1, 6, 30), None), "2020-01-01T06:30:00Z/.."),
            ((dt.datetime(2020, 1, 1, 6, 30, tzinfo=dt.timezone.utc), None),
             "2020-01-01T06:30:00+00:00/.."),
            ((dt.datetime(2020, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=-7))), "2020-01-02"),
             "2020-01-01T00:00:00-07:00/2020-01-02"),
        ]
        for (start, end), want in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(_ogc.datetime_param(start, end), want)

    def test_interval_start_with_end_is_an_error(self):
        with self.assertRaises(ValueError):
            _ogc.datetime_param("2020-01-01/2020-01-31", "2020-02-01")
        with self.assertRaises(ValueError):
            _ogc.datetime_param("P7D", "2020-02-01")

    def test_durations_resolve_client_side(self):
        """The server rejects every duration form with 400 "Unknown string
        format" (verified live), so "P7D" must never reach it verbatim."""
        now = dt.datetime(2026, 9, 14, 12, 0, 30, 123456, tzinfo=dt.timezone.utc)
        saved = _ogc._now
        _ogc._now = lambda: now
        try:
            cases = [
                ("P7D", "2026-09-07T12:00:30Z/.."),
                ("p7d", "2026-09-07T12:00:30Z/.."),
                ("PT6H", "2026-09-14T06:00:30Z/.."),
                ("PT90M", "2026-09-14T10:30:30Z/.."),
                ("P2W", "2026-08-31T12:00:30Z/.."),
                ("P1DT12H", "2026-09-13T00:00:30Z/.."),
                ("PT0.5H", "2026-09-14T11:30:30Z/.."),
                (" P1D ", "2026-09-13T12:00:30Z/.."),
            ]
            for text, want in cases:
                with self.subTest(duration=text):
                    self.assertEqual(_ogc.datetime_param(text), want)
        finally:
            _ogc._now = saved

    def test_bad_durations(self):
        for text in ("P1Y", "P3M", "P1Y2D", "P", "PT", "P1DT", "P7X", "Pfoo"):
            with self.subTest(duration=text):
                with self.assertRaises(ValueError) as ctx:
                    _ogc.datetime_param(text)
                if "Y" in text or text == "P3M":
                    self.assertIn("years or months", str(ctx.exception))

    def test_minutes_after_t_are_not_months(self):
        self.assertTrue(_ogc.datetime_param("PT30M").endswith("Z/.."))

    def test_unsupported_type(self):
        with self.assertRaises(TypeError):
            _ogc.datetime_param(20200101)


class ApiKeyHeadersTests(unittest.TestCase):
    def test_header_or_nothing(self):
        self.assertEqual(_ogc.api_key_headers("k"), {"X-Api-Key": "k"})
        self.assertEqual(_ogc.api_key_headers(None), {})
        self.assertEqual(_ogc.api_key_headers(""), {})


if __name__ == "__main__":
    unittest.main()
