"""`restusgs/waterdata.py`: parameter mapping, sorting, `to_rows`, API-key resolution.

Offline: every request lands on a `FakeOgcServer` patched onto `_ogc`.
"""
from __future__ import annotations

import os
import random
import unittest
from unittest import mock

from georest.restusgs import _http, _ogc, waterdata

from .support import FakeOgcServer, observation, patched_ogc

#: Neutralise any key in the developer's real environment for a test.
NO_ENV_KEY = dict.fromkeys(waterdata._KEY_NAMES, "")


class WaterdataCase(unittest.TestCase):
    def setUp(self):
        waterdata.set_api_key(None)
        self._env = mock.patch.dict(os.environ, NO_ENV_KEY)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        waterdata.set_api_key(None)


class DailyValuesTests(WaterdataCase):
    def test_parameter_mapping(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values("09380000", start="2024-01-01", end="2024-01-31")
        q = server.query()
        self.assertTrue(server.urls[0].startswith(f"{waterdata.BASE_URL}/collections/daily/items?"))
        self.assertEqual(q["monitoring_location_id"], "USGS-09380000", "bare ids get the prefix")
        self.assertEqual(q["parameter_code"], "00060")
        self.assertEqual(q["statistic_id"], "00003")
        self.assertEqual(q["datetime"], "2024-01-01/2024-01-31")
        self.assertEqual(q["skipGeometry"], "true")
        self.assertEqual(q["limit"], str(waterdata.SERIES_PAGE_SIZE))
        self.assertEqual(q["f"], "json")
        self.assertNotIn("filter", q)

    def test_prefixed_id_passes_through(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values("USEPA-370427111405201")
        self.assertEqual(server.query()["monitoring_location_id"], "USEPA-370427111405201")

    def test_output_is_sorted_by_time_even_when_served_shuffled(self):
        obs = [observation(i, time=f"2024-01-{i + 1:02d}") for i in range(20)]
        random.Random(7).shuffle(obs)
        server = FakeOgcServer(obs, page_cap=6)
        with patched_ogc(fetch_json_with_headers=server):
            fc = waterdata.get_daily_values("09380000", max_items=None)
        times = [f["properties"]["time"] for f in fc["features"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), 20)

    def test_sorts_within_series_then_by_time(self):
        obs = [observation(0, time="2024-01-02", series="b"),
               observation(1, time="2024-01-01", series="b"),
               observation(2, time="2024-01-03", series="a")]
        server = FakeOgcServer(obs)
        with patched_ogc(fetch_json_with_headers=server):
            fc = waterdata.get_daily_values(["09380000", "09402500"])
        got = [(f["properties"]["time_series_id"], f["properties"]["time"]) for f in fc["features"]]
        self.assertEqual(got, [("a", "2024-01-03"), ("b", "2024-01-01"), ("b", "2024-01-02")])

    def test_continuous_timestamps_with_mixed_offsets_sort_by_instant(self):
        obs = [observation(0, time="2024-03-10T01:00:00-07:00"),   # 08:00Z
               observation(1, time="2024-03-10T07:30:00Z"),        # 07:30Z
               observation(2, time="2024-03-10T09:00:00+00:00")]   # 09:00Z
        server = FakeOgcServer(obs)
        with patched_ogc(fetch_json_with_headers=server):
            fc = waterdata.get_continuous_values("09380000")
        got = [f["properties"]["time"] for f in fc["features"]]
        self.assertEqual(got, ["2024-03-10T07:30:00Z", "2024-03-10T01:00:00-07:00",
                               "2024-03-10T09:00:00+00:00"])

    def test_unparsable_and_missing_times_do_not_crash_the_sort(self):
        obs = [observation(0, time="2024-01-02"), observation(1, time="sometime"),
               observation(2, time=None), observation(3, time="2024-01-01")]
        server = FakeOgcServer(obs)
        with patched_ogc(fetch_json_with_headers=server):
            fc = waterdata.get_daily_values("09380000")
        got = [f["properties"]["time"] for f in fc["features"]]
        self.assertEqual(got, ["2024-01-01", "2024-01-02", "sometime", None])

    def test_properties_trim_keeps_the_series_essentials(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values("09380000", properties="value")
        props = server.query()["properties"].split(",")
        self.assertEqual(props[0], "value")
        for name in ("time", "time_series_id", "monitoring_location_id"):
            self.assertIn(name, props)
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values("09380000", properties=["time", "value", "qualifier"])
        self.assertEqual(server.last_query["properties"].split(",")[:3], ["time", "value", "qualifier"])

    def test_list_of_sites_becomes_a_cql_in(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values(["09380000", "USGS-09402500"])
        q = server.query()
        self.assertEqual(q["filter"], "monitoring_location_id IN ('USGS-09380000', 'USGS-09402500')")
        self.assertEqual(q["filter-lang"], "cql2-text")
        self.assertNotIn("monitoring_location_id", q)

    def test_single_item_list_is_a_plain_param(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values(["09380000"])
        self.assertEqual(server.query()["monitoring_location_id"], "USGS-09380000")

    def test_user_filter_is_anded_with_generated_clauses(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values(["1", "2"], parameter_code=["00060", "00065"],
                                       filter="approval_status='Approved'")
        f = server.query()["filter"]
        self.assertEqual(f, "(approval_status='Approved') AND "
                            "(monitoring_location_id IN ('USGS-1', 'USGS-2')) AND "
                            "(parameter_code IN ('00060', '00065'))")

    def test_empty_site_list_is_an_error(self):
        with self.assertRaises(ValueError):
            waterdata.get_daily_values([])

    def test_truncated_and_rate_limit_pass_through(self):
        server = FakeOgcServer([observation(i) for i in range(5)], page_cap=2, rate_limit=(1000, 998))
        with patched_ogc(fetch_json_with_headers=server):
            fc = waterdata.get_daily_values("09380000", max_items=3)
        self.assertTrue(fc["truncated"])
        self.assertEqual(fc["rate_limit"], {"limit": 1000, "remaining": 998})
        self.assertEqual(len(fc["features"]), 3)

    def test_extra_filters_and_reserved_names(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values("09380000", approval_status="Approved")
        self.assertEqual(server.query()["approval_status"], "Approved")
        with self.assertRaises(TypeError):
            waterdata.get_daily_values("09380000", cursor="x")


class OtherCollectionsTests(WaterdataCase):
    def test_continuous_has_no_statistic(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_continuous_values("09380000", "00065", start="P2D")
        q = server.query()
        self.assertIn("/collections/continuous/items?", server.urls[0])
        self.assertEqual(q["parameter_code"], "00065")
        self.assertRegex(q["datetime"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ/\.\.$",
                         "a duration is resolved to an instant; the server rejects 'P2D'")
        self.assertNotIn("statistic_id", q)

    def test_latest_values_sources(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_latest_values(bbox=(-112, 36, -111, 37), parameter_code="00060")
            waterdata.get_latest_values(source="continuous", monitoring_location_id="1")
        self.assertIn("/collections/latest-daily/items?", server.urls[0])
        self.assertEqual(server.query(0)["bbox"], "-112,36,-111,37")
        self.assertNotIn("skipGeometry", server.query(0), "latest values keep the point by default")
        self.assertIn("/collections/latest-continuous/items?", server.urls[1])
        with self.assertRaises(ValueError):
            waterdata.get_latest_values(source="bogus")

    def test_field_measurements_and_peaks(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_field_measurements("09380000", start="2012-01-01")
            waterdata.get_peaks("09380000", end="2000-12-31")
        self.assertIn("/collections/field-measurements/items?", server.urls[0])
        self.assertEqual(server.query(0)["datetime"], "2012-01-01/..")
        self.assertIn("/collections/peaks/items?", server.urls[1])
        self.assertNotIn("parameter_code", server.query(1))

    def test_peaks_window_is_a_cql_filter_not_datetime(self):
        """`peaks` answers 400 "datetime query not supported" (verified live)."""
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_peaks("09380000", start="2010-01-01", end="2020-12-31")
            waterdata.get_peaks("09380000", end="2000-12-31", filter="water_year > 1990")
            waterdata.get_peaks("09380000")
        q0, q1, q2 = server.query(0), server.query(1), server.query(2)
        for q in (q0, q1, q2):
            self.assertNotIn("datetime", q)
        self.assertEqual(q0["filter"], "(time >= '2010-01-01') AND (time <= '2020-12-31')")
        self.assertEqual(q1["filter"], "(water_year > 1990) AND (time <= '2000-12-31')")
        self.assertNotIn("filter", q2)

    def test_time_series_metadata(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_time_series_metadata(monitoring_location_id="09380000",
                                               parameter_code="00060")
        self.assertIn("/collections/time-series-metadata/items?", server.urls[0])
        q = server.query()
        self.assertEqual(q["monitoring_location_id"], "USGS-09380000")
        self.assertEqual(q["parameter_code"], "00060")
        self.assertEqual(q["skipGeometry"], "true")

    def test_generic_get_items_does_not_add_series_properties(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            fc = waterdata.get_items("channel-measurements", properties="value", state_code="49")
        q = server.query()
        self.assertEqual(q["properties"], "value")
        self.assertEqual(q["state_code"], "49")
        self.assertEqual(q["limit"], str(waterdata.LOCATION_PAGE_SIZE))
        self.assertEqual(len(fc["features"]), 1)

    def test_catalog_calls(self):
        server = FakeOgcServer(collections=[{"id": "daily"}], queryables={"time": {}})
        with patched_ogc(fetch_json_with_headers=server):
            self.assertEqual(waterdata.list_collections(), [{"id": "daily"}])
            self.assertEqual(waterdata.get_queryables("daily"), {"time": {}})
        self.assertEqual(server.urls[0], f"{waterdata.BASE_URL}/collections?f=json")
        self.assertEqual(server.urls[1], f"{waterdata.BASE_URL}/collections/daily/queryables?f=json")


class MonitoringLocationTests(WaterdataCase):
    def test_search_maps_named_filters_to_direct_params(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            fc = waterdata.search_monitoring_locations(
                bbox=(-111.7, 36.8, -111.5, 37.0), state_code="49", site_type_code="ST",
                hydrologic_unit_code="1407", agency_code="USGS")
        q = server.query()
        self.assertIn("/collections/monitoring-locations/items?", server.urls[0])
        self.assertEqual(q["bbox"], "-111.7,36.8,-111.5,37.0")
        self.assertEqual(q["state_code"], "49")
        self.assertEqual(q["site_type_code"], "ST")
        self.assertEqual(q["hydrologic_unit_code"], "1407")
        self.assertEqual(q["agency_code"], "USGS")
        self.assertEqual(q["limit"], "1000")
        self.assertNotIn("skipGeometry", q)
        self.assertEqual(len(fc["features"]), 1)

    def test_search_site_type_list_and_ids(self):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.search_monitoring_locations(site_type_code=["ST", "LK"],
                                                  monitoring_location_id="09380000")
        q = server.query()
        self.assertEqual(q["filter"], "site_type_code IN ('ST', 'LK')")
        self.assertEqual(q["monitoring_location_id"], "USGS-09380000")

    def test_get_monitoring_location_fetches_one_feature(self):
        feat = observation(0)
        feat["id"] = "USGS-09380000"
        server = FakeOgcServer([feat])
        with patched_ogc(fetch_json_with_headers=server):
            got = waterdata.get_monitoring_location("09380000")
        self.assertIs(got, feat)
        self.assertEqual(server.urls[0],
                         f"{waterdata.BASE_URL}/collections/monitoring-locations/items/USGS-09380000?f=json")


class ToRowsTests(unittest.TestCase):
    def test_converts_value_and_keeps_everything_else(self):
        fc = {"features": [observation(0, value="12.5", qualifier=["Ice"]),
                           observation(1, time="2023-12-31", value="Ice")]}
        rows = waterdata.to_rows(fc)
        self.assertEqual([r["time"] for r in rows], ["2023-12-31", "2024-01-01"], "sorted")
        self.assertEqual(rows[1]["value"], 12.5)
        self.assertIsNone(rows[0]["value"], "non-numeric becomes None, not an exception")
        self.assertEqual(rows[1]["qualifier"], ["Ice"])
        self.assertEqual(rows[1]["unit_of_measure"], "ft^3/s")
        self.assertEqual(rows[1]["id"], "obs-0", "feature id is added when properties have none")

    def test_does_not_mutate_the_collection(self):
        fc = {"features": [observation(0, value="1")]}
        waterdata.to_rows(fc)
        self.assertEqual(fc["features"][0]["properties"]["value"], "1")

    def test_numeric_override_and_missing_keys(self):
        fc = {"features": [observation(0, value="1", drainage_area="2.5")]}
        rows = waterdata.to_rows(fc, numeric=("drainage_area", "nope"))
        self.assertEqual(rows[0]["drainage_area"], 2.5)
        self.assertEqual(rows[0]["value"], "1")
        self.assertNotIn("nope", rows[0])

    def test_empty_and_null_properties(self):
        self.assertEqual(waterdata.to_rows({"features": []}), [])
        rows = waterdata.to_rows({"features": [{"type": "Feature", "id": "x", "properties": None}]})
        self.assertEqual(rows, [{"id": "x"}])


class ApiKeyTests(WaterdataCase):
    def _header_sent(self, **kwargs):
        server = FakeOgcServer([observation(0)])
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values("09380000", **kwargs)
        return server.headers[0].get("X-Api-Key")

    def test_anonymous_by_default(self):
        self.assertIsNone(self._header_sent())

    def test_explicit_argument(self):
        self.assertEqual(self._header_sent(api_key="explicit"), "explicit")

    def test_environment_fallback_in_order(self):
        with mock.patch.dict(os.environ, {"USGS_WATERDATA_API_KEY": "second"}):
            self.assertEqual(self._header_sent(), "second")
        with mock.patch.dict(os.environ, {"USGS_API_KEY": "first", "USGS_WATERDATA_API_KEY": "second"}):
            self.assertEqual(self._header_sent(), "first")

    def test_set_api_key_beats_the_environment_and_clears(self):
        with mock.patch.dict(os.environ, {"USGS_API_KEY": "env"}):
            waterdata.set_api_key("set")
            self.assertEqual(self._header_sent(), "set")
            waterdata.set_api_key(None)
            self.assertEqual(self._header_sent(), "env")

    def test_explicit_beats_set_api_key(self):
        waterdata.set_api_key("set")
        self.assertEqual(self._header_sent(api_key="explicit"), "explicit")

    def test_empty_string_forces_anonymous(self):
        waterdata.set_api_key("set")
        with mock.patch.dict(os.environ, {"USGS_API_KEY": "env"}):
            self.assertIsNone(self._header_sent(api_key=""))

    def test_key_never_lands_in_a_url(self):
        server = FakeOgcServer([observation(i) for i in range(5)], page_cap=2)
        with patched_ogc(fetch_json_with_headers=server):
            waterdata.get_daily_values("09380000", api_key="sekret", max_items=None)
            waterdata.list_collections(api_key="sekret")
        for url in server.urls:
            self.assertNotIn("sekret", url)
        self.assertTrue(all(h.get("X-Api-Key") == "sekret" for h in server.headers))

    def test_rate_limit_error_is_reexported(self):
        self.assertIs(waterdata.RateLimitError, _http.RateLimitError)


class ModuleShapeTests(unittest.TestCase):
    def test_constants(self):
        self.assertTrue(waterdata.BASE_URL.startswith("https://api.waterdata.usgs.gov/ogcapi/v1"))
        self.assertIn("daily", waterdata.COLLECTIONS)
        self.assertIn("00060", waterdata.PARAMETER_CODES)
        self.assertIn("00003", waterdata.STATISTIC_CODES)
        self.assertEqual(waterdata.SERIES_PAGE_SIZE, _ogc.DEFAULT_PAGE_SIZE)
        self.assertLessEqual(waterdata.SERIES_PAGE_SIZE, _ogc.MAX_LIMIT)

    def test_site_id_normalisation(self):
        self.assertEqual(waterdata._site_id("09380000"), "USGS-09380000")
        self.assertEqual(waterdata._site_id(9380000), "USGS-9380000")
        self.assertEqual(waterdata._site_id(" USGS-09380000 "), "USGS-09380000")


if __name__ == "__main__":
    unittest.main()
