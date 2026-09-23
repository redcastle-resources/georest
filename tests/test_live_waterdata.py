"""Live tests for `restusgs.waterdata` against api.waterdata.usgs.gov.

Opt-in via USGS_LIVE=1 (or EDW_LIVE=1). Every call goes through `retry`, so
a server that is down skips rather than fails. Anonymous callers get "a few
queries per hour"; set USGS_API_KEY to keep this suite off the rate limit.

Lees Ferry (USGS-09380000, Colorado River below Glen Canyon Dam) is the
fixture site: continuously gauged since 1921 with daily discharge (00060),
so its data are not going anywhere.
"""
from __future__ import annotations

import unittest

from georest.restusgs import _ogc, waterdata

from .support import requires_live_usgs, retry

SITE = "USGS-09380000"
LEES_FERRY_BBOX = (-111.7, 36.8, -111.5, 37.0)


@requires_live_usgs
class LiveWaterdataTests(unittest.TestCase):
    def test_collections_catalog_and_version(self):
        cols = retry(waterdata.list_collections)
        ids = {c["id"] for c in cols}
        self.assertIn("monitoring-locations", ids)
        self.assertIn("daily", ids)
        # Early warning if the API moves off v1 (v0 was still the documented
        # version when this was written; v1 was what the server linked to).
        hrefs = " ".join(link.get("href", "") for c in cols for link in c.get("links", []))
        self.assertIn("/ogcapi/v1/", hrefs)

    def test_bbox_site_search(self):
        fc = retry(lambda: waterdata.search_monitoring_locations(
            bbox=LEES_FERRY_BBOX, site_type_code="ST"))
        self.assertTrue(fc["features"])
        ids = [f["id"] for f in fc["features"]]
        self.assertTrue(all(i.startswith("USGS-") for i in ids), ids)
        self.assertIn(SITE, ids)
        self.assertEqual(fc["features"][0]["geometry"]["type"], "Point")

    def test_daily_discharge_window(self):
        fc = retry(lambda: waterdata.get_daily_values(
            SITE, "00060", start="2024-01-01", end="2024-01-31"))
        self.assertGreaterEqual(len(fc["features"]), 28)
        self.assertNotIn("truncated", fc)
        times = [f["properties"]["time"] for f in fc["features"]]
        self.assertEqual(times, sorted(times), "must be sorted client-side")
        self.assertTrue(all(t.startswith("2024-01-") for t in times), times[:3])
        self.assertIsNone(fc["features"][0]["geometry"], "skip_geometry default")
        rows = waterdata.to_rows(fc)
        self.assertIsInstance(rows[0]["value"], float)
        self.assertEqual(rows[0]["monitoring_location_id"], SITE)

    def test_queryables(self):
        props = retry(lambda: waterdata.get_queryables("daily"))
        self.assertIn("parameter_code", props)
        self.assertIn("time", props)

    def test_peaks_and_field_measurements_carry_time_and_value(self):
        peaks = retry(lambda: waterdata.get_peaks(SITE, max_items=3))
        fm = retry(lambda: waterdata.get_field_measurements(SITE, max_items=3))
        for fc in (peaks, fm):
            self.assertTrue(fc["features"])
            props = fc["features"][0]["properties"]
            self.assertIn("time", props)
            self.assertIn("value", props)

    def test_max_page_size_is_still_accepted(self):
        """Guards `MAX_LIMIT`: the schema says 10 000 but 50 000 works live."""
        fc = retry(lambda: waterdata.get_daily_values(
            SITE, "00060", start="2019-01-01", end="2019-01-10",
            page_size=_ogc.MAX_LIMIT, properties="time,value"))
        self.assertGreaterEqual(len(fc["features"]), 9)
        self.assertLessEqual(set(fc["features"][0]["properties"]),
                             {"time", "value", "time_series_id", "monitoring_location_id"})

    def test_bad_datetime_surfaces_the_api_description(self):
        # Deliberately NOT through `retry`: a 400 is the expected answer, not
        # a transient failure to wait out. A connection failure is the one
        # RuntimeError here that says nothing about the code, so it skips.
        try:
            waterdata.get_daily_values(SITE, start="not-a-date")
        except RuntimeError as exc:
            msg = str(exc)
            if "400" not in msg:
                self.skipTest(f"server unavailable: {msg.splitlines()[0]}")
            self.assertGreater(len(msg.splitlines()), 1, "the error body should follow the URL")
            self.assertIn("not-a-date", msg, "the API's own description should be in the message")
        else:
            self.fail("a nonsense datetime should have been rejected with a 400")


if __name__ == "__main__":
    unittest.main()
