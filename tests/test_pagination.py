"""Paging and chunking must never silently return a subset.

Real EDW layers all report maxRecordCount=2000, so a hardcoded 2000-record
stride is latent there. These tests model a server whose cap is lower, which
is the configuration that silently drops records.
"""
import unittest

from geoREST.RESTesri import edw

from .support import FakeServer, patched


def with_server(max_record_count=1000, total=5000, layer_info=None):
    """Patch edw to talk to a FakeServer; returns it for request inspection."""
    server = FakeServer(max_record_count, total)
    info = layer_info or (lambda s, l: {"maxRecordCount": server.max_record_count})
    return server, patched(post_json=server.post_json, get_layer_info=info)


class PageStride(unittest.TestCase):
    def test_pages_at_the_layers_real_limit(self):
        server, ctx = with_server(max_record_count=1000, total=5000)
        with ctx:
            got = edw.query_features_with_pagination("X", 0, max_features=5000)
        self.assertEqual(len(got["features"]), 5000)
        requested = [p["resultRecordCount"] for p in server.requests
                     if "resultOffset" in p]
        self.assertEqual(requested, ["1000"] * 5)

    def test_no_gaps_or_duplicates_across_pages(self):
        server, ctx = with_server(max_record_count=1000, total=5000)
        with ctx:
            got = edw.query_features_with_pagination("X", 0, max_features=5000)
        ids = [f["properties"]["objectid"] for f in got["features"]]
        self.assertEqual(ids, list(range(1, 5001)))

    def test_max_features_mid_page_is_respected(self):
        _, ctx = with_server(max_record_count=1000, total=5000)
        with ctx:
            got = edw.query_features_with_pagination("X", 0, max_features=2500)
        self.assertEqual(len(got["features"]), 2500)

    def test_stops_cleanly_when_data_runs_out(self):
        _, ctx = with_server(max_record_count=1000, total=37)
        with ctx:
            got = edw.query_features_with_pagination("X", 0, max_features=5000)
        self.assertEqual(len(got["features"]), 37)

    def test_survives_a_failed_max_record_count_lookup(self):
        """A metadata failure falls back to a guess; exceededTransferLimit covers it."""
        def boom(service, layer):
            raise RuntimeError("EDW layer error: Layer not found")

        _, ctx = with_server(max_record_count=1000, total=5000, layer_info=boom)
        with ctx:
            got = edw.query_features_with_pagination("X", 0, max_features=5000)
        self.assertEqual(len(got["features"]), 5000)


class ChunkedIdFetch(unittest.TestCase):
    """The object-ID fallback used when a complex AOI returns nothing."""

    def test_chunks_at_the_layers_real_limit(self):
        _, ctx = with_server(max_record_count=1000, total=4000)
        with ctx:
            got = edw._fetch_features_by_ids("X", 0, "objectid",
                                             list(range(1, 4001)), "*", 4326, 4000)
        self.assertEqual(len(got["features"]), 4000)

    def test_every_requested_id_comes_back(self):
        _, ctx = with_server(max_record_count=1000, total=4000)
        with ctx:
            got = edw._fetch_features_by_ids("X", 0, "objectid",
                                             list(range(1, 4001)), "*", 4326, 4000)
        ids = [f["properties"]["objectid"] for f in got["features"]]
        self.assertEqual(ids, list(range(1, 4001)))

    def test_an_over_wide_chunk_raises_rather_than_truncating(self):
        _, ctx = with_server(max_record_count=1000, total=3000)
        with ctx, self.assertRaises(RuntimeError) as caught:
            edw._fetch_features_by_ids("X", 0, "objectid", list(range(1, 3001)),
                                       "*", 4326, 3000, chunk_size=2000)
        message = str(caught.exception)
        self.assertIn("1000", message)
        self.assertIn("2000", message)

    def test_missing_features_key_raises_runtime_error_not_key_error(self):
        with patched(post_json=lambda url, params, *a, **k: {"type": "FeatureCollection"},
                     get_layer_info=lambda s, l: {"maxRecordCount": 1000}):
            with self.assertRaises(RuntimeError):
                edw._fetch_features_by_ids("X", 0, "objectid", [1, 2, 3],
                                           "*", 4326, 3)

    def test_max_features_truncates_the_id_list(self):
        _, ctx = with_server(max_record_count=1000, total=4000)
        with ctx:
            got = edw._fetch_features_by_ids("X", 0, "objectid",
                                             list(range(1, 4001)), "*", 4326, 1500)
        self.assertEqual(len(got["features"]), 1500)


class ResultRecordCount(unittest.TestCase):
    """The server clamps to its own maxRecordCount; a client-side clamp only under-fetched."""

    def test_request_is_not_pre_clamped(self):
        server, ctx = with_server(max_record_count=5000, total=5000)
        with ctx:
            edw.query_features("X", 0, max_features=5000)
        self.assertEqual(server.requests[-1]["resultRecordCount"], "5000")

    def test_a_layer_allowing_more_than_2000_delivers_more(self):
        _, ctx = with_server(max_record_count=5000, total=5000)
        with ctx:
            got = edw.query_features("X", 0, max_features=5000)
        self.assertEqual(len(got["features"]), 5000)


class MaxRecordCountCache(unittest.TestCase):
    def setUp(self):
        edw._MAX_RECORD_COUNT_CACHE.clear()

    def tearDown(self):
        edw._MAX_RECORD_COUNT_CACHE.clear()

    def test_value_is_read_from_the_layer_and_cached(self):
        calls = []

        def info(service, layer):
            calls.append((service, layer))
            return {"maxRecordCount": 1234}

        with patched(get_layer_info=info):
            self.assertEqual(edw._get_max_record_count("SVC", 3), 1234)
            self.assertEqual(edw._get_max_record_count("SVC", 3), 1234)
            edw._get_max_record_count("SVC", 4)
        self.assertEqual(len(calls), 2, "second lookup of the same layer should hit cache")

    def test_falls_back_when_the_value_is_unusable(self):
        for bogus in ({"maxRecordCount": 0}, {"maxRecordCount": None},
                      {"maxRecordCount": "nope"}, {}):
            with self.subTest(bogus=bogus):
                edw._MAX_RECORD_COUNT_CACHE.clear()
                with patched(get_layer_info=lambda s, l, _b=bogus: _b):
                    self.assertEqual(edw._get_max_record_count("S", 1),
                                     edw._MAX_RECORD_COUNT)


class InputSpatialReference(unittest.TestCase):
    """in_sr is independent of out_sr; requesting another output projection
    must not change how the input geometry is read."""

    def setUp(self):
        self.sent = {}
        # get_layer_info is patched too: the paginating path resolves the
        # layer's maxRecordCount and would otherwise reach the network.
        self._ctx = patched(
            post_json=self._capture,
            get_layer_info=lambda s, l: {"maxRecordCount": 1000},
        )
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def _capture(self, url, params, *args, **kwargs):
        self.sent.update(params)
        return {"type": "FeatureCollection", "features": []}

    def test_defaults_to_wgs84_regardless_of_out_sr(self):
        edw.query_features("X", 0, geometry="-106,40,-105,41", out_sr=3857)
        self.assertEqual(self.sent["inSR"], "4326")
        self.assertEqual(self.sent["outSR"], "3857")

    def test_explicit_in_sr_is_honoured(self):
        edw.query_features("X", 0, geometry="-106,40,-105,41",
                           out_sr=4326, in_sr=3857)
        self.assertEqual(self.sent["inSR"], "3857")

    def test_pagination_takes_in_sr_too(self):
        edw.query_features_with_pagination("X", 0, geometry="-106,40,-105,41",
                                           out_sr=3857)
        self.assertEqual(self.sent["inSR"], "4326")

    def test_analytic_takes_in_sr_too(self):
        edw.query_features_analytic(
            "X", 0, out_analytics=[{"type": "RANK", "out_name": "r"}],
            geometry="-106,40,-105,41", out_sr=3857)
        self.assertEqual(self.sent["inSR"], "4326")


if __name__ == "__main__":
    unittest.main()
