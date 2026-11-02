"""Offline tests for georest.fema: the NFHL client and its transport.

FEMA's answers are stood in for here; tests/test_live_fema.py asks the real
service (FEMA_LIVE=1).
"""
from __future__ import annotations

import io
import unittest
import urllib.error
from datetime import date, datetime, timezone
from unittest import mock

from georest.fema import _http, nfhl


def _ms(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


class _Fema:
    """Answers each NFHL query from ``rows`` keyed by "<service>/<layer>";
    a key in ``fail`` raises the way a failed request does."""

    def __init__(self, rows=None, fail=()):
        self.rows, self.fail, self.calls = rows or {}, set(fail), []

    def __call__(self, url, params=None, timeout=None):
        service = ("Pending" if "Pending_NFHL" in url else
                   "Prelim" if "Prelim_NFHL" in url else "NFHL")
        key = f"{service}/{url.rstrip('/').split('/')[-2]}"
        self.calls.append((key, dict(params or {})))
        if key in self.fail:
            raise RuntimeError(f"Request error: connection reset: {url}")
        return {"features": [{"attributes": a} for a in self.rows.get(key, [])]}


class FloodZoneTest(unittest.TestCase):

    def _zone(self, rows, **kw):
        fema = _Fema(rows, **kw)
        with mock.patch.object(nfhl, "fetch_json", fema):
            return nfhl.flood_zone(-80.13, 25.79), fema

    def test_a_zone_comes_back_with_its_meaning(self):
        z, fema = self._zone({"NFHL/28": [{
            "FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T",
            "STATIC_BFE": 8.0, "DEPTH": -9999, "LEN_UNIT": "Feet",
            "V_DATUM": "NAVD88", "DFIRM_ID": "12086C"}]})
        self.assertEqual(z["zone"], "AE")
        self.assertTrue(z["sfha"])
        self.assertEqual(z["static_bfe"], 8.0)
        self.assertIsNone(z["depth"])                 # -9999 is FEMA's "none"
        self.assertTrue(z["mapped"])
        self.assertEqual(z["zones"], [])

    def test_the_point_is_sent_longitude_first_with_no_geometry_back(self):
        _z, fema = self._zone({"NFHL/28": [{"FLD_ZONE": "X", "SFHA_TF": "F"}]})
        params = fema.calls[0][1]
        self.assertEqual(params["geometry"], "-80.13,25.79")
        self.assertEqual(params["geometryType"], "esriGeometryPoint")
        self.assertEqual(params["returnGeometry"], "false")

    def test_no_zone_and_no_map_says_unmapped_not_safe(self):
        z, _ = self._zone({})
        self.assertIsNone(z["zone"])
        self.assertFalse(z["mapped"])

    def test_no_zone_inside_a_mapped_study_says_mapped(self):
        z, _ = self._zone({"NFHL/0": [{"STUDY_ID": "12086C"}]})
        self.assertIsNone(z["zone"])
        self.assertTrue(z["mapped"])

    def test_open_water_gives_way_to_a_real_zone(self):
        z, _ = self._zone({"NFHL/28": [{"FLD_ZONE": "OPEN WATER"},
                                       {"FLD_ZONE": "VE", "SFHA_TF": "T"}]})
        self.assertEqual(z["zone"], "VE")

    def test_a_point_on_a_boundary_lists_every_zone(self):
        z, _ = self._zone({"NFHL/28": [
            {"FLD_ZONE": "AE", "ZONE_SUBTY": "FLOODWAY", "SFHA_TF": "T"},
            {"FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T"}]})
        self.assertEqual(len(z["zones"]), 2)

    def test_a_failed_query_is_raised_never_read_as_no_zone(self):
        with self.assertRaises(RuntimeError):
            self._zone({}, fail={"NFHL/28"})

    def test_bad_coordinates_fail_before_any_request(self):
        fema = _Fema()
        with mock.patch.object(nfhl, "fetch_json", fema):
            with self.assertRaises(ValueError):
                nfhl.flood_zone(25.79, -180.5)
        self.assertEqual(fema.calls, [])


class MapDatesTest(unittest.TestCase):

    def _dates(self, rows, **kw):
        with mock.patch.object(nfhl, "fetch_json", _Fema(rows, **kw)):
            return nfhl.map_dates(-80.13, 25.79)

    def test_the_newest_panel_and_its_date(self):
        d = self._dates({"NFHL/3": [
            {"FIRM_PAN": "48201C0690L", "EFF_DATE": _ms(2007, 6, 18)},
            {"FIRM_PAN": "48201C0690N", "EFF_DATE": _ms(2017, 1, 6)}]})
        self.assertEqual(d["panel"], "48201C0690N")
        self.assertEqual(d["effective"], date(2017, 1, 6))
        self.assertEqual(d["unchecked"], [])

    def test_a_revision_after_the_panel_counts_and_an_older_one_does_not(self):
        d = self._dates({"NFHL/3": [{"FIRM_PAN": "P", "EFF_DATE": _ms(2010, 1, 1)}],
                         "NFHL/1": [{"EFF_DATE": _ms(2005, 1, 1), "STATUS": "Effective"},
                                    {"EFF_DATE": _ms(2021, 8, 2), "STATUS": "Effective"}]})
        self.assertEqual(d["revised"], date(2021, 8, 2))

    def test_superseded_revisions_are_ignored(self):
        d = self._dates({"NFHL/3": [{"FIRM_PAN": "P", "EFF_DATE": _ms(2010, 1, 1)}],
                         "NFHL/1": [{"EFF_DATE": _ms(2021, 8, 2), "STATUS": "Superseded"}]})
        self.assertIsNone(d["revised"])

    def test_pending_and_preliminary_maps(self):
        d = self._dates({"NFHL/3": [{"FIRM_PAN": "12086C0317L", "EFF_DATE": _ms(2009, 9, 11)}],
                         "Pending/0": [{"EFF_DATE": _ms(2027, 2, 19)}],
                         "Prelim/0": [{"PRELM_ISSUE_DATE": _ms(2021, 2, 25)}]})
        self.assertEqual(d["pending_effective"], date(2027, 2, 19))
        self.assertEqual(d["preliminary_issued"], date(2021, 2, 25))

    def test_a_failed_lookup_is_named_not_read_as_none(self):
        d = self._dates({"NFHL/3": [{"FIRM_PAN": "P", "EFF_DATE": _ms(2017, 1, 6)}]},
                        fail={"Pending/0"})
        self.assertIsNone(d["pending_effective"])
        self.assertEqual(d["unchecked"], ["pending_effective"])
        self.assertEqual(d["effective"], date(2017, 1, 6))


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
            return _http.fetch_json("https://hazards.fema.gov/x/query", {"f": "json"})

    def test_a_reset_connection_is_retried(self):
        reset = urllib.error.URLError(ConnectionResetError(10054, "reset"))
        self.assertEqual(self._run([reset, reset, b'{"features": []}']),
                         {"features": []})
        self.assertEqual(self.sleep.call_count, 2)

    def test_resets_that_never_stop_are_raised(self):
        reset = urllib.error.URLError(ConnectionResetError(10054, "reset"))
        with self.assertRaises(RuntimeError):
            self._run([reset] * _http._RETRIES)

    def test_a_refused_connection_is_not_retried(self):
        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        with self.assertRaises(RuntimeError):
            self._run([refused])
        self.sleep.assert_not_called()

    def test_an_error_object_under_http_200_is_raised(self):
        body = b'{"error": {"code": 500, "message": "Service not started"}}'
        with self.assertRaises(RuntimeError) as ctx:
            self._run([body])
        self.assertIn("Service not started", str(ctx.exception))

    def test_a_gateway_error_is_retried_and_a_500_is_not(self):
        def http_error(code):
            return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))
        self.assertEqual(self._run([http_error(503), b'{"ok": 1}']), {"ok": 1})
        with self.assertRaises(RuntimeError):
            self._run([http_error(500)])

    def test_a_body_that_is_not_json_is_a_value_error(self):
        with self.assertRaises(ValueError):
            self._run([b"<html>maintenance</html>"])


if __name__ == "__main__":
    unittest.main()
