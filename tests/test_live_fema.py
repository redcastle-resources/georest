"""Live tests for georest.fema against hazards.fema.gov (FEMA_LIVE=1).

The points are chosen for answers that change rarely: an AE zone on Miami
Beach with new maps adopted for 2027, a minimal-hazard X zone in downtown
Houston, and the open Pacific, where FEMA has no map. A server that is
down skips rather than fails.
"""
from __future__ import annotations

import unittest
from datetime import date

from georest.fema import nfhl, openfema

from .support import requires_live_fema, retry


@requires_live_fema
class LiveNfhlTests(unittest.TestCase):

    def test_miami_beach_is_in_the_special_flood_hazard_area(self):
        z = retry(lambda: nfhl.flood_zone(-80.1300, 25.7907))
        self.assertTrue(z["mapped"])
        self.assertTrue(z["sfha"])
        self.assertTrue(z["zone"].startswith(("A", "V")))

    def test_miami_beach_has_new_maps_coming(self):
        d = retry(lambda: nfhl.map_dates(-80.1300, 25.7907))
        self.assertEqual(d["unchecked"], [])
        self.assertTrue(d["panel"].startswith("12086C"))
        self.assertIsInstance(d["effective"], date)
        # Adopted for 2027-02-19 when this was written; once they take
        # effect this becomes the effective date instead.
        self.assertTrue(d["pending_effective"] or d["effective"] >= date(2027, 2, 19))

    def test_downtown_houston_is_minimal_hazard(self):
        z = retry(lambda: nfhl.flood_zone(-95.3698, 29.7604))
        self.assertEqual(z["zone"], "X")
        self.assertFalse(z["sfha"])

    def test_the_open_pacific_is_unmapped(self):
        z = retry(lambda: nfhl.flood_zone(-157.0, 30.0))
        self.assertIsNone(z["zone"])
        self.assertFalse(z["mapped"])


@requires_live_fema
class LiveOpenFemaTests(unittest.TestCase):

    def test_harvey_is_one_disaster_over_many_counties(self):
        rows = retry(lambda: openfema.declarations(number=4332))
        self.assertGreaterEqual(len(rows), 50)
        self.assertEqual(set(rows["declaration_string"]), {"DR-4332-TX"})

    def test_harris_county_had_harvey(self):
        df = retry(lambda: openfema.disasters(fips="48201", since="2017-08-01",
                                              until="2017-09-30"))
        self.assertIn(4332, list(df["disaster_number"]))

    def test_grouping_never_has_more_disasters_than_rows(self):
        rows = retry(lambda: openfema.declarations(state="TX", since="2024-01-01"))
        grouped = retry(lambda: openfema.disasters(state="TX", since="2024-01-01"))
        self.assertLessEqual(len(grouped), len(rows))
        self.assertEqual(len(grouped), rows["disaster_number"].nunique())


if __name__ == "__main__":
    unittest.main()
