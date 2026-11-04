"""Live tests for georest.cloud (STAC_LIVE=1).

Shapes and registry drift, never today's scenes. The one test that matters
most is the drift check: every collection the registry names must still exist
in its catalog, so a catalog that renames or retires one fails here and the
registry gets corrected. A server that is down skips rather than fails.
"""
from __future__ import annotations

import unittest

from georest.cloud import catalogs, stac

from .support import requires_live_stac, retry


@requires_live_stac
class LiveStacTests(unittest.TestCase):

    def test_every_registered_collection_still_exists(self):
        for name, entry in catalogs.CATALOGS.items():
            with self.subTest(catalog=name):
                df = retry(lambda: stac.collections(name, max_items=5000))
                if df.attrs["truncated"]:
                    self.skipTest(f"{name} has more than 5000 collections; cannot check")
                missing = sorted(set(entry["collections"]) - set(df["id"]))
                self.assertEqual(missing, [], f"{name}: registered collections not found")

    def test_planetary_computer_lists_its_forest_service_collections(self):
        df = retry(lambda: stac.collections("planetary-computer", "forest"))
        self.assertIn("fia", set(df["id"]))
        self.assertTrue(df.loc[df["id"] == "fia", "featured"].all())

    def test_a_word_search_on_a_paged_catalog_finds_hls(self):
        df = retry(lambda: stac.collections("nasa-lpcloud", "harmonized landsat"))
        self.assertTrue({"HLSS30_2.0", "HLSL30_2.0"} <= set(df["id"]))

    def test_a_capped_listing_says_it_was_cut(self):
        df = retry(lambda: stac.collections("nasa-ornl-cloud", max_items=25))
        self.assertEqual(len(df), 25)
        self.assertTrue(df.attrs["truncated"])

    def test_a_catalog_given_by_url_resolves_to_its_registered_name(self):
        url = catalogs.CATALOGS["landsatlook"]["url"]
        df = retry(lambda: stac.collections(url, "surface reflectance"))
        self.assertEqual(set(df["catalog"]), {"landsatlook"})
        self.assertIn("landsat-c2l2-sr", set(df["id"]))


if __name__ == "__main__":
    unittest.main()
