"""georest.usgs.nationalmap, offline: EPQS's text replies and TNM paging."""

import json
import unittest
from unittest import mock

from georest.usgs import nationalmap as nm


def _epqs(value, resolution=1, acquired="9/1/2023"):
    return json.dumps({"location": {"x": 0, "y": 0}, "value": str(value),
                       "resolution": resolution, "attributes": {"AcquisitionDate": acquired}})


class ElevationTest(unittest.TestCase):
    def serve(self, *replies):
        calls = []
        replies = list(replies)

        def fake(url, params=None, **kwargs):
            calls.append(dict(params or {}))
            return replies.pop(0)

        patcher = mock.patch.object(nm, "fetch_text", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_a_value_comes_back_as_a_number(self):
        calls = self.serve(_epqs("3149.6"))
        self.assertEqual(nm.elevation(-105.75, 40.62), 3149.6)
        self.assertEqual((calls[0]["x"], calls[0]["y"], calls[0]["units"]), (-105.75, 40.62, "Meters"))

    def test_feet(self):
        calls = self.serve(_epqs("10333.3"))
        nm.elevation(-105.75, 40.62, units="feet")
        self.assertEqual(calls[0]["units"], "Feet")

    def test_call_failed_means_no_data_not_an_error(self):
        self.serve("Call failed.  [Failed cloud operation: Open]")
        self.assertIsNone(nm.elevation(-140, 30))

    def test_another_text_reply_is_retried_before_it_is_reported(self):
        calls = self.serve("Invalid or missing input parameters.", _epqs("100"))
        self.assertEqual(nm.elevation(-105, 40), 100.0)
        self.assertEqual(len(calls), 2)

    def test_a_text_reply_that_persists_is_an_error(self):
        self.serve(*["Invalid or missing input parameters."] * 3)
        with self.assertRaisesRegex(RuntimeError, "three times running"):
            nm.elevation(-105, 40)

    def test_bad_coordinates_and_units_fail_before_any_request(self):
        calls = self.serve()
        for args, kwargs in (((40.62, -105.75 - 200), {}), ((500, 30), {}),
                             ((-105, 40), {"units": "furlongs"})):
            with self.subTest(args=args), self.assertRaises(ValueError):
                nm.elevation(*args, **kwargs)
        self.assertEqual(calls, [])

    def test_several_points_make_a_frame_with_gaps_left_empty(self):
        self.serve(_epqs("10"), "Call failed.", _epqs("30", resolution=10))
        df = nm.elevations([(-105, 40), (-140, 30), (-104, 41)])
        self.assertEqual(list(df.columns), ["lon", "lat", "elevation", "resolution_m", "acquired"])
        self.assertEqual(df["elevation"].iloc[0], 10.0)
        self.assertTrue(df["elevation"].isna().iloc[1])
        self.assertEqual(df["resolution_m"].iloc[2], 10)


def _item(i, size=2_000_000):
    return {"title": f"tile {i}", "format": "GeoTIFF", "sizeInBytes": size,
            "publicationDate": "2022-02-10", "downloadURL": f"https://x/{i}.tif",
            "metaUrl": f"https://x/{i}.xml", "sourceId": str(i),
            "boundingBox": {"minX": -106, "minY": 40, "maxX": -105.9, "maxY": 40.1}}


class ProductsTest(unittest.TestCase):
    def serve(self, handler):
        calls = []

        def fake(url, params=None, **kwargs):
            calls.append((url, dict(params or {})))
            return handler(url, dict(params or {}))

        patcher = mock.patch.object(nm, "fetch_json", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_every_page_is_fetched(self):
        items = [_item(i) for i in range(7)]

        def handler(url, params):
            start = params["offset"]
            return {"total": 7, "errors": [], "items": items[start:start + 3]}

        with mock.patch.object(nm, "_PAGE_SIZE", 3):
            calls = self.serve(handler)
            df = nm.find_products((-106, 40, -105, 41), "Digital Elevation Model (DEM) 1 meter")
        self.assertEqual(list(df["title"]), [f"tile {i}" for i in range(7)])
        self.assertEqual([p["offset"] for _, p in calls], [0, 3, 6])
        self.assertEqual(calls[0][1]["bbox"], "-106.0,40.0,-105.0,41.0")
        self.assertEqual(df["size_mb"].iloc[0], 2.0)
        self.assertEqual(df["west"].iloc[0], -106)

    def test_formats_and_dates_are_sent(self):
        calls = self.serve(lambda url, p: {"total": 0, "errors": [], "items": []}
                           if url.endswith("/products") else [{"title": "E", "sbDatasetTag": "D"}])
        nm.find_products((-106, 40, -105, 41), "D", formats=["LAS", "LAZ"],
                         start="2020-01-01", end="2023-12-31")
        sent = calls[0][1]
        self.assertEqual((sent["prodFormats"], sent["dateType"], sent["start"], sent["end"]),
                         ("LAS,LAZ", "Publication", "2020-01-01", "2023-12-31"))

    def test_server_errors_are_raised(self):
        self.serve(lambda url, p: {"total": 0, "errors": ["bad bbox"], "items": []})
        with self.assertRaisesRegex(RuntimeError, "bad bbox"):
            nm.find_products((-106, 40, -105, 41), "D")

    def test_an_unknown_dataset_name_says_where_the_names_are(self):
        self.serve(lambda url, p: {"total": 0, "errors": [], "items": []}
                   if url.endswith("/products") else [{"title": "Elev", "sbDatasetTag": "Real Name"}])
        with self.assertRaisesRegex(ValueError, "list_datasets"):
            nm.find_products((-106, 40, -105, 41), "Made Up")

    def test_a_backwards_bbox_fails_before_any_request(self):
        calls = self.serve(lambda url, p: {})
        with self.assertRaises(ValueError):
            nm.find_products((-105, 41, -106, 40), "D")
        self.assertEqual(calls, [])

    def test_datasets_flatten_each_groups_product_types(self):
        self.serve(lambda url, p: [
            {"title": "Elevation Products", "tags": [
                {"title": "1-meter DEM", "sbDatasetTag": "Digital Elevation Model (DEM) 1 meter",
                 "extents": ["10000 x 10000 meter"], "infoUrl": "https://i/1"}]},
            {"title": "US Topo", "sbDatasetTag": "US Topo", "infoUrl": "https://i/2"},
        ])
        df = nm.list_datasets()
        self.assertEqual(list(df["dataset"]), ["Digital Elevation Model (DEM) 1 meter", "US Topo"])
        self.assertEqual(list(df["category"]), ["Elevation Products", "US Topo"])


if __name__ == "__main__":
    unittest.main()
