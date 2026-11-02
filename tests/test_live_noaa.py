"""Live tests for georest.noaa (NOAA_LIVE=1).

Shapes only, never today's weather: a forecast has periods, a station near
Houston reports, Galveston's tide gauge answers. A service that is down
skips rather than fails.
"""
from __future__ import annotations

import unittest

from georest.noaa import coops, nhc, nwps, nws
from georest.noaa._http import NOAAError

from .support import requires_live_noaa, retry


@requires_live_noaa
class LiveNoaaTests(unittest.TestCase):

    def test_houston_has_a_forecast_in_one_unit_system(self):
        df = retry(lambda: nws.forecast(-95.37, 29.76))
        self.assertGreater(len(df), 0)
        self.assertIn("temperature_f", df.columns)
        self.assertNotIn("temperature_c", df.columns)

    def test_the_open_atlantic_has_no_nws_forecast(self):
        # Not through retry(): NotCovered is the answer here, not an outage.
        try:
            nws.forecast(-40.0, 30.0)
        except nws.NotCovered:
            return
        except NOAAError as exc:
            self.skipTest(f"the NWS API is unavailable: {exc}")
        self.fail("a point in the open Atlantic got an NWS forecast")

    def test_alerts_answer_with_their_columns(self):
        df = retry(lambda: nws.alerts(area="TX"))
        self.assertIn("has_polygon", df.columns)

    def test_stations_near_houston_are_nearest_first(self):
        df = retry(lambda: nws.stations(-95.37, 29.76))
        self.assertGreater(len(df), 3)
        self.assertEqual(list(df["distance_km"]), sorted(df["distance_km"]))

    def test_buffalo_bayou_has_flood_stages(self):
        g = retry(lambda: nwps.gauge("BBST2"))
        self.assertEqual(g["state"], "TX")
        self.assertIsNotNone(g["flood_stages"]["minor"])

    def test_galveston_pier_21_is_the_nearest_tide_station(self):
        df = retry(lambda: coops.nearest(-94.7933, 29.31))
        self.assertEqual(df.iloc[0]["station"], "8771450")

    def test_galveston_water_levels_come_back(self):
        df = retry(lambda: coops.water_levels("8771450", hours=6))
        self.assertGreater(len(df), 0)
        self.assertEqual(df.attrs["datum"], "MLLW")

    def test_nhc_answers_with_its_columns(self):
        df = retry(lambda: nhc.active_storms())
        self.assertIn("wind_kt", df.columns)


if __name__ == "__main__":
    unittest.main()
