"""georest.nifc.perimeters against the real NIFC services (SERVICES_LIVE=1).

Known fires with known numbers, so a change in NIFC's schema or a broken
query shows up as a failure rather than an empty list.
"""
from __future__ import annotations

import unittest

from georest.nifc import perimeters as p

from .support import requires_live_services


@requires_live_services
class LiveNifcTest(unittest.TestCase):

    def test_a_recent_fire_has_its_dates(self):
        [r] = p.find_fires("Monroe Canyon", year=2025, state="UT")
        self.assertEqual(r["discovered"], "2025-07-13")
        self.assertEqual(r["contained"], "2025-09-04")
        self.assertAlmostEqual(r["acres"], 73720, delta=500)
        self.assertEqual(r["source_url"], p.WFIGS_URL)

    def test_a_shared_name_lists_every_fire_largest_first(self):
        rows = p.find_fires("Dixie", year=2021)
        self.assertEqual(rows[0]["state"], "CA")
        self.assertIn("ID", [r["state"] for r in rows])

    def test_an_old_fire_comes_from_the_history(self):
        rows = p.find_fires("Hayman", year=2002)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["source_url"], p.HISTORY_URL)

    def test_the_perimeter_is_a_generalized_polygon(self):
        fc = p.fire_perimeter("Cameron Peak", year=2020)
        geom = fc["features"][0]["geometry"]
        self.assertIn(geom["type"], ("Polygon", "MultiPolygon"))
        self.assertEqual(fc["features"][0]["properties"]["state"], "CO")


if __name__ == "__main__":
    unittest.main()
