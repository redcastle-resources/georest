"""Offline tests for georest.noaa: nws, nwps, coops, nhc and the transport.

NOAA's answers are stood in for here; tests/test_live_noaa.py asks the real
services (NOAA_LIVE=1).
"""
from __future__ import annotations

import io
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

from georest.noaa import _http, coops, nhc, nwps, nws


def _patch(test, module, fake):
    patcher = mock.patch.object(module, "fetch_json", fake)
    patcher.start()
    test.addCleanup(patcher.stop)


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# nws
# ---------------------------------------------------------------------------

POINT = {"properties": {
    "forecast": "https://api.weather.gov/gridpoints/HGX/63,95/forecast",
    "forecastHourly": "https://api.weather.gov/gridpoints/HGX/63,95/forecast/hourly",
    "observationStations": "https://api.weather.gov/gridpoints/HGX/63,95/stations"}}


def _obs(minutes_ago, temp_c=30.0, wind_kmh=18.0, qc="V"):
    return {"properties": {
        "timestamp": _ago(minutes_ago), "textDescription": "Clear",
        "temperature": {"value": temp_c, "unitCode": "wmoUnit:degC", "qualityControl": qc},
        "dewpoint": {"value": 20.0, "unitCode": "wmoUnit:degC", "qualityControl": "V"},
        "heatIndex": {"value": None, "unitCode": "wmoUnit:degC"},
        "windChill": {"value": None, "unitCode": "wmoUnit:degC"},
        "relativeHumidity": {"value": 55.0, "unitCode": "wmoUnit:percent"},
        "windSpeed": {"value": wind_kmh, "unitCode": "wmoUnit:km_h-1"},
        "windGust": {"value": None, "unitCode": "wmoUnit:km_h-1"},
        "windDirection": {"value": 180, "unitCode": "wmoUnit:degree_(angle)"},
        "barometricPressure": {"value": 101900, "unitCode": "wmoUnit:Pa"},
        "visibility": {"value": 16090, "unitCode": "wmoUnit:m"}}}


class AlertsTest(unittest.TestCase):

    def setUp(self):
        self.calls = []

        def fake(url, params=None, timeout=None, accept=None):
            self.calls.append((url, dict(params or {})))
            return {"features": [
                {"geometry": None, "properties": {
                    "id": "a1", "event": "Coastal Flood Advisory", "severity": "Minor",
                    "areaDesc": "Galveston Island", "affectedZones": [
                        "https://api.weather.gov/zones/forecast/TXZ442"]}},
                {"geometry": {"type": "Polygon", "coordinates": []}, "properties": {
                    "id": "a2", "event": "Flood Warning", "severity": "Severe",
                    "affectedZones": []}}]}
        _patch(self, nws, fake)

    def test_rows_carry_zones_and_whether_there_is_a_polygon(self):
        df = nws.alerts(area="tx")
        self.assertEqual(list(df["has_polygon"]), [False, True])
        self.assertEqual(df.iloc[0]["zones"], ["TXZ442"])
        self.assertEqual(self.calls[0][1]["area"], "TX")

    def test_a_point_is_sent_latitude_first_as_the_nws_wants(self):
        nws.alerts(point=(-95.37, 29.76))
        self.assertEqual(self.calls[0][1]["point"], "29.7600,-95.3700")

    def test_point_and_area_together_are_refused(self):
        with self.assertRaises(ValueError):
            nws.alerts(point=(-95.37, 29.76), area="TX")

    def test_no_alerts_still_has_the_columns(self):
        _patch(self, nws, lambda *a, **k: {"features": []})
        df = nws.alerts(area="TX")
        self.assertEqual(len(df), 0)
        self.assertIn("has_polygon", df.columns)


class ForecastTest(unittest.TestCase):

    def setUp(self):
        def fake(url, params=None, timeout=None, accept=None):
            if "/points/" in url:
                return POINT
            return {"properties": {"periods": [
                {"name": "Tonight", "temperature": 67, "temperatureUnit": "F",
                 "windSpeed": "5 to 10 mph", "windDirection": "SE",
                 "probabilityOfPrecipitation": {"value": 12},
                 "shortForecast": "Partly Cloudy", "isDaytime": False},
                {"name": "Odd", "temperature": 20, "temperatureUnit": "K",
                 "windSpeed": "", "shortForecast": "?"}]}}
        _patch(self, nws, fake)

    def test_us_units_and_the_wind_range_upper_end(self):
        df = nws.forecast(-95.37, 29.76)
        self.assertEqual(df.iloc[0]["temperature_f"], 67)
        self.assertEqual(df.iloc[0]["wind_max_mph"], 10)
        self.assertEqual(df.iloc[0]["wind_text"], "5 to 10 mph")

    def test_si_units_convert_and_rename(self):
        df = nws.forecast(-95.37, 29.76, units="si")
        self.assertAlmostEqual(df.iloc[0]["temperature_c"], 19.4)
        self.assertEqual(df.iloc[0]["wind_max_kmh"], 16)
        self.assertNotIn("temperature_f", df.columns)

    def test_an_unknown_temperature_unit_is_not_guessed(self):
        df = nws.forecast(-95.37, 29.76)
        self.assertTrue(df["temperature_f"].isna().iloc[1])

    def test_outside_the_nws_is_not_covered(self):
        def fake(url, params=None, timeout=None, accept=None):
            raise _http.NOAAError("Request failed (404)", status=404)
        _patch(self, nws, fake)
        with self.assertRaises(nws.NotCovered):
            nws.forecast(-40.0, 30.0)

    def test_bad_units_are_refused(self):
        with self.assertRaises(ValueError):
            nws.forecast(-95.37, 29.76, units="kelvin")


class ObservationsTest(unittest.TestCase):

    def _run(self, readings, **kw):
        def fake(url, params=None, timeout=None, accept=None):
            if "/points/" in url:
                return POINT
            if url.endswith("/stations"):
                return {"features": [
                    {"properties": {"stationIdentifier": s, "name": s},
                     "geometry": {"coordinates": [-95.37 + 0.1 * i, 29.76]}}
                    for i, s in enumerate(readings)]}
            station = url.split("/stations/")[1].split("/")[0]
            got = readings[station]
            if isinstance(got, Exception):
                raise got
            return got
        _patch(self, nws, fake)
        return nws.observations(-95.37, 29.76, **kw)

    def test_si_readings_come_back_in_us_units(self):
        df = self._run({"KHOU": _obs(10)}, n=1)
        row = df.iloc[0]
        self.assertEqual(row["temperature_f"], 86.0)
        self.assertAlmostEqual(row["wind_mph"], 11.2)
        self.assertAlmostEqual(row["pressure_inhg"], 30.09)
        self.assertAlmostEqual(row["visibility_mi"], 10.0)

    def test_stale_and_future_readings_are_dropped_and_counted(self):
        df = self._run({"OLD": _obs(600), "SKEW": _obs(-30), "KHOU": _obs(10)}, n=3)
        self.assertEqual(list(df["station"]), ["KHOU"])
        self.assertEqual(df.attrs["dropped_stale"], ["OLD", "SKEW"])

    def test_a_station_that_fails_is_named(self):
        df = self._run({"DOWN": _http.NOAAError("x", status=500), "KHOU": _obs(10)}, n=2)
        self.assertEqual(df.attrs["failed"], ["DOWN"])

    def test_unvalidated_readings_are_flagged(self):
        df = self._run({"KHOU": _obs(10, qc="Z")}, n=1)
        self.assertEqual(df.iloc[0]["qc"], {"temperature_f": "Z"})

    def test_an_unrecognized_unit_is_left_empty_not_mislabelled(self):
        o = _obs(10)
        o["properties"]["windSpeed"] = {"value": 9.0, "unitCode": "wmoUnit:knots"}
        df = self._run({"KHOU": o}, n=1)
        self.assertIsNone(df.iloc[0]["wind_mph"])
        self.assertIn("unrecognized unit", df.iloc[0]["qc"]["wind_mph"])

    def test_nearest_station_first(self):
        df = self._run({"NEAR": _obs(10), "FAR": _obs(10)}, n=2)
        self.assertEqual(list(df["station"]), ["NEAR", "FAR"])
        self.assertLess(df.iloc[0]["distance_km"], df.iloc[1]["distance_km"])


# ---------------------------------------------------------------------------
# nwps
# ---------------------------------------------------------------------------

def _gauge(lid, stage, category, fstage=-999, fcat="fcst_not_current"):
    return {"lid": lid, "name": lid, "state": {"abbreviation": "TX"},
            "latitude": 29.76, "longitude": -95.4,
            "status": {"observed": {"primary": stage, "primaryUnit": "ft",
                                    "secondary": -999, "secondaryUnit": "kcfs",
                                    "floodCategory": category,
                                    "validTime": "2026-10-28T18:15:00Z"},
                       "forecast": {"primary": fstage, "floodCategory": fcat,
                                    "validTime": "2026-10-30T12:00:00Z"}}}


class NwpsTest(unittest.TestCase):

    def test_sentinels_and_a_stale_forecast_become_none(self):
        _patch(self, nwps, lambda *a, **k: {"gauges": [_gauge("A", 2.3, "no_flooding")]})
        row = nwps.gauges((-96, 29, -95, 30)).iloc[0]
        self.assertEqual(row["stage"], 2.3)
        self.assertIsNone(row["flow"])
        self.assertIsNone(row["forecast_stage"])
        self.assertIsNone(row["forecast_at"])

    def test_a_current_forecast_keeps_its_own_time(self):
        _patch(self, nwps, lambda *a, **k: {"gauges": [
            _gauge("A", 20.0, "action", 29.0, "minor")]})
        row = nwps.gauges((-96, 29, -95, 30)).iloc[0]
        self.assertEqual(row["forecast_stage"], 29.0)
        self.assertEqual(row["forecast_category"], "minor")
        self.assertEqual(row["forecast_at"], "2026-10-30T12:00:00Z")

    def test_flooding_only_and_the_count(self):
        _patch(self, nwps, lambda *a, **k: {"gauges": [
            _gauge("A", 2.3, "no_flooding"), _gauge("B", 30.0, "moderate")]})
        df = nwps.gauges((-96, 29, -95, 30), flooding_only=True)
        self.assertEqual(list(df["lid"]), ["B"])
        self.assertEqual(df.attrs["flooding_now"], 1)

    def test_a_backwards_bbox_fails_before_any_request(self):
        _patch(self, nwps, mock.Mock(side_effect=AssertionError("no request")))
        with self.assertRaises(ValueError):
            nwps.gauges((-95, 30, -96, 29))

    def test_gauge_flood_stages_with_an_unset_one(self):
        g = _gauge("BBST2", 2.26, "no_flooding")
        g["flood"] = {"categories": {"action": {"stage": 17}, "minor": {"stage": 28},
                                     "moderate": {"stage": 29.5}, "major": {"stage": -9999}}}
        _patch(self, nwps, lambda *a, **k: g)
        stages = nwps.gauge("BBST2")["flood_stages"]
        self.assertEqual(stages["minor"], 28.0)
        self.assertIsNone(stages["major"])


# ---------------------------------------------------------------------------
# coops
# ---------------------------------------------------------------------------

class CoopsTest(unittest.TestCase):

    def setUp(self):
        coops._STATIONS_CACHE.clear()
        self.addCleanup(coops._STATIONS_CACHE.clear)

    def test_nearest_within_a_distance(self):
        _patch(self, coops, lambda *a, **k: {"stations": [
            {"id": "8771450", "name": "Galveston Pier 21", "lat": 29.31, "lng": -94.7933},
            {"id": "far", "name": "Far away", "lat": 40.0, "lng": -70.0}]})
        df = coops.nearest(-94.79, 29.31)
        self.assertEqual(list(df["station"]), ["8771450"])
        self.assertLess(df.iloc[0]["distance_km"], 1)

    def test_water_levels_keep_the_datum_and_the_unit(self):
        calls = []

        def fake(url, params=None, timeout=None, accept=None):
            calls.append(params)
            return {"metadata": {"name": "Galveston Pier 21", "lat": "29.31", "lon": "-94.79"},
                    "data": [{"t": "2026-10-28 19:30", "v": "1.184", "q": "p", "f": "0,0,0,0"},
                             {"t": "2026-10-28 19:36", "v": "", "q": "p", "f": "0,0,0,0"}]}
        _patch(self, coops, fake)
        df = coops.water_levels("8771450", hours=6, datum="NAVD")
        self.assertEqual(df.attrs["datum"], "NAVD")
        self.assertEqual(df.iloc[0]["water_level_ft"], 1.184)
        self.assertTrue(df["water_level_ft"].isna().iloc[1])     # a blank reading is missing, not 0
        self.assertEqual(calls[0]["time_zone"], "gmt")

    def test_an_error_under_http_200_is_raised(self):
        _patch(self, coops, lambda *a, **k: {"error": {"message": "No data was found."}})
        with self.assertRaises(_http.NOAAError) as ctx:
            coops.water_levels("8771450")
        self.assertIn("No data was found", str(ctx.exception))

    def test_predictions_take_dates_in_either_form(self):
        calls = []

        def fake(url, params=None, timeout=None, accept=None):
            calls.append(params)
            return {"predictions": [{"t": "2026-11-01 05:56", "v": "1.878", "type": "H"}]}
        _patch(self, coops, fake)
        df = coops.predictions("8771450", "2026-11-01", "20261102")
        self.assertEqual((calls[0]["begin_date"], calls[0]["end_date"]), ("20261101", "20261102"))
        self.assertEqual(df.iloc[0]["type"], "H")

    def test_a_bad_date_fails_before_any_request(self):
        _patch(self, coops, mock.Mock(side_effect=AssertionError("no request")))
        with self.assertRaises(ValueError):
            coops.predictions("8771450", "next week", "2026-11-02")


# ---------------------------------------------------------------------------
# nhc
# ---------------------------------------------------------------------------

class NhcTest(unittest.TestCase):

    def test_a_storm_row(self):
        _patch(self, nhc, lambda *a, **k: {"activeStorms": [{
            "id": "ep222026", "name": "Vance", "classification": "TS", "intensity": "35",
            "pressure": "1004", "latitudeNumeric": 19.2, "longitudeNumeric": -109.2,
            "movementDir": 315, "movementSpeed": 7, "lastUpdate": "2026-10-28T15:00:00.000Z",
            "publicAdvisory": {"url": "https://www.nhc.noaa.gov/text/x.shtml"},
            "trackCone": {"zipFile": "https://www.nhc.noaa.gov/gis/x.zip"}}]})
        row = nhc.active_storms().iloc[0]
        self.assertEqual(row["classification_name"], "Tropical Storm")
        self.assertEqual(row["wind_kt"], 35.0)
        self.assertEqual(row["lon"], -109.2)
        self.assertTrue(row["cone_url"].endswith(".zip"))

    def test_no_storms_still_has_the_columns(self):
        _patch(self, nhc, lambda *a, **k: {"activeStorms": []})
        df = nhc.active_storms()
        self.assertEqual(len(df), 0)
        self.assertIn("wind_kt", df.columns)


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TransportTest(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.object(_http, "_sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, answers):
        answers = list(answers)

        def fake(req, timeout=None):
            got = answers.pop(0)
            if isinstance(got, BaseException):
                raise got
            return _Resp(got)
        with mock.patch.object(_http, "_urlopen", fake):
            return _http.fetch_json("https://api.weather.gov/x")

    def _http_error(self, code, body=b""):
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))

    def test_a_500_from_the_nws_is_retried(self):
        self.assertEqual(self._run([self._http_error(500), b'{"ok": 1}']), {"ok": 1})

    def test_a_404_carries_its_status_and_the_detail(self):
        with self.assertRaises(_http.NOAAError) as ctx:
            self._run([self._http_error(404, b'{"detail": "Unable to provide data"}')])
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("Unable to provide data", str(ctx.exception))

    def test_a_reset_is_retried_and_a_refusal_is_not(self):
        reset = urllib.error.URLError(ConnectionResetError(10054, "reset"))
        self.assertEqual(self._run([reset, b'{"ok": 1}']), {"ok": 1})
        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        with self.assertRaises(_http.NOAAError) as ctx:
            self._run([refused])
        self.assertIsNone(ctx.exception.status)


if __name__ == "__main__":
    unittest.main()
