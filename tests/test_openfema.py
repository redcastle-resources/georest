"""Offline tests for georest.fema.openfema (disaster declarations).

OpenFEMA's answers are stood in for here; tests/test_live_fema.py asks the
real service (FEMA_LIVE=1).
"""
from __future__ import annotations

import unittest
from unittest import mock

from georest.fema import openfema


def _decl(number, area, fips="48201", date="2024-07-09", incident="Hurricane", rid=None):
    return {"disasterNumber": number, "femaDeclarationString": f"DR-{number}-TX",
            "declarationType": "DR", "declarationDate": f"{date}T00:00:00.000Z",
            "state": "TX", "designatedArea": area, "fipsStateCode": fips[:2],
            "fipsCountyCode": fips[2:], "incidentType": incident,
            "declarationTitle": "HURRICANE BERYL",
            "incidentBeginDate": "2024-07-05T00:00:00.000Z",
            "incidentEndDate": "2024-07-09T00:00:00.000Z",
            "ihProgramDeclared": True, "iaProgramDeclared": False,
            "paProgramDeclared": True, "hmProgramDeclared": True,
            "disasterCloseoutDate": None, "id": rid or f"{number}-{area}"}


class _OpenFema:
    """Serves ``rows`` a page at a time, honoring $skip/$top, and reports the
    count only when $count=true is sent (as OpenFEMA does)."""

    def __init__(self, rows, count=None):
        self.rows, self.count, self.calls = rows, count, []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append(dict(params))
        skip, top = int(params.get("$skip", 0)), int(params.get("$top", 1000))
        count = (len(self.rows) if self.count is None else self.count)
        return {"metadata": {"count": count if params.get("$count") == "true" else 0},
                "DisasterDeclarationsSummaries": self.rows[skip:skip + top]}


class DeclarationsTest(unittest.TestCase):

    def _run(self, fake, **kw):
        with mock.patch.object(openfema, "fetch_json", fake):
            return openfema.declarations(**kw)

    def test_every_page_is_fetched(self):
        rows = [_decl(4798, f"County {i}") for i in range(7)]
        fake = _OpenFema(rows)
        with mock.patch.object(openfema, "_PAGE_SIZE", 3):
            df = self._run(fake, state="TX")
        self.assertEqual(len(df), 7)
        self.assertEqual([c["$skip"] for c in fake.calls], ["0", "3", "6"])

    def test_a_short_read_is_raised_not_returned(self):
        fake = _OpenFema([_decl(4798, "A")], count=5)
        with self.assertRaises(RuntimeError) as ctx:
            self._run(fake, state="TX")
        self.assertIn("1 of 5", str(ctx.exception))

    def test_the_count_is_asked_for(self):
        fake = _OpenFema([_decl(4798, "A")])
        self._run(fake, state="TX")
        self.assertEqual(fake.calls[0]["$count"], "true")

    def test_the_filter(self):
        fake = _OpenFema([])
        self._run(fake, state="tx", fips="48201", incident_type="Hurricane",
                  declaration_type="dr", since="2017-01-01", until="2024-12-31")
        where = fake.calls[0]["$filter"]
        for part in ("state eq 'TX'", "fipsStateCode eq '48' and fipsCountyCode eq '201'",
                     "incidentType eq 'Hurricane'", "declarationType eq 'DR'",
                     "declarationDate ge '2017-01-01T00:00:00.000Z'",
                     "declarationDate le '2024-12-31T00:00:00.000Z'"):
            self.assertIn(part, where)

    def test_a_quote_in_a_value_is_escaped(self):
        fake = _OpenFema([])
        self._run(fake, incident_type="Dam/Levee Break's")
        self.assertIn("'Dam/Levee Break''s'", fake.calls[0]["$filter"])

    def test_rows_get_dates_and_a_fips(self):
        df = self._run(_OpenFema([_decl(4798, "Harris (County)")]), state="TX")
        row = df.iloc[0]
        self.assertEqual(row["declaration_date"], "2024-07-09")
        self.assertEqual(row["fips"], "48201")
        self.assertNotIn("fips_state", df.columns)

    def test_no_match_still_has_the_columns(self):
        df = self._run(_OpenFema([]), state="TX", since="2099-01-01")
        self.assertEqual(len(df), 0)
        self.assertIn("declaration_string", df.columns)

    def test_bad_arguments_fail_before_any_request(self):
        fake = mock.Mock(side_effect=AssertionError("no request"))
        for kw in ({"fips": "4820"}, {"declaration_type": "XX"}, {"since": "last year"}):
            with self.subTest(**kw), self.assertRaises(ValueError):
                self._run(fake, **kw)


class DisastersTest(unittest.TestCase):

    def test_rows_are_grouped_into_disasters_with_distinct_areas(self):
        rows = [_decl(4798, "Harris (County)", rid="a"),
                _decl(4798, "Galveston (County)", fips="48167", rid="b"),
                _decl(4798, "Galveston (County)", fips="48167", rid="c"),   # a repeat record
                _decl(4781, "Harris (County)", date="2024-05-17", incident="Flood", rid="d")]
        with mock.patch.object(openfema, "fetch_json", _OpenFema(rows)):
            df = openfema.disasters(state="TX")
        self.assertEqual(list(df["disaster_number"]), [4798, 4781])     # newest first
        self.assertEqual(list(df["areas"]), [2, 1])

    def test_no_match_still_has_the_columns(self):
        with mock.patch.object(openfema, "fetch_json", _OpenFema([])):
            df = openfema.disasters(state="TX")
        self.assertEqual(list(df.columns), list(openfema._DISASTER_COLUMNS))


if __name__ == "__main__":
    unittest.main()
