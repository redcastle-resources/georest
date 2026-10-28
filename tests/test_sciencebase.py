"""georest.usgs.sciencebase, offline: paging, limits, filters and rows."""

import unittest
from unittest import mock

from georest.usgs import sciencebase as sb


def _item(i, **extra):
    return {"id": f"id{i}", "title": f"item {i}", "summary": "s", "hasChildren": False,
            "systemTypes": ["Data Release"], "files": [{}, {}],
            "link": {"url": f"https://sb/{i}"},
            "dates": [{"type": "Publication", "dateString": "2021-02-24"},
                      {"type": "lastUpdated", "dateString": "2025-01-01T00:00:00"}], **extra}


class ScienceBaseTest(unittest.TestCase):
    def serve(self, handler):
        calls = []

        def fake(url, params=None, **kwargs):
            calls.append((url, dict(params or {})))
            return handler(url, dict(params or {}))

        patcher = mock.patch.object(sb, "fetch_json", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def paged(self, n):
        items = [_item(i) for i in range(n)]

        def handler(url, params):
            start, size = params.get("offset", 0), params["max"]
            return {"total": n, "items": items[start:start + size]}
        return handler

    def test_every_page_is_fetched(self):
        with mock.patch.object(sb, "PAGE_SIZE", 3):
            calls = self.serve(self.paged(7))
            df = sb.search("streamflow")
        self.assertEqual(list(df["id"]), [f"id{i}" for i in range(7)])
        self.assertEqual([p["offset"] for _, p in calls], [0, 3, 6])

    def test_limit_stops_early_and_asks_only_for_what_is_left(self):
        with mock.patch.object(sb, "PAGE_SIZE", 3):
            calls = self.serve(self.paged(10))
            df = sb.search("streamflow", limit=5)
        self.assertEqual(len(df), 5)
        self.assertEqual([p["max"] for _, p in calls], [3, 2])

    def test_filters_are_sent(self):
        calls = self.serve(lambda url, p: {"total": 0, "items": []})
        sb.search("fire", data_releases_only=True, parent_id="abc")
        self.assertEqual(calls[0][1]["filter"], ["systemType=Data Release", "parentId=abc"])
        sb.search("fire", data_releases_only=True)
        self.assertEqual(calls[1][1]["filter"], "systemType=Data Release")

    def test_rows_flatten_dates_types_and_files(self):
        self.serve(lambda url, p: {"total": 1, "items": [_item(1)]})
        row = sb.search("x").iloc[0]
        self.assertEqual((row["published"], row["last_updated"]), ("2021-02-24", "2025-01-01T00:00:00"))
        self.assertEqual((row["item_type"], row["files"], row["url"]), ("Data Release", 2, "https://sb/1"))

    def test_no_matches_still_has_the_columns(self):
        self.serve(lambda url, p: {"total": 0, "items": []})
        df = sb.search("nothing matches this")
        self.assertTrue(df.empty)
        self.assertIn("title", df.columns)

    def test_a_bad_limit_fails_before_any_request(self):
        calls = self.serve(lambda url, p: {})
        with self.assertRaises(ValueError):
            sb.search("x", limit=0)
        self.assertEqual(calls, [])

    def test_files_have_sizes_in_megabytes(self):
        self.serve(lambda url, p: {"files": [{"name": "a.zip", "size": 2_500_000,
                                              "contentType": "application/zip", "url": "https://f/a"}]})
        df = sb.item_files("id1")
        self.assertEqual(df.to_dict("records"),
                         [{"name": "a.zip", "size_mb": 2.5, "content_type": "application/zip",
                           "url": "https://f/a"}])

    def test_an_item_carries_its_details(self):
        self.serve(lambda url, p: _item(1, body="<p>b</p>", parentId="p1",
                                        tags=[{"name": "fire"}],
                                        contacts=[{"name": "USGS", "type": "Publisher"}],
                                        webLinks=[{"title": "Home", "uri": "https://w"}],
                                        spatial={"boundingBox": {"minX": -1, "minY": -2,
                                                                 "maxX": 3, "maxY": 4}}))
        item = sb.get_item("id1")
        self.assertEqual((item["tags"], item["bbox"], item["parent_id"]), (["fire"], (-1, -2, 3, 4), "p1"))
        self.assertEqual(item["contacts"], [{"name": "USGS", "role": "Publisher"}])

    def test_an_unknown_item_is_a_clear_error(self):
        self.serve(lambda url, p: {})
        with self.assertRaisesRegex(ValueError, "no ScienceBase item"):
            sb.get_item("nope")


if __name__ == "__main__":
    unittest.main()
