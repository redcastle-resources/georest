"""Live tests for georest.fema against hazards.fema.gov (FEMA_LIVE=1).

The points are chosen for answers that change rarely: an AE zone on Miami
Beach with new maps adopted for 2027, a minimal-hazard X zone in downtown
Houston, and the open Pacific, where FEMA has no map. A server that is
down skips rather than fails.
"""
from __future__ import annotations

import unittest
from datetime import date

from georest.fema import nfhl

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


if __name__ == "__main__":
    unittest.main()
