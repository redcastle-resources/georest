"""`cloud/catalogs.py`: the registry's shape and the name/URL resolution rules.

Offline. That each registered collection still exists on its server is the
live test's job (`test_live_stac.py`); this holds the registry itself to the
rules the rest of the package relies on.
"""
from __future__ import annotations

import unittest

from georest.cloud import catalogs


class TestRegistryShape(unittest.TestCase):
    def test_every_entry_has_the_fields_the_client_reads(self):
        for name, entry in catalogs.CATALOGS.items():
            with self.subTest(catalog=name):
                for key in ("title", "url", "assets", "notes", "collections"):
                    self.assertIn(key, entry)
                self.assertTrue(entry["url"].startswith("https://"))
                self.assertFalse(entry["url"].endswith("/"), "urls are stored without a trailing slash")

    def test_every_assets_value_is_one_the_docs_explain(self):
        for name, entry in catalogs.CATALOGS.items():
            with self.subTest(catalog=name):
                self.assertIn(entry["assets"], catalogs.ASSET_ACCESS)
            for cid, meta in entry["collections"].items():
                with self.subTest(catalog=name, collection=cid):
                    self.assertIn(meta.get("assets", entry["assets"]), catalogs.ASSET_ACCESS)

    def test_every_featured_collection_has_a_note(self):
        for name, entry in catalogs.CATALOGS.items():
            for cid, meta in entry["collections"].items():
                with self.subTest(catalog=name, collection=cid):
                    self.assertTrue(meta.get("note"), "a featured collection says what it holds")

    def test_urls_are_unique(self):
        urls = [e["url"] for e in catalogs.CATALOGS.values()]
        self.assertEqual(len(urls), len(set(urls)))

    def test_the_catalogs_named_in_the_plan_are_present(self):
        self.assertEqual(
            set(catalogs.names()),
            {"earth-search", "planetary-computer", "landsatlook", "nasa-lpcloud",
             "nasa-ornl-cloud"})

    def test_how_files_are_read_matches_what_was_observed(self):
        """Verified against the live servers when the registry was written:
        Planetary Computer refuses unsigned reads, NASA's downloads answer 401
        without a login, and Landsat and NAIP on Earth Search are s3:// links."""
        c = catalogs.CATALOGS
        self.assertEqual(c["planetary-computer"]["assets"], "sign")
        self.assertEqual(c["nasa-lpcloud"]["assets"], "earthdata")
        self.assertEqual(c["earth-search"]["assets"], "open")
        self.assertEqual(c["earth-search"]["collections"]["landsat-c2-l2"]["assets"],
                         "requester-pays")
        self.assertEqual(c["earth-search"]["collections"]["naip"]["assets"], "requester-pays")


class TestResolve(unittest.TestCase):
    def test_a_name_resolves_to_its_entry(self):
        entry = catalogs.resolve("earth-search")
        self.assertEqual(entry["name"], "earth-search")
        self.assertEqual(entry["url"], catalogs.CATALOGS["earth-search"]["url"])

    def test_a_registered_url_resolves_to_its_name_even_with_a_trailing_slash(self):
        url = catalogs.CATALOGS["landsatlook"]["url"]
        self.assertEqual(catalogs.resolve(url + "/")["name"], "landsatlook")
        self.assertEqual(catalogs.resolve(url)["assets"], "open")

    def test_an_unregistered_url_is_accepted_and_marked_unknown(self):
        entry = catalogs.resolve("https://stac.example.org/api/")
        self.assertEqual(entry["url"], "https://stac.example.org/api")
        self.assertEqual(entry["assets"], "unknown")
        self.assertEqual(entry["collections"], {})

    def test_an_unknown_name_lists_the_valid_ones(self):
        with self.assertRaises(ValueError) as caught:
            catalogs.resolve("nope")
        message = str(caught.exception)
        for name in catalogs.names():
            self.assertIn(name, message)
        self.assertIn("root URL", message)

    def test_blank_is_an_error_not_a_default(self):
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                with self.assertRaises(ValueError):
                    catalogs.resolve(blank)

    def test_resolve_does_not_let_a_caller_edit_the_registry(self):
        entry = catalogs.resolve("earth-search")
        entry["name"] = "changed"
        self.assertEqual(catalogs.resolve("earth-search")["name"], "earth-search")


if __name__ == "__main__":
    unittest.main()
