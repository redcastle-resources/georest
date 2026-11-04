"""`cloud/stac.py`: the catalog table and the collection list, offline.

`support.FakeStacServer` serves a collection list with the paging real servers
do (NASA sends 20 at a time and a GET `next` link), so the paging, the word
filter, the featured ordering and the cap are exercised without a network.
"""
from __future__ import annotations

import unittest

from georest.cloud import catalogs, stac
from georest.cloud._http import CloudError

from .support import FakeStacServer, patched_stac, stac_collection


def many(n, prefix="c"):
    return [stac_collection(f"{prefix}{i:03d}", title=f"Collection {i}") for i in range(n)]


class TestCatalogs(unittest.TestCase):
    def test_one_row_per_registered_catalog(self):
        df = stac.catalogs()
        self.assertEqual(list(df["name"]), catalogs.names())
        self.assertEqual(list(df.columns), ["name", "title", "url", "assets", "notes"])

    def test_no_network_is_touched(self):
        with patched_stac(fetch_json=lambda *a, **k: self.fail("catalogs() made a request")):
            self.assertGreater(len(stac.catalogs()), 0)


class TestCollectionsPaging(unittest.TestCase):
    def test_every_page_is_followed_to_the_end(self):
        server = FakeStacServer(many(45), page_cap=20)
        with patched_stac(fetch_json=server):
            df = stac.collections("https://stac.example.org/api")
        self.assertEqual(len(df), 45)
        self.assertEqual(len(server.urls), 3)
        self.assertFalse(df.attrs["truncated"])

    def test_the_first_request_asks_for_a_page_size_and_later_ones_use_the_link(self):
        server = FakeStacServer(many(45), page_cap=20)
        with patched_stac(fetch_json=server):
            stac.collections("https://stac.example.org/api")
        self.assertIn("limit=100", server.urls[0])
        self.assertIn("cursor=20", server.urls[1])
        self.assertNotIn("limit", server.urls[1], "the next link is followed as the server gave it")

    def test_a_relative_next_link_is_resolved_against_the_catalog(self):
        server = FakeStacServer(many(30), page_cap=20, relative_links=True)
        with patched_stac(fetch_json=server):
            df = stac.collections("https://stac.example.org/api")
        self.assertEqual(len(df), 30)
        self.assertTrue(server.urls[1].startswith("https://stac.example.org/api/collections?"))

    def test_max_items_stops_the_fetch_and_says_so(self):
        server = FakeStacServer(many(45), page_cap=20)
        with patched_stac(fetch_json=server):
            df = stac.collections("https://stac.example.org/api", max_items=30)
        self.assertEqual(len(df), 30)
        self.assertTrue(df.attrs["truncated"])
        self.assertEqual(len(server.urls), 2, "it stops asking once it has enough")

    def test_a_cap_that_equals_the_catalog_size_is_not_truncation(self):
        server = FakeStacServer(many(40), page_cap=20)
        with patched_stac(fetch_json=server):
            df = stac.collections("https://stac.example.org/api", max_items=40)
        self.assertEqual(len(df), 40)
        self.assertFalse(df.attrs["truncated"])

    def test_a_page_that_overshoots_the_cap_is_cut_and_flagged(self):
        server = FakeStacServer(many(10), page_cap=10)
        with patched_stac(fetch_json=server):
            df = stac.collections("https://stac.example.org/api", max_items=4)
        self.assertEqual(len(df), 4)
        self.assertTrue(df.attrs["truncated"])

    def test_a_repeating_next_link_cannot_loop_forever(self):
        looping = {"collections": [stac_collection("a")],
                   "links": [{"rel": "next", "href": "https://stac.example.org/api/collections"}]}
        calls = []

        def fetch(url, params=None, **kw):
            calls.append(url)
            return looping

        with patched_stac(fetch_json=fetch):
            df = stac.collections("https://stac.example.org/api")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(df), 1)

    def test_a_post_next_link_is_not_followed_as_a_get(self):
        page = {"collections": [stac_collection("a")],
                "links": [{"rel": "next", "method": "POST", "href": "https://x/collections"}]}
        with patched_stac(fetch_json=lambda *a, **k: page):
            df = stac.collections("https://stac.example.org/api")
        self.assertEqual(len(df), 1)

    def test_max_items_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "max_items"):
            stac.collections("earth-search", max_items=0)


class TestCollectionRows(unittest.TestCase):
    def run_one(self, collection, catalog="https://stac.example.org/api", query=""):
        with patched_stac(fetch_json=FakeStacServer([collection])):
            return stac.collections(catalog, query)

    def test_columns_and_a_plain_row(self):
        df = self.run_one(stac_collection("sentinel-2-l2a", title="Sentinel-2 L2A",
                                          description="Surface reflectance.",
                                          start="2015-06-27T00:00:00Z", end=None,
                                          license="CC-BY-4.0"))
        self.assertEqual(list(df.columns), ["catalog", "id", "title", "summary", "license",
                                            "start", "end", "assets", "featured", "note"])
        row = df.iloc[0]
        self.assertEqual(row["id"], "sentinel-2-l2a")
        self.assertEqual(row["summary"], "Surface reflectance.")
        self.assertEqual(row["start"], "2015-06-27T00:00:00Z")
        self.assertIsNone(row["end"])
        self.assertEqual(row["license"], "CC-BY-4.0")
        self.assertFalse(row["featured"])

    def test_a_long_markdown_description_becomes_a_short_plain_summary(self):
        text = ("The [National Agriculture Imagery Program](https://example.org/naip) "
                "acquires aerial imagery. " + "More words. " * 80 + "\n\nSecond paragraph.")
        summary = self.run_one(stac_collection("naip", description=text)).iloc[0]["summary"]
        self.assertTrue(summary.startswith("The National Agriculture Imagery Program acquires"))
        self.assertNotIn("](", summary)
        self.assertNotIn("Second paragraph", summary)
        self.assertLessEqual(len(summary), stac._SUMMARY_CHARS + 3)
        self.assertTrue(summary.endswith("..."))

    def test_a_missing_description_or_extent_is_none_not_an_error(self):
        row = self.run_one({"id": "bare"}).iloc[0]
        self.assertIsNone(row["summary"])
        self.assertIsNone(row["start"])
        self.assertIsNone(row["end"])
        self.assertIsNone(row["title"])

    def test_non_dict_entries_are_ignored(self):
        server = FakeStacServer([stac_collection("a"), "junk", None])
        with patched_stac(fetch_json=server):
            self.assertEqual(list(stac.collections("https://stac.example.org/api")["id"]), ["a"])

    def test_assets_defaults_to_the_catalogs_and_unknown_urls_say_unknown(self):
        self.assertEqual(self.run_one(stac_collection("a")).iloc[0]["assets"], "unknown")
        df = self.run_one(stac_collection("sentinel-2-l2a"), catalog="planetary-computer")
        self.assertEqual(df.iloc[0]["assets"], "sign")

    def test_a_collection_can_override_its_catalogs_assets(self):
        df = self.run_one(stac_collection("naip"), catalog="earth-search")
        self.assertEqual(df.iloc[0]["assets"], "requester-pays")
        self.assertTrue(df.iloc[0]["featured"])
        self.assertIn("NAIP", df.iloc[0]["note"])


class TestQuery(unittest.TestCase):
    CATALOG = [
        stac_collection("burn-severity", title="Burn Severity", description="Fire effects."),
        stac_collection("elevation", title="Elevation", description="Terrain heights."),
        stac_collection("wildfire-perimeters", title="Wildfire Perimeters",
                        description="Where fires burned.", keywords=["fire", "forest"]),
        stac_collection("hotspots", title="Hotspots", description="Active fire detections."),
    ]

    def ids(self, query):
        with patched_stac(fetch_json=FakeStacServer(self.CATALOG)):
            return list(stac.collections("https://stac.example.org/api", query)["id"])

    def test_a_word_matches_id_title_description_and_keywords(self):
        self.assertEqual(set(self.ids("fire")),
                         {"burn-severity", "wildfire-perimeters", "hotspots"})
        self.assertEqual(self.ids("forest"), ["wildfire-perimeters"])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(self.ids("ELEVATION"), ["elevation"])

    def test_several_words_must_all_appear(self):
        self.assertEqual(self.ids("fire active"), ["hotspots"])
        self.assertEqual(self.ids("fire elevation"), [])

    def test_a_word_in_the_id_or_title_ranks_above_one_only_in_the_description(self):
        ids = self.ids("fire")
        self.assertEqual(ids[0], "wildfire-perimeters", "its id holds the word")
        self.assertLess(ids.index("wildfire-perimeters"), ids.index("burn-severity"))

    def test_an_empty_query_lists_everything_in_server_order(self):
        self.assertEqual(self.ids(""), [c["id"] for c in self.CATALOG])

    def test_no_match_is_an_empty_table_with_columns(self):
        with patched_stac(fetch_json=FakeStacServer(self.CATALOG)):
            df = stac.collections("https://stac.example.org/api", "zzz")
        self.assertEqual(len(df), 0)
        self.assertIn("id", df.columns)
        self.assertFalse(df.attrs["truncated"])

    def test_featured_collections_come_first(self):
        mixed = [stac_collection("other"), stac_collection("mtbs", title="MTBS"),
                 stac_collection("another")]
        with patched_stac(fetch_json=FakeStacServer(mixed)):
            df = stac.collections("planetary-computer")
        self.assertEqual(list(df["id"]), ["mtbs", "other", "another"])
        self.assertEqual(list(df["featured"]), [True, False, False])


class TestErrors(unittest.TestCase):
    def test_an_unknown_catalog_name_is_a_value_error_before_any_request(self):
        with patched_stac(fetch_json=lambda *a, **k: self.fail("requested")):
            with self.assertRaisesRegex(ValueError, "unknown catalog"):
                stac.collections("not-a-catalog")

    def test_a_url_that_is_not_a_stac_api_says_so(self):
        with patched_stac(fetch_json=lambda *a, **k: {"hello": "world"}):
            with self.assertRaisesRegex(ValueError, "root URL of a STAC API"):
                stac.collections("https://example.org/not-stac")

    def test_a_transport_failure_is_the_cloud_error_unchanged(self):
        def boom(*a, **k):
            raise CloudError("Request failed (503): https://x", status=503)

        with patched_stac(fetch_json=boom):
            with self.assertRaises(CloudError) as caught:
                stac.collections("earth-search")
        self.assertEqual(caught.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
