"""`cloud/stac.py` search, assets, footprints and sign, offline.

`support.FakeStacSearch` serves a search endpoint with the paging styles real
servers use (a POST next link with a body to merge, a plain GET next link), so
the request bodies, the paging, the truncation flag and the checks on what a
caller passes are all exercised without a network.
"""
from __future__ import annotations

import datetime as dt
import json
import unittest

from georest.cloud import stac
from georest.cloud._http import CloudError

from .support import FakeStacSearch, patched_stac, stac_item

BBOX = (-114.2, 46.8, -113.8, 47.1)
ROOT = "https://stac.example.org/api"


def run(server, *args, **kwargs):
    """`stac.search` against a fake server."""
    with patched_stac(post_json_body=server.post_json_body, fetch_json=server.fetch_json):
        return stac.search(*args, **kwargs)


def scenes(n, **extra):
    return [stac_item(f"s{i:03d}", **extra) for i in range(n)]


def square(x, y, size=0.1):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + size, y], [x + size, y + size],
                                                 [x, y]]]}


class TestRequestBody(unittest.TestCase):
    def body(self, **kwargs):
        server = FakeStacSearch(scenes(3))
        run(server, ROOT, kwargs.pop("collections", "sentinel-2-l2a"), **kwargs)
        return server.bodies()[0]

    def test_a_search_is_a_post_to_the_catalogs_search_endpoint(self):
        server = FakeStacSearch(scenes(1))
        run(server, "earth-search", "sentinel-2-l2a", bbox=BBOX)
        method, url, _ = server.calls[0]
        self.assertEqual((method, url), ("POST", "https://earth-search.aws.element84.com/v1/search"))

    def test_collections_bbox_and_a_page_size_are_sent(self):
        body = self.body(bbox=BBOX, max_items=10)
        self.assertEqual(body["collections"], ["sentinel-2-l2a"])
        self.assertEqual(body["bbox"], list(BBOX))
        self.assertEqual(body["limit"], 11, "one extra, to tell a full result from a cut one")

    def test_the_page_size_never_exceeds_what_nasa_serves(self):
        self.assertEqual(self.body(bbox=BBOX, max_items=1000)["limit"], stac._SEARCH_PAGE_SIZE)

    def test_a_list_of_collections_is_sent_as_given(self):
        body = self.body(collections=["hls2-l30", "hls2-s30"], bbox=BBOX)
        self.assertEqual(body["collections"], ["hls2-l30", "hls2-s30"])

    def test_newest_first_is_the_default_and_can_be_turned_off(self):
        self.assertEqual(self.body(bbox=BBOX)["sortby"],
                         [{"field": "properties.datetime", "direction": "desc"}])
        self.assertNotIn("sortby", self.body(bbox=BBOX, newest_first=False))

    def test_cloud_cover_is_sent_as_a_query_and_merged_with_the_callers(self):
        body = self.body(bbox=BBOX, max_cloud_cover=20,
                         query={"platform": {"eq": "sentinel-2a"}})
        self.assertEqual(body["query"], {"platform": {"eq": "sentinel-2a"},
                                         "eo:cloud_cover": {"lte": 20.0}})

    def test_nothing_optional_is_sent_when_not_asked_for(self):
        body = self.body()
        for key in ("bbox", "intersects", "datetime", "query"):
            self.assertNotIn(key, body)

    def test_the_callers_query_dict_is_not_edited(self):
        mine = {"platform": {"eq": "x"}}
        self.body(bbox=BBOX, max_cloud_cover=10, query=mine)
        self.assertEqual(mine, {"platform": {"eq": "x"}})


class TestDatetime(unittest.TestCase):
    def sent(self, value):
        server = FakeStacSearch(scenes(1))
        run(server, ROOT, "c", datetime=value)
        return server.bodies()[0]["datetime"]

    def test_a_day_range_covers_both_whole_days(self):
        self.assertEqual(self.sent("2024-06-01/2024-09-30"),
                         "2024-06-01T00:00:00Z/2024-09-30T23:59:59Z")

    def test_a_pair_works_like_the_slash_form(self):
        self.assertEqual(self.sent(("2024-06-01", "2024-06-30")),
                         "2024-06-01T00:00:00Z/2024-06-30T23:59:59Z")

    def test_a_single_day_is_that_whole_day(self):
        self.assertEqual(self.sent("2024-07-15"), "2024-07-15T00:00:00Z/2024-07-15T23:59:59Z")

    def test_dates_and_datetimes_are_accepted(self):
        self.assertEqual(self.sent((dt.date(2024, 6, 1), dt.datetime(2024, 6, 2, 12, 30))),
                         "2024-06-01T00:00:00Z/2024-06-02T12:30:00Z")

    def test_an_aware_datetime_is_converted_to_utc(self):
        east = dt.timezone(dt.timedelta(hours=2))
        got = self.sent((dt.datetime(2024, 6, 1, 2, 0, tzinfo=east), None))
        self.assertEqual(got, "2024-06-01T00:00:00Z/..")

    def test_an_open_end_is_dots(self):
        self.assertEqual(self.sent("2024-06-01/.."), "2024-06-01T00:00:00Z/..")
        self.assertEqual(self.sent((None, "2024-06-30")), "../2024-06-30T23:59:59Z")

    def test_a_full_timestamp_passes_through(self):
        self.assertEqual(self.sent("2024-06-01T10:30:00Z/2024-06-01T11:30:00Z"),
                         "2024-06-01T10:30:00Z/2024-06-01T11:30:00Z")

    def test_blank_means_no_filter(self):
        server = FakeStacSearch(scenes(1))
        run(server, ROOT, "c", datetime="  ")
        self.assertNotIn("datetime", server.bodies()[0])

    def test_unusable_values_say_what_a_date_looks_like(self):
        for bad, message in [("June 2024", "not a date"), ("2024-13-45/2024-14-01", "not a date"),
                             ("../..", "open at both ends"), ((None, None), "open at both ends"),
                             ("2024-09-30/2024-06-01", "after it ends"),
                             ("a/b/c", "start/end"), (("2024-01-01",), "pair")]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, message):
                    stac._datetime_range(bad)


class TestRows(unittest.TestCase):
    def test_columns_and_a_plain_row(self):
        df = run(FakeStacSearch(scenes(1)), ROOT, "sentinel-2-l2a", bbox=BBOX)
        self.assertEqual(list(df.columns), ["catalog", "collection", "id", "datetime",
                                            "cloud_cover", "platform", "assets", "thumbnail",
                                            "bbox", "geometry"])
        row = df.iloc[0]
        self.assertEqual((row["id"], row["collection"], row["platform"]),
                         ("s000", "sentinel-2-l2a", "sentinel-2a"))
        self.assertEqual(row["datetime"], "2024-07-01T18:00:00Z")
        self.assertEqual(row["cloud_cover"], 5.0)
        self.assertEqual(row["thumbnail"], "https://img.example.org/s000.jpg")
        self.assertEqual(row["bbox"], list(BBOX))
        self.assertEqual(row["geometry"]["type"], "Polygon")
        self.assertEqual(row["assets"], "unknown", "an unregistered catalog's files are unknown")

    def test_how_files_are_read_comes_from_the_registry(self):
        server = FakeStacSearch([stac_item("a", collection="landsat-c2-l2")])
        df = run(server, "earth-search", "landsat-c2-l2")
        self.assertEqual(df.iloc[0]["assets"], "requester-pays")
        df = run(FakeStacSearch([stac_item("a")]), "planetary-computer", "sentinel-2-l2a")
        self.assertEqual(df.iloc[0]["assets"], "sign")
        self.assertEqual(df.iloc[0]["catalog"], "planetary-computer")

    def test_a_scene_with_no_cloud_value_has_nan_not_an_error(self):
        item = stac_item("a")
        del item["properties"]["eo:cloud_cover"]
        df = run(FakeStacSearch([item, stac_item("b", cloud=3)]), ROOT, "c")
        self.assertTrue(df["cloud_cover"].isna().iloc[0])
        self.assertEqual(df["cloud_cover"].iloc[1], 3.0)

    def test_start_datetime_stands_in_when_datetime_is_null(self):
        item = stac_item("a")
        item["properties"].update(datetime=None, start_datetime="2024-05-01T00:00:00Z")
        self.assertEqual(run(FakeStacSearch([item]), ROOT, "c").iloc[0]["datetime"],
                         "2024-05-01T00:00:00Z")

    def test_a_thumbnail_on_s3_is_not_offered_as_a_link(self):
        item = stac_item("a", assets={"thumbnail": {"href": "s3://bucket/t.jpg"}})
        self.assertIsNone(run(FakeStacSearch([item]), ROOT, "c").iloc[0]["thumbnail"])

    def test_a_bare_item_is_a_row_of_nones_not_an_error(self):
        df = run(FakeStacSearch([{"id": "bare"}]), ROOT, "c")
        row = df.iloc[0]
        self.assertEqual(row["id"], "bare")
        self.assertIsNone(row["bbox"])
        self.assertIsNone(row["geometry"])
        self.assertIsNone(row["thumbnail"])

    def test_non_dict_features_are_ignored(self):
        self.assertEqual(len(run(FakeStacSearch([stac_item("a"), "junk", None]), ROOT, "c")), 1)

    def test_no_match_is_an_empty_table_with_columns(self):
        df = run(FakeStacSearch([]), ROOT, "c")
        self.assertEqual(len(df), 0)
        self.assertIn("cloud_cover", df.columns)
        self.assertFalse(df.attrs["truncated"])
        self.assertEqual(df.attrs["matched"], 0)

    def test_a_six_number_bbox_is_trimmed_to_four(self):
        item = stac_item("a")
        item["bbox"] = [-114.2, 46.8, 0, -113.8, 47.1, 100]
        self.assertEqual(len(run(FakeStacSearch([item]), ROOT, "c").iloc[0]["bbox"]), 4)


class TestPagingAndTruncation(unittest.TestCase):
    def test_every_page_is_followed_with_a_post_link(self):
        server = FakeStacSearch(scenes(120), page_cap=50, paging="post")
        df = run(server, ROOT, "c", max_items=500)
        self.assertEqual(len(df), 120)
        self.assertEqual(len(server.calls), 3)
        self.assertFalse(df.attrs["truncated"])
        self.assertEqual(list(df["id"]), [f"s{i:03d}" for i in range(120)])

    def test_the_next_link_body_is_merged_into_the_original(self):
        server = FakeStacSearch(scenes(120), page_cap=50, paging="post")
        run(server, ROOT, "c", bbox=BBOX, datetime="2024-07-01", max_items=500)
        first, second = server.bodies()[:2]
        self.assertEqual(second["offset"], 50)
        for key in ("collections", "bbox", "datetime", "limit"):
            self.assertEqual(second[key], first[key], "the filter must not be lost on page 2")

    def test_a_get_next_link_is_followed(self):
        server = FakeStacSearch(scenes(120), page_cap=50, paging="get")
        df = run(server, ROOT, "c", max_items=500)
        self.assertEqual(len(df), 120)
        self.assertEqual([c[0] for c in server.calls], ["POST", "GET", "GET"])

    def test_max_items_stops_the_fetch_and_says_so(self):
        server = FakeStacSearch(scenes(120), page_cap=50)
        df = run(server, ROOT, "c", max_items=60)
        self.assertEqual(len(df), 60)
        self.assertTrue(df.attrs["truncated"])
        self.assertEqual(len(server.calls), 2, "it stops asking once it has enough")
        self.assertEqual(list(df["id"])[-1], "s059", "the first scenes in server order are kept")

    def test_a_result_exactly_the_size_of_the_cap_is_not_truncation(self):
        for paging in ("post", "get"):
            with self.subTest(paging=paging):
                df = run(FakeStacSearch(scenes(60), page_cap=50, paging=paging), ROOT, "c",
                         max_items=60)
                self.assertEqual(len(df), 60)
                self.assertFalse(df.attrs["truncated"])

    def test_one_more_than_the_cap_is_truncation(self):
        df = run(FakeStacSearch(scenes(61), page_cap=50), ROOT, "c", max_items=60)
        self.assertEqual(len(df), 60)
        self.assertTrue(df.attrs["truncated"])

    def test_a_server_that_ignores_the_limit_is_still_cut_to_max_items(self):
        df = run(FakeStacSearch(scenes(40), page_cap=1000), ROOT, "c", max_items=5)
        self.assertEqual(len(df), 5)
        self.assertTrue(df.attrs["truncated"])

    def test_matched_is_the_servers_count_wherever_it_puts_it(self):
        for where in ("numberMatched", "context"):
            with self.subTest(where=where):
                df = run(FakeStacSearch(scenes(7), matched_in=where), ROOT, "c", max_items=3)
                self.assertEqual(df.attrs["matched"], 7)
        self.assertIsNone(run(FakeStacSearch(scenes(7), matched_in=None), ROOT, "c")
                          .attrs["matched"])

    def test_a_repeating_next_link_cannot_loop_forever(self):
        calls = []
        looping = {"features": [stac_item("a")],
                   "links": [{"rel": "next", "method": "POST", "href": f"{ROOT}/search",
                              "body": {"collections": ["c"], "limit": 51}}]}

        def post(url, body, **kw):
            calls.append(url)
            return looping

        with patched_stac(post_json_body=post):
            df = stac.search(ROOT, "c")
        self.assertLessEqual(len(calls), 2)
        self.assertGreaterEqual(len(df), 1)

    def test_a_next_link_with_no_body_repeats_the_original(self):
        sent = []
        pages = iter([{"features": [stac_item("a")],
                       "links": [{"rel": "next", "method": "POST", "href": f"{ROOT}/search?p=2"}]},
                      {"features": [stac_item("b")], "links": []}])

        def post(url, body, **kw):
            sent.append((url, body))
            return next(pages)

        with patched_stac(post_json_body=post):
            df = stac.search(ROOT, "c", bbox=BBOX)
        self.assertEqual(list(df["id"]), ["a", "b"])
        self.assertEqual(sent[1][1]["bbox"], list(BBOX))

    def test_a_relative_next_link_is_resolved_against_the_request(self):
        seen = []
        pages = iter([{"features": [stac_item("a")],
                       "links": [{"rel": "next", "href": "search?offset=1"}]},
                      {"features": [stac_item("b")], "links": []}])

        def post(url, body, **kw):
            return next(pages)

        def get(url, *a, **kw):
            seen.append(url)
            return next(pages)

        with patched_stac(post_json_body=post, fetch_json=get):
            stac.search(ROOT, "c")
        self.assertEqual(seen, [f"{ROOT}/search?offset=1"])


class TestCloudCoverCheck(unittest.TestCase):
    def test_a_server_that_honours_the_query_returns_only_clear_scenes(self):
        items = [stac_item("clear", cloud=5), stac_item("cloudy", cloud=80)]
        df = run(FakeStacSearch(items), ROOT, "c", max_cloud_cover=20)
        self.assertEqual(list(df["id"]), ["clear"])

    def test_a_server_that_ignores_the_query_is_checked_here(self):
        items = [stac_item("clear", cloud=5), stac_item("cloudy", cloud=80)]
        df = run(FakeStacSearch(items, honor_query=False), ROOT, "c", max_cloud_cover=20)
        self.assertEqual(list(df["id"]), ["clear"])

    def test_filtered_out_scenes_do_not_count_toward_max_items_or_matched(self):
        items = [stac_item(f"c{i}", cloud=90) for i in range(30)] + scenes(3, cloud=2)
        df = run(FakeStacSearch(items, honor_query=False, page_cap=10), ROOT, "c",
                 max_cloud_cover=20, max_items=3)
        self.assertEqual(len(df), 3)
        self.assertFalse(df.attrs["truncated"])
        self.assertIsNone(df.attrs["matched"], "the server's count includes scenes dropped here")

    def test_a_scene_with_no_cloud_value_is_not_dropped_by_the_check(self):
        item = stac_item("a")
        del item["properties"]["eo:cloud_cover"]
        df = run(FakeStacSearch([item], honor_query=False), ROOT, "c", max_cloud_cover=10)
        self.assertEqual(len(df), 1)


class TestArgumentChecks(unittest.TestCase):
    def fail(self, message, *args, **kwargs):
        with patched_stac(post_json_body=lambda *a, **k: self.fail("requested")):
            with self.assertRaisesRegex(ValueError, message):
                stac.search(*args, **kwargs)

    def test_a_search_must_name_a_collection(self):
        for blank in ("", "  ", [], None, [" "]):
            with self.subTest(blank=blank):
                self.fail("at least one collection", ROOT, blank)

    def test_bbox_and_intersects_are_exclusive(self):
        self.fail("not both", ROOT, "c", bbox=BBOX, intersects=square(-114, 46))

    def test_a_bad_bbox_says_what_a_bbox_is(self):
        self.fail("west, south, east, north", ROOT, "c", bbox=(1, 2, 3))
        self.fail("west < east", ROOT, "c", bbox=(-113.8, 46.8, -114.2, 47.1))

    def test_max_items_is_bounded(self):
        for bad in (0, -1, 5001, 1.5, "10", True):
            with self.subTest(bad=bad):
                self.fail("max_items", ROOT, "c", max_items=bad)

    def test_the_largest_allowed_max_items_is_accepted(self):
        server = FakeStacSearch(scenes(2))
        self.assertEqual(len(run(server, ROOT, "c", max_items=5000)), 2)

    def test_cloud_cover_is_a_percentage(self):
        for bad in (-1, 101):
            with self.subTest(bad=bad):
                self.fail("percentage", ROOT, "c", max_cloud_cover=bad)

    def test_an_unknown_catalog_name_fails_before_any_request(self):
        self.fail("unknown catalog", "nope", "c")

    def test_a_reply_that_is_not_search_results_says_so(self):
        for reply in ({"hello": "world"}, [], {"features": "x"}):
            with self.subTest(reply=reply):
                with patched_stac(post_json_body=lambda *a, **k: reply):
                    with self.assertRaisesRegex(ValueError, "root URL of a STAC API"):
                        stac.search(ROOT, "c")

    def test_a_server_refusal_keeps_its_explanation(self):
        def refuse(*a, **k):
            raise CloudError("Request failed (400): https://x\nUnknown collection 'zzz'",
                             status=400)

        with patched_stac(post_json_body=refuse):
            with self.assertRaises(CloudError) as caught:
                stac.search(ROOT, "zzz")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("Unknown collection", str(caught.exception))


class TestIntersects(unittest.TestCase):
    def sent(self, value):
        server = FakeStacSearch(scenes(1))
        run(server, ROOT, "c", intersects=value)
        return server.bodies()[0]["intersects"]

    def test_a_geometry_goes_through_unchanged(self):
        g = square(-114, 46)
        self.assertEqual(self.sent(g), g)

    def test_a_feature_gives_up_its_geometry(self):
        g = square(-114, 46)
        self.assertEqual(self.sent({"type": "Feature", "properties": {"n": 1}, "geometry": g}), g)

    def test_a_one_feature_collection_is_that_feature(self):
        g = square(-114, 46)
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g}]}
        self.assertEqual(self.sent(fc), g)

    def test_several_polygons_become_one_multipolygon(self):
        a, b = square(-114, 46), {"type": "MultiPolygon", "coordinates": [
            square(-110, 45)["coordinates"], square(-109, 45)["coordinates"]]}
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": a}, {"type": "Feature", "geometry": b}]}
        got = self.sent(fc)
        self.assertEqual(got["type"], "MultiPolygon")
        self.assertEqual(len(got["coordinates"]), 3)

    def test_mixed_shapes_become_a_geometry_collection(self):
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": square(-114, 46)},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-113, 46]}}]}
        self.assertEqual(self.sent(fc)["type"], "GeometryCollection")

    def test_features_without_a_shape_are_skipped(self):
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None},
            {"type": "Feature", "geometry": square(-114, 46)}]}
        self.assertEqual(self.sent(fc), square(-114, 46))

    def test_geojson_text_is_accepted(self):
        self.assertEqual(self.sent(json.dumps(square(-114, 46))), square(-114, 46))

    def test_a_point_is_a_valid_place(self):
        point = {"type": "Point", "coordinates": [-113.9, 46.9]}
        self.assertEqual(self.sent(point), point)

    def test_a_query_that_found_nothing_says_so(self):
        for empty in ({"type": "FeatureCollection", "features": []},
                      {"type": "FeatureCollection", "features": [{"type": "Feature",
                                                                 "geometry": None}]}):
            with self.subTest(empty=empty):
                with self.assertRaisesRegex(ValueError, "no features with a shape"):
                    stac._geometry_of(empty)

    def test_unusable_values_are_refused_with_a_reason(self):
        for bad, message in [("not json", "not JSON"), (42, "GeoJSON"), ([1, 2], "GeoJSON"),
                             ({"rings": [[[0, 0], [1, 1]]]}, "Esri JSON"),
                             ({"x": 1, "y": 2}, "Esri JSON"),
                             ({"type": "Feature", "geometry": None}, "no geometry"),
                             ({"type": "Blob"}, "expected a GeoJSON")]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, message):
                    stac._geometry_of(bad)

    def test_projected_coordinates_are_caught_before_the_server_sees_them(self):
        meters = {"type": "Polygon", "coordinates": [[[500000, 5200000], [500100, 5200000],
                                                       [500100, 5200100], [500000, 5200000]]]}
        with patched_stac(post_json_body=lambda *a, **k: self.fail("requested")):
            with self.assertRaisesRegex(ValueError, "looks projected.*out_sr=4326"):
                stac.search(ROOT, "c", intersects=meters)

    def test_a_geometry_with_no_coordinates_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no coordinates"):
            stac._check_geometry({"type": "Polygon", "coordinates": []})

    def test_a_non_numeric_coordinate_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not a number"):
            stac._check_geometry({"type": "Point", "coordinates": ["a", "b"]})

    def test_a_perimeter_with_too_many_vertices_says_how_to_simplify(self):
        n = stac._MAX_VERTICES + 1
        ring = [[-114 + i * 1e-6, 46 + (i % 7) * 1e-6] for i in range(n)]
        with patched_stac(post_json_body=lambda *a, **k: self.fail("requested")):
            with self.assertRaisesRegex(ValueError, "max_allowable_offset"):
                stac.search(ROOT, "c", intersects={"type": "Polygon", "coordinates": [ring]})

    def test_vertices_are_counted_across_every_part(self):
        parts = [[[ [-114 + i * 1e-4, 46.0] for i in range(6000)]] for _ in range(2)]
        with self.assertRaisesRegex(ValueError, "12,000 vertices"):
            stac._check_geometry({"type": "MultiPolygon", "coordinates": parts})

    def test_a_geometry_exactly_at_the_limit_is_accepted(self):
        ring = [[-114 + i * 1e-6, 46.0] for i in range(stac._MAX_VERTICES)]
        stac._check_geometry({"type": "Polygon", "coordinates": [ring]})

    def test_the_geometry_is_not_edited_for_the_caller(self):
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": square(-114, 46)},
            {"type": "Feature", "geometry": square(-113, 46)}]}
        before = json.dumps(fc, sort_keys=True)
        self.sent(fc)
        self.assertEqual(json.dumps(fc, sort_keys=True), before)


class TestFootprints(unittest.TestCase):
    def test_one_feature_per_scene_with_its_shape_and_facts(self):
        df = run(FakeStacSearch(scenes(3)), ROOT, "sentinel-2-l2a", bbox=BBOX)
        fc = stac.footprints(df)
        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertEqual(len(fc["features"]), 3)
        feature = fc["features"][0]
        self.assertEqual(feature["type"], "Feature")
        self.assertEqual(feature["id"], "s000")
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertEqual(feature["properties"], {
            "id": "s000", "collection": "sentinel-2-l2a", "datetime": "2024-07-01T18:00:00Z",
            "cloud_cover": 5.0, "platform": "sentinel-2a"})

    def test_an_unknown_cloud_cover_is_null_in_the_json(self):
        item = stac_item("a")
        del item["properties"]["eo:cloud_cover"]
        fc = stac.footprints(run(FakeStacSearch([item, stac_item("b")]), ROOT, "c"))
        self.assertIsNone(fc["features"][0]["properties"]["cloud_cover"])
        json.dumps(fc, allow_nan=False)  # strict JSON: a NaN here would break map libraries

    def test_scenes_with_no_shape_are_left_out(self):
        item = stac_item("a")
        item["geometry"] = None
        fc = stac.footprints(run(FakeStacSearch([item, stac_item("b")]), ROOT, "c"))
        self.assertEqual([f["id"] for f in fc["features"]], ["b"])

    def test_no_scenes_is_an_empty_collection(self):
        fc = stac.footprints(run(FakeStacSearch([]), ROOT, "c"))
        self.assertEqual(fc, {"type": "FeatureCollection", "features": []})

    def test_something_that_is_not_a_search_result_is_refused(self):
        for bad in (None, [], {"features": []}):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "stac.search"):
                    stac.footprints(bad)

    def test_a_perimeter_search_round_trips_through_footprints(self):
        """The footprints of a search can be searched with again (a Feature per scene)."""
        df = run(FakeStacSearch(scenes(2)), ROOT, "c", bbox=BBOX)
        fc = stac.footprints(df)
        again = run(FakeStacSearch(scenes(1)), ROOT, "c", intersects=fc)
        self.assertEqual(len(again), 1)


PC = "planetary-computer"
BLOB = "https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/11/T/QN/B04.tif"
TOKEN = "st=2026-11-03&se=2026-11-04&sig=abc%3D"


class TokenServer(FakeStacSearch):
    """The search fake plus Planetary Computer's token endpoint."""

    def __init__(self, items=(), token=TOKEN):
        super().__init__(items)
        self.token = token

    def fetch_json(self, url, params=None, timeout=None, **kwargs):
        if "/sas/v1/token/" in url:
            self.calls.append(("GET", url, None))
            return {"msft:expiry": "2026-11-04T19:34:30Z", "token": self.token} \
                if self.token else {"msg": "no such collection"}
        return super().fetch_json(url, params, timeout, **kwargs)


def pc_item(iid="a"):
    return stac_item(iid, assets={
        "B04": {"href": BLOB, "title": "Red", "type": "image/tiff", "roles": ["data"]},
        "tilejson": {"href": "https://planetarycomputer.microsoft.com/api/data/v1/item/tilejson.json",
                     "type": "application/json", "roles": ["tiles"]}})


class TestAssets(unittest.TestCase):
    def get(self, server, *args, **kwargs):
        with patched_stac(fetch_json=server.fetch_json):
            return stac.assets(*args, **kwargs)

    def test_one_row_per_file_with_its_facts(self):
        df = self.get(FakeStacSearch([stac_item("a")]), ROOT, "sentinel-2-l2a", "a")
        self.assertEqual(list(df.columns), ["catalog", "collection", "item", "key", "title",
                                            "type", "roles", "access", "href"])
        self.assertEqual(list(df["key"]), ["thumbnail", "B04"])
        red = df[df["key"] == "B04"].iloc[0]
        self.assertEqual((red["title"], red["type"], red["roles"], red["item"]),
                         ("Red", "image/tiff", "data", "a"))
        self.assertEqual(red["href"], "https://data.example.org/a/B04.tif")

    def test_the_request_is_the_items_url_with_ids_quoted(self):
        server = FakeStacSearch([stac_item("S2/odd id", collection="c")])
        self.get(server, ROOT, "c", "S2/odd id")
        self.assertEqual(server.calls[0][1], f"{ROOT}/collections/c/items/S2%2Fodd%20id")

    def test_how_files_are_read_comes_from_the_registry_per_collection(self):
        server = FakeStacSearch([stac_item("a", collection="naip")])
        self.assertEqual(set(self.get(server, "earth-search", "naip", "a")["access"]),
                         {"requester-pays"})

    def test_roles_that_are_several_are_joined(self):
        item = stac_item("a", assets={"x": {"href": "https://h/x", "roles": ["data", "visual"]}})
        self.assertEqual(self.get(FakeStacSearch([item]), ROOT, "c", "a").iloc[0]["roles"],
                         "data, visual")

    def test_an_asset_with_no_extras_is_a_row_of_nones(self):
        item = stac_item("a", assets={"x": {"href": "s3://bucket/x.tif"}, "y": "junk"})
        df = self.get(FakeStacSearch([item]), ROOT, "c", "a")
        self.assertEqual(df.iloc[0]["href"], "s3://bucket/x.tif")
        self.assertTrue(df["href"].isna().iloc[1])
        self.assertTrue(df["title"].isna().all())

    def test_an_item_with_no_assets_is_an_empty_table_with_columns(self):
        df = self.get(FakeStacSearch([stac_item("a", assets={})]), ROOT, "c", "a")
        self.assertEqual(len(df), 0)
        self.assertIn("href", df.columns)

    def test_a_missing_scene_is_the_servers_404(self):
        with self.assertRaises(CloudError) as caught:
            self.get(FakeStacSearch([]), ROOT, "c", "nope")
        self.assertEqual(caught.exception.status, 404)

    def test_blank_ids_are_refused_before_any_request(self):
        for args in (("", "a"), ("c", ""), ("c", "  ")):
            with self.subTest(args=args):
                with patched_stac(fetch_json=lambda *a, **k: self.fail("requested")):
                    with self.assertRaisesRegex(ValueError, "collection id and the item id"):
                        stac.assets(ROOT, *args)

    def test_a_reply_that_is_not_an_item_says_so(self):
        for reply in ({"hello": 1}, [], {"assets": []}):
            with self.subTest(reply=reply):
                with patched_stac(fetch_json=lambda *a, **k: reply):
                    with self.assertRaisesRegex(ValueError, "did not return a STAC item"):
                        stac.assets(ROOT, "c", "a")

    def test_links_are_unsigned_unless_asked(self):
        server = TokenServer([pc_item()])
        df = self.get(server, PC, "sentinel-2-l2a", "a")
        self.assertEqual(df[df["key"] == "B04"].iloc[0]["href"], BLOB)
        self.assertEqual(set(df["access"]), {"sign"})
        self.assertEqual(len(server.calls), 1, "no token was fetched")

    def test_sign_adds_the_token_to_storage_links_only(self):
        server = TokenServer([pc_item()])
        df = self.get(server, PC, "sentinel-2-l2a", "a", sign=True)
        self.assertEqual(df[df["key"] == "B04"].iloc[0]["href"], BLOB + "?" + TOKEN)
        self.assertEqual(df[df["key"] == "tilejson"].iloc[0]["href"],
                         "https://planetarycomputer.microsoft.com/api/data/v1/item/tilejson.json")
        tokens = [c for c in server.calls if "/sas/v1/token/" in c[1]]
        self.assertEqual(len(tokens), 1, "one token covers the whole scene")
        self.assertTrue(tokens[0][1].endswith("/token/sentinel-2-l2a"))

    def test_sign_is_a_no_op_where_links_need_no_token(self):
        server = FakeStacSearch([stac_item("a")])
        df = self.get(server, "earth-search", "sentinel-2-l2a", "a", sign=True)
        self.assertEqual(len(server.calls), 1)
        self.assertEqual(df[df["key"] == "B04"].iloc[0]["href"], "https://data.example.org/a/B04.tif")


class TestSign(unittest.TestCase):
    def sign(self, server, *args, **kwargs):
        with patched_stac(fetch_json=server.fetch_json):
            return stac.sign(*args, **kwargs)

    def test_a_string_in_a_string_out(self):
        self.assertEqual(self.sign(TokenServer(), BLOB, "sentinel-2-l2a"), BLOB + "?" + TOKEN)

    def test_a_list_in_a_list_out_with_one_token_request(self):
        server = TokenServer()
        other = BLOB.replace("B04", "B03")
        got = self.sign(server, [BLOB, other], "sentinel-2-l2a")
        self.assertEqual(got, [BLOB + "?" + TOKEN, other + "?" + TOKEN])
        self.assertEqual(len(server.calls), 1)

    def test_a_link_with_a_query_gets_the_token_appended_with_an_ampersand(self):
        self.assertEqual(self.sign(TokenServer(), BLOB + "?x=1", "c"), BLOB + "?x=1&" + TOKEN)

    def test_links_that_are_not_storage_or_already_signed_come_back_unchanged(self):
        server = TokenServer()
        for href in ("https://example.org/x.tif", "s3://bucket/x.tif",
                     BLOB + "?sv=1&sig=zzz"):
            with self.subTest(href=href):
                self.assertEqual(self.sign(server, href, "c"), href)
        self.assertEqual(server.calls, [], "no token is fetched when nothing needs one")

    def test_a_catalog_that_does_not_sign_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not need signed links"):
            self.sign(TokenServer(), BLOB, "c", catalog="earth-search")

    def test_a_collection_with_no_token_is_a_cloud_error(self):
        with self.assertRaisesRegex(CloudError, "did not issue a token"):
            self.sign(TokenServer(token=None), BLOB, "nope")

    def test_the_collection_id_is_quoted_in_the_token_url(self):
        server = TokenServer()
        self.sign(server, BLOB, "a/b")
        self.assertTrue(server.calls[0][1].endswith("/token/a%2Fb"))

    def test_the_token_never_appears_in_an_error_or_a_table_it_was_not_asked_for(self):
        server = TokenServer([pc_item()])
        with patched_stac(fetch_json=server.fetch_json):
            df = stac.assets(PC, "sentinel-2-l2a", "a")
        self.assertNotIn("sig=", " ".join(map(str, df["href"])))


if __name__ == "__main__":
    unittest.main()
