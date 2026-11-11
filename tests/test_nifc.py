"""Offline tests for georest.nifc.perimeters.

NIFC's answers are stood in for here; tests/test_live_nifc.py asks the
real services (SERVICES_LIVE=1).
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from georest.nifc import perimeters as p


def _ms(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def _wf(oid, name, acres, state, disc, cont=None, out=None, final=True, irwin="A"):
    return {"OBJECTID": oid, "poly_IncidentName": name, "poly_GISAcres": acres,
            "poly_FeatureCategory": p._FINAL if final else "Wildfire Daily Fire Perimeter",
            "attr_FireDiscoveryDateTime": _ms(*disc), "attr_ContainmentDateTime": cont and _ms(*cont),
            "attr_FireOutDateTime": out and _ms(*out), "attr_POOState": f"US-{state}",
            "attr_IrwinID": "{" + irwin + "}"}


def _hist(oid, name, acres, year, unit="", irwin=""):
    return {"OBJECTID": oid, "INCIDENT": name, "GIS_ACRES": acres, "FIRE_YEAR_INT": year,
            "UNIT_ID": unit, "IRWINID": irwin}


class _Nifc:
    """Answers attribute queries from ``wfigs``/``history`` rows: exact-name
    queries get ``exact`` rows, LIKE queries get ``like`` rows. Geometry
    queries answer one square."""

    def __init__(self, wfigs=(), history=(), like_wfigs=(), like_history=()):
        self.rows = {("W", True): list(wfigs), ("H", True): list(history),
                     ("W", False): list(like_wfigs), ("H", False): list(like_history)}
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if params.get("f") == "geojson":
            return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}]}
        which = "W" if url.startswith(p.WFIGS_URL) else "H"
        exact = " LIKE " not in params["where"]
        return {"features": [{"attributes": a} for a in self.rows[(which, exact)]]}


class FindFiresTest(unittest.TestCase):

    def _find(self, nifc, *a, **kw):
        with mock.patch.object(p, "fetch_json", nifc):
            return p.find_fires(*a, **kw)

    def test_a_fire_comes_back_with_its_dates(self):
        nifc = _Nifc(wfigs=[_wf(7, "Monroe Canyon", 73720.6, "UT", (2025, 7, 13), (2025, 9, 4), (2025, 10, 27))])
        [r] = self._find(nifc, "Monroe Canyon Fire", year=2025)
        self.assertEqual((r["name"], r["year"], r["state"]), ("Monroe Canyon", 2025, "UT"))
        self.assertEqual((r["discovered"], r["contained"], r["fire_out"]),
                         ("2025-07-13", "2025-09-04", "2025-10-27"))
        self.assertTrue(r["final"])
        self.assertEqual(r["irwin_id"], "A")

    def test_the_name_is_cleaned_and_quoted_safely(self):
        nifc = _Nifc()
        self._find(nifc, "  O'Brien   Fire ", year=2021)
        where = nifc.calls[0][1]["where"]
        self.assertIn("UPPER('O''Brien')", where)       # "Fire" dropped, quote doubled
        self.assertIn("DATE '2021-01-01'", where)
        self.assertIn("DATE '2022-01-01'", where)

    def test_no_name_is_refused(self):
        with self.assertRaises(ValueError):
            p.find_fires("   ")

    def test_one_row_per_fire_preferring_the_final_perimeter(self):
        nifc = _Nifc(wfigs=[
            _wf(1, "Dixie", 900000, "CA", (2021, 7, 14), final=False, irwin="X"),
            _wf(2, "Dixie", 963309, "CA", (2021, 7, 14), (2021, 10, 25), irwin="X"),
            _wf(3, "Dixie", 43789, "ID", (2021, 7, 5), irwin="Y")])
        rows = self._find(nifc, "Dixie", year=2021)
        self.assertEqual([(r["state"], r["object_id"]) for r in rows], [("CA", 2), ("ID", 3)])

    def test_state_narrows_by_code_or_name(self):
        nifc = _Nifc(wfigs=[_wf(2, "Dixie", 963309, "CA", (2021, 7, 14), irwin="X"),
                            _wf(3, "Dixie", 43789, "ID", (2021, 7, 5), irwin="Y")])
        self.assertEqual([r["state"] for r in self._find(nifc, "Dixie", 2021, "Idaho")], ["ID"])
        self.assertEqual([r["state"] for r in self._find(nifc, "Dixie", 2021, "us-id")], ["ID"])
        with self.assertRaises(ValueError):
            p.find_fires("Dixie", 2021, "Atlantis")

    def test_wfigs_wins_over_history_for_the_same_fire(self):
        nifc = _Nifc(wfigs=[_wf(2, "Cameron Peak", 208913, "CO", (2020, 8, 13), irwin="C")],
                     history=[_hist(9, "CAMERON PEAK", 208600, 2020, "COARF")])
        [r] = self._find(nifc, "Cameron Peak", year=2020)
        self.assertEqual(r["source_url"], p.WFIGS_URL)

    def test_history_alone_for_years_before_wfigs(self):
        nifc = _Nifc(history=[_hist(9, "Hayman", 138287, 2002), _hist(10, "Hayman", 137760, 2002, "COPSF")])
        rows = self._find(nifc, "Hayman", year=2002)
        self.assertFalse(any(u.startswith(p.WFIGS_URL) for u, _ in nifc.calls))
        # One fire held twice: merged, keeping the copy that knew its state.
        self.assertEqual([(r["acres"], r["state"]) for r in rows], [(138287, "CO")])
        self.assertIsNone(rows[0]["discovered"])

    def test_a_partial_name_is_tried_when_nothing_matches_exactly(self):
        nifc = _Nifc(like_wfigs=[_wf(5, "Monroe Canyon", 73720, "UT", (2025, 7, 13))])
        [r] = self._find(nifc, "Monroe", year=2025)
        self.assertEqual(r["name"], "Monroe Canyon")
        self.assertTrue(any(" LIKE " in c[1]["where"] for c in nifc.calls))

    def test_largest_first_and_min_acres(self):
        nifc = _Nifc(wfigs=[_wf(1, "Palisades", 5, "WA", (2022, 8, 26), irwin="S"),
                            _wf(2, "Palisades", 23448, "CA", (2025, 1, 7), irwin="L")])
        self.assertEqual([r["acres"] for r in self._find(nifc, "Palisades")], [23448, 5])
        self.assertEqual([r["acres"] for r in self._find(nifc, "Palisades", min_acres=100)], [23448])

    def test_a_service_error_is_raised_not_swallowed(self):
        def broken(url, params=None, timeout=None):
            return {"error": {"code": 400, "message": "Invalid query"}}
        with mock.patch.object(p, "fetch_json", broken):
            with self.assertRaisesRegex(ValueError, "Invalid query"):
                p.find_fires("Dixie", 2021)


class FirePerimeterTest(unittest.TestCase):

    def _perim(self, nifc, *a, **kw):
        with mock.patch.object(p, "fetch_json", nifc):
            return p.fire_perimeter(*a, **kw)

    def test_the_largest_fire_with_the_others_as_candidates(self):
        nifc = _Nifc(wfigs=[_wf(2, "Dixie", 963309, "CA", (2021, 7, 14), irwin="X"),
                            _wf(3, "Dixie", 43789, "ID", (2021, 7, 5), irwin="Y")])
        fc = self._perim(nifc, "Dixie", year=2021)
        self.assertEqual(fc["type"], "FeatureCollection")
        [f] = fc["features"]
        self.assertEqual(f["properties"]["state"], "CA")
        self.assertEqual([c["state"] for c in fc["candidates"]], ["ID"])
        self.assertEqual(fc["source_url"], p.WFIGS_URL)
        geo = nifc.calls[-1][1]
        self.assertEqual((geo["where"], geo["outSR"], geo["f"]), ("OBJECTID = 2", "4326", "geojson"))

    def test_the_outline_is_generalized_unless_asked_not_to(self):
        nifc = _Nifc(wfigs=[_wf(2, "Dixie", 963309, "CA", (2021, 7, 14))])
        fc = self._perim(nifc, "Dixie", 2021)
        self.assertAlmostEqual(float(nifc.calls[-1][1]["maxAllowableOffset"]), 10 / 111_320)
        self.assertEqual(fc["features"][0]["properties"]["generalized_m"], 10)
        fc = self._perim(nifc, "Dixie", 2021, max_offset_m=None)
        self.assertNotIn("maxAllowableOffset", nifc.calls[-1][1])
        self.assertEqual(fc["features"][0]["properties"]["generalized_m"], 0)

    def test_no_match_is_a_lookup_error_naming_what_was_asked(self):
        with self.assertRaisesRegex(LookupError, "Nope.*2020.*UT"):
            self._perim(_Nifc(), "Nope", year=2020, state="UT")

    def test_no_geometry_is_a_lookup_error(self):
        nifc = _Nifc(wfigs=[_wf(2, "Dixie", 963309, "CA", (2021, 7, 14))])
        real = nifc.__call__

        def no_geom(url, params=None, timeout=None):
            if (params or {}).get("f") == "geojson":
                return {"features": []}
            return real(url, params, timeout)
        with mock.patch.object(p, "fetch_json", no_geom):
            with self.assertRaisesRegex(LookupError, "no geometry"):
                p.fire_perimeter("Dixie", 2021)


if __name__ == "__main__":
    unittest.main()
