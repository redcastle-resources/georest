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

    # -- search, assets, footprints, sign: shapes and behaviour, never today's scenes --

    BBOX = (-114.2, 46.8, -113.8, 47.1)   # Missoula, MT: covered by every catalog below

    def test_a_search_returns_flat_rows_newest_first(self):
        df = retry(lambda: stac.search("earth-search", "sentinel-2-l2a", bbox=self.BBOX,
                                       datetime="2024-06-01/2024-09-30", max_cloud_cover=20,
                                       max_items=10))
        self.assertEqual(len(df), 10)
        self.assertTrue(df.attrs["truncated"])
        self.assertTrue((df["cloud_cover"] <= 20).all())
        self.assertEqual(list(df["datetime"]), sorted(df["datetime"], reverse=True))
        self.assertTrue(all(c.startswith("2024-") for c in df["datetime"]))

    def test_paging_runs_past_one_page_on_a_post_linked_catalog(self):
        df = retry(lambda: stac.search("earth-search", "sentinel-2-l2a", bbox=self.BBOX,
                                       datetime="2024-01-01/2024-12-31", max_items=120))
        self.assertEqual(len(df), 120)
        self.assertEqual(len(set(df["id"])), 120, "no scene repeated across pages")

    def test_paging_follows_nasas_get_links_to_the_end(self):
        df = retry(lambda: stac.search("nasa-lpcloud", "HLSS30_2.0", bbox=self.BBOX,
                                       datetime="2024-06-01/2024-09-30", max_items=500))
        self.assertGreater(len(df), 50, "more than one page of 50")
        self.assertFalse(df.attrs["truncated"])
        self.assertEqual(len(df), df.attrs["matched"])

    def test_a_polygon_search_and_its_footprints(self):
        polygon = {"type": "Polygon", "coordinates": [[[-114.2, 46.8], [-113.8, 46.8],
                                                       [-113.8, 47.1], [-114.2, 47.1],
                                                       [-114.2, 46.8]]]}
        df = retry(lambda: stac.search("landsatlook", "landsat-c2l2-sr", intersects=polygon,
                                       datetime="2024-07-01/2024-08-31", max_items=5))
        self.assertGreater(len(df), 0)
        fc = stac.footprints(df)
        self.assertEqual(len(fc["features"]), len(df))
        self.assertEqual(fc["features"][0]["geometry"]["type"], "Polygon")

    def test_a_scenes_files_are_listed_and_a_planetary_computer_link_signs(self):
        scene = retry(lambda: stac.search("planetary-computer", "sentinel-2-l2a",
                                          bbox=self.BBOX, max_items=1)).iloc[0]
        files = retry(lambda: stac.assets("planetary-computer", "sentinel-2-l2a", scene["id"]))
        self.assertIn("B04", set(files["key"]))
        self.assertEqual(set(files["access"]), {"sign"})
        link = files.loc[files["key"] == "B04", "href"].iloc[0]
        signed = retry(lambda: stac.sign(link, "sentinel-2-l2a"))
        self.assertTrue(signed.startswith(link + "?"))
        self.assertIn("sig=", signed)

    def test_an_unknown_collection_is_an_empty_table_not_an_error(self):
        """Earth Search answers a mistyped collection id with zero results. The docs
        say so, and tell the caller to check ids with stac.collections()."""
        df = retry(lambda: stac.search("earth-search", "no-such-collection-zzz",
                                       bbox=self.BBOX, max_items=1))
        self.assertEqual(len(df), 0)
        self.assertIn("id", df.columns)


if __name__ == "__main__":
    unittest.main()
