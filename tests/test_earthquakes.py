"""georest.usgs.earthquakes, offline: checks, paging and the rows."""

import unittest
from unittest import mock

from georest.usgs import earthquakes as eq


def _quake(i, mag=5.0, ms=1_735_689_600_000):
    return {"type": "Feature", "id": f"us{i}",
            "geometry": {"type": "Point", "coordinates": [140.0, 35.0, 10.0]},
            "properties": {"mag": mag, "magType": "mww", "place": f"place {i}", "time": ms,
                           "updated": ms, "type": "earthquake", "alert": "green",
                           "tsunami": 1, "sig": 400, "net": "us", "title": f"M {mag}",
                           "url": f"https://e/{i}", "status": "reviewed"}}


class EarthquakesTest(unittest.TestCase):
    def serve(self, handler):
        calls = []

        def fake(url, params=None, **kwargs):
            calls.append((url, dict(params or {})))
            return handler(url, dict(params or {}))

        patcher = mock.patch.object(eq, "fetch_json", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_every_page_is_fetched_when_a_search_exceeds_the_limit(self):
        quakes = [_quake(i) for i in range(7)]

        def handler(url, params):
            if url.endswith("/count"):
                return {"count": len(quakes)}
            start = params["offset"] - 1
            return {"features": quakes[start:start + params["limit"]]}

        with mock.patch.object(eq, "PAGE_LIMIT", 3):
            calls = self.serve(handler)
            df = eq.search("2025-01-01", "2025-12-31")
        self.assertEqual(list(df["id"]), [f"us{i}" for i in range(7)])
        self.assertEqual([p.get("offset") for _, p in calls[1:]], [1, 4, 7])

    def test_rows_carry_time_depth_and_flags(self):
        self.serve(lambda url, p: {"count": 1} if url.endswith("/count")
                   else {"features": [_quake(1, mag=7.1)]})
        row = eq.search("2025-01-01", "2025-01-02").iloc[0]
        self.assertEqual(row["magnitude"], 7.1)
        self.assertEqual((row["lon"], row["lat"], row["depth_km"]), (140.0, 35.0, 10.0))
        self.assertEqual(str(row["time"]), "2025-01-01 00:00:00+00:00")
        self.assertIs(bool(row["tsunami"]), True)

    def test_no_events_still_has_the_columns(self):
        self.serve(lambda url, p: {"count": 0})
        df = eq.search("2025-01-01", "2025-01-02", min_magnitude=9.5)
        self.assertTrue(df.empty)
        self.assertIn("magnitude", df.columns)

    def test_filters_are_sent_under_the_service_names(self):
        calls = self.serve(lambda url, p: {"count": 0})
        eq.search("2025-01-01", "2025-12-31", center=(-118.24, 34.05), radius_km=100,
                  min_magnitude=3, max_depth_km=20, alert_level="yellow", order="largest")
        sent = calls[0][1]
        self.assertEqual((sent["longitude"], sent["latitude"], sent["maxradiuskm"]),
                         (-118.24, 34.05, 100.0))
        self.assertEqual((sent["minmagnitude"], sent["maxdepth"], sent["alertlevel"],
                          sent["orderby"]), (3.0, 20.0, "yellow", "magnitude"))

    def test_bad_searches_fail_before_any_request(self):
        calls = self.serve(lambda url, p: {"count": 0})
        bad = (
            {"bbox": (0, 0, 1, 1), "center": (0, 0), "radius_km": 10},
            {"radius_km": 10},
            {"center": (0, 0)},
            {"center": (0, 0), "radius_km": 99999},
            {"alert_level": "purple"},
            {"order": "loudest"},
            {"bbox": (0, 5, 1, 1)},
        )
        for kwargs in bad:
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}), self.assertRaises(ValueError):
                eq.search("2025-01-01", "2025-12-31", **kwargs)
        self.assertEqual(calls, [])

    def test_an_event_lists_its_products(self):
        detail = _quake(9)
        detail["properties"]["products"] = {
            "shakemap": [{"contents": {"download/grid.xml": {"url": "https://e/grid.xml"}}}],
            "dyfi": [{"contents": {}}],
        }
        self.serve(lambda url, p: detail)
        got = eq.get_event("us9")
        self.assertEqual(got["products"], ["dyfi", "shakemap"])
        self.assertEqual(got["product_urls"], {"shakemap": "https://e/grid.xml"})

    def test_an_unknown_event_is_a_clear_error(self):
        self.serve(lambda url, p: {"type": "FeatureCollection", "features": []})
        with self.assertRaisesRegex(ValueError, "no event"):
            eq.get_event("nope")


if __name__ == "__main__":
    unittest.main()
