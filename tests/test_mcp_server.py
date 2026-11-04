"""georest.mcp.server, offline: the tool contract the module docstring promises."""

import asyncio
import json
import socket
import unittest
from unittest import mock

try:
    from georest.mcp import server
except ImportError:  # the optional "mcp" extra is not installed
    server = None


def call(name, **args):
    """Call a tool the way an MCP client does; return its JSON as a dict."""
    out = asyncio.run(server.mcp.call_tool(name, args))
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


@unittest.skipIf(server is None, 'needs the "mcp" extra')
class ToolCatalog(unittest.TestCase):
    EXPECTED = {
        "search_fia", "describe_fia_grouping", "fia_estimate", "lcms_summary",
        "water_sites", "water_series", "water_values", "river_sites",
        "elevation", "earthquakes", "sciencebase_search", "sciencebase_files", "water_quality",
        "edw_search", "edw_layers", "edw_query", "portal_search", "feature_service_query",
        "stac_collections", "stac_search", "stac_assets",
    }

    def setUp(self):
        self.tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}

    def test_the_tools_are_the_documented_ones(self):
        self.assertEqual(set(self.tools), self.EXPECTED)

    def test_every_tool_is_read_only_and_documented(self):
        for name, t in self.tools.items():
            with self.subTest(tool=name):
                self.assertTrue(t.annotations and t.annotations.readOnlyHint)
                self.assertTrue((t.description or "").strip())

    def test_no_secret_in_any_schema(self):
        """A parameter named api_key or token invites the model to ask users
        for keys and paste them into the conversation."""
        for name, t in self.tools.items():
            props = set((t.inputSchema or {}).get("properties", {}))
            with self.subTest(tool=name):
                self.assertFalse(props & {"api_key", "token", "password", "key"}, props)

    def test_the_fia_rules_reach_the_model(self):
        self.assertIn("Never report an estimate", server.INSTRUCTIONS)
        self.assertIn("se_pct", self.tools["fia_estimate"].description)

    def test_the_stac_rules_reach_the_model(self):
        for phrase in ("never pixels", "truncated", "access", "Never ask a user for a token"):
            self.assertIn(phrase, server.INSTRUCTIONS)

    def test_no_stac_tool_can_sign_a_link(self):
        for name in ("stac_collections", "stac_search", "stac_assets"):
            props = set(self.tools[name].inputSchema.get("properties", {}))
            self.assertFalse(props & {"sign", "signed"}, (name, props))


@unittest.skipIf(server is None, 'needs the "mcp" extra')
class Results(unittest.TestCase):
    def test_every_row_comes_back(self):
        import pandas as pd

        rows = pd.DataFrame({"row": [f"r{i}" for i in range(150)], "estimate": range(150),
                             "se_pct": [5.0] * 150, "plots": [40] * 150,
                             "unreliable": [i % 10 == 0 for i in range(150)]})
        with mock.patch("georest.usfs.fia.estimate", return_value=rows):
            got = call("fia_estimate", wc=[412023], snum=2)
        self.assertEqual(got["count"], 150)
        self.assertEqual(len(got["rows"]), 150, "rows were cut short")
        self.assertEqual((got["reliable_count"], got["unreliable_count"]), (135, 15))

    def test_missing_values_are_null_not_nan(self):
        import pandas as pd

        df = pd.DataFrame({"time": ["2024-01-01"], "value": [float("nan")]})
        df.attrs["truncated"] = False
        with mock.patch("georest.usgs.waterdata.get_daily_values", return_value=df):
            text = asyncio.run(server.mcp.call_tool("water_values", {"site": "09380000"}))
        blocks = text[0] if isinstance(text, tuple) else text
        self.assertNotIn("NaN", blocks[0].text, "NaN is not JSON")
        self.assertIsNone(json.loads(blocks[0].text)["rows"][0]["value"])

    def test_a_large_earthquake_search_is_refused_with_its_count(self):
        with mock.patch("georest.usgs.earthquakes.count", return_value=12345), \
             mock.patch("georest.usgs.earthquakes.search") as search:
            got = call("earthquakes", min_magnitude=1, max_events=2000)
        self.assertIn("error", got)
        self.assertEqual(got["count"], 12345)
        search.assert_not_called()

    def test_a_bad_argument_is_an_answer_not_a_traceback(self):
        got = call("water_values", site="09380000", kind="hourly")
        self.assertIn("kind must be", got["error"])
        got = call("water_sites")
        self.assertIn("bbox", got["error"])
        got = call("water_sites", bbox=[-105, 41, -106, 40])
        self.assertIn("west < east", got["error"])

    def test_an_upstream_failure_is_an_answer(self):
        with mock.patch("georest.usgs.waterdata.get_time_series_metadata",
                        side_effect=RuntimeError("Request failed (503)")):
            got = call("water_series", site="09380000")
        self.assertIn("503", got["error"])
        self.assertIn("hint", got)


def scenes_frame(n=2, truncated=False, matched=None):
    """What stac.search returns, geometry column included."""
    import pandas as pd

    df = pd.DataFrame({
        "catalog": ["earth-search"] * n, "collection": ["sentinel-2-l2a"] * n,
        "id": [f"S2_{i}" for i in range(n)], "datetime": ["2024-07-01T18:00:00Z"] * n,
        "cloud_cover": [5.0] * n, "platform": ["sentinel-2a"] * n, "assets": ["open"] * n,
        "thumbnail": [None] * n, "bbox": [[-114.2, 46.8, -113.8, 47.1]] * n,
        "geometry": [{"type": "Polygon", "coordinates": []}] * n})
    df.attrs.update({"truncated": truncated, "matched": matched, "catalog": "earth-search"})
    return df


PERIMETER = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"objectid": 1},
     "geometry": {"type": "Polygon", "coordinates": [[[-114, 47], [-113, 47], [-113, 46], [-114, 47]]]}}]}
BOX = [-114.2, 46.8, -113.8, 47.1]


@unittest.skipIf(server is None, 'needs the "mcp" extra')
class StacTools(unittest.TestCase):
    def test_rows_carry_no_geometry_and_the_flags_arrive(self):
        with mock.patch.object(server.stac, "search",
                               return_value=scenes_frame(3, truncated=True, matched=900)) as search:
            got = call("stac_search", catalog="earth-search", collections=["sentinel-2-l2a"],
                       bbox=BOX, datetime="2024-06-01/2024-09-30", max_cloud_cover=20)
        self.assertEqual(got["count"], 3)
        self.assertTrue(got["truncated"])
        self.assertEqual((got["matched"], got["catalog"]), (900, "earth-search"))
        for row in got["rows"]:
            self.assertNotIn("geometry", row)
            self.assertIn("bbox", row)
        kwargs = search.call_args.kwargs
        self.assertEqual((kwargs["bbox"], kwargs["max_items"], kwargs["max_cloud_cover"]),
                         (tuple(BOX), 50, 20))

    def test_a_perimeter_is_fetched_simplified_and_searched(self):
        with mock.patch.object(server.edw, "query_features", return_value=PERIMETER) as edw_q, \
             mock.patch.object(server.stac, "search", return_value=scenes_frame()) as search:
            got = call("stac_search", catalog="earth-search", collections=["sentinel-2-l2a"],
                       perimeter_service="EDW_MTBS_01", perimeter_layer=0,
                       perimeter_where="fire_name = 'CAMERON PEAK'")
        edw_q.assert_called_once()
        self.assertEqual(edw_q.call_args.kwargs["max_allowable_offset"], 0.001)
        self.assertEqual(edw_q.call_args.kwargs["where"], "fire_name = 'CAMERON PEAK'")
        self.assertIs(search.call_args.kwargs["intersects"], PERIMETER)
        self.assertNotIn("bbox", search.call_args.kwargs)
        self.assertEqual(got["perimeter_features"], 1)

    def test_max_items_is_capped_and_says_so(self):
        with mock.patch.object(server.stac, "search", return_value=scenes_frame()) as search:
            got = call("stac_search", catalog="earth-search", collections=["x"], bbox=BOX,
                       max_items=5000)
        self.assertEqual(search.call_args.kwargs["max_items"], 500)
        self.assertIn("500", got["note"])

    def test_an_empty_search_says_what_to_check(self):
        with mock.patch.object(server.stac, "search", return_value=scenes_frame(0)):
            got = call("stac_search", catalog="earth-search", collections=["sentinl-2"], bbox=BOX)
        self.assertEqual(got["count"], 0)
        self.assertIn("stac_collections", got["hint"])

    def test_bad_requests_are_answers_and_never_reach_the_network(self):
        with mock.patch.object(server.stac, "search") as search, \
             mock.patch.object(server.edw, "query_features") as edw_q:
            cases = {
                "no place": {},
                "both places": {'bbox': BOX, 'perimeter_service': "EDW_MTBS_01", 'perimeter_layer': 0},
                "half a perimeter": {'perimeter_service': "EDW_MTBS_01"},
                "backwards bbox": {'bbox': [-113, 46, -114, 47]},
                "no collections": {'bbox': BOX, 'collections': []},
            }
            for label, extra in cases.items():
                args = {"catalog": "earth-search", "collections": ["sentinel-2-l2a"], **extra}
                with self.subTest(label):
                    self.assertIn("error", call("stac_search", **args))
        search.assert_not_called()
        edw_q.assert_not_called()

    def test_an_empty_or_oversized_perimeter_is_refused(self):
        empty = {"type": "FeatureCollection", "features": []}
        many = {"type": "FeatureCollection", "features": PERIMETER["features"] * 20}
        for label, fc, words in (("empty", empty, "edw_query"), ("many", many, "narrower")):
            with self.subTest(label), \
                 mock.patch.object(server.edw, "query_features", return_value=fc), \
                 mock.patch.object(server.stac, "search") as search:
                got = call("stac_search", catalog="earth-search", collections=["x"],
                           perimeter_service="EDW_MTBS_01", perimeter_layer=0)
                self.assertIn(words, got["error"])
                search.assert_not_called()

    def test_an_upstream_failure_is_an_answer(self):
        from georest.cloud._http import CloudError

        with mock.patch.object(server.stac, "search",
                               side_effect=CloudError("HTTP 502", status=502)):
            got = call("stac_search", catalog="earth-search", collections=["x"], bbox=BOX)
        self.assertIn("502", got["error"])
        self.assertIn("hint", got)

    def test_a_blank_catalog_lists_the_catalogs(self):
        import pandas as pd

        listing = pd.DataFrame({"name": ["earth-search"], "title": ["Earth Search"],
                                "url": ["https://earth-search.aws.element84.com/v1"],
                                "assets": ["open"], "notes": ["n"]})
        with mock.patch.object(server.stac, "catalogs", return_value=listing), \
             mock.patch.object(server.stac, "collections") as collections:
            got = call("stac_collections")
        self.assertEqual(got["rows"][0]["name"], "earth-search")
        collections.assert_not_called()

    def test_collections_pass_truncated_through(self):
        import pandas as pd

        df = pd.DataFrame({"catalog": ["lpcloud"], "id": ["HLSS30_2.0"]})
        df.attrs.update({"truncated": True, "catalog": "nasa-lpcloud"})
        with mock.patch.object(server.stac, "collections", return_value=df) as listing:
            got = call("stac_collections", catalog="nasa-lpcloud", query="hls", max_items=20)
        self.assertTrue(got["truncated"])
        listing.assert_called_once_with("nasa-lpcloud", "hls", max_items=20)

    def test_assets_come_back_unsigned_with_access(self):
        import pandas as pd

        df = pd.DataFrame({"key": ["B04"], "access": ["sign"],
                           "href": ["https://x.blob.core.windows.net/b04.tif"]})
        with mock.patch.object(server.stac, "assets", return_value=df) as assets:
            got = call("stac_assets", catalog="planetary-computer", collection="sentinel-2-l2a",
                       item="S2_0")
        self.assertEqual(assets.call_args.kwargs, {"sign": False})
        self.assertEqual(got["rows"][0]["access"], "sign")


@unittest.skipIf(server is None, 'needs the "mcp" extra')
class NoImportOnTheCallPath(unittest.TestCase):
    """A first-time import inside a tool (pandas, above all) lost that call's
    reply: the client timed out and every later call worked (reproduced
    2026-10-28 with a one-tool FastMCP server). So nothing in the server
    module may import from inside a function."""

    def test_no_function_imports(self):
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(server))
        offenders = []
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        offenders.append(f"{fn.name} (line {node.lineno})")
        self.assertEqual(offenders, [], "imports on the call path")


@unittest.skipIf(server is None, 'needs the "mcp" extra')
class PublicUrls(unittest.TestCase):
    def resolve_to(self, ip):
        return mock.patch("socket.getaddrinfo",
                          return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])

    def test_public_https_is_accepted(self):
        with self.resolve_to("52.0.0.1"):
            self.assertEqual(server.public_https_url("https://example.com/arcgis/rest"),
                             "https://example.com/arcgis/rest")

    def test_internal_addresses_are_refused(self):
        for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.169.254", "::1", "0.0.0.0"):
            with self.subTest(ip=ip), self.resolve_to(ip), self.assertRaises(ValueError):
                server.public_https_url("https://looks-public.example.com/x")

    def test_plain_http_and_other_schemes_are_refused(self):
        for url in ("http://example.com/x", "file:///etc/passwd", "ftp://example.com/x", "example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                server.public_https_url(url)

    def test_the_url_tools_use_the_guard(self):
        with self.resolve_to("169.254.169.254"), mock.patch(
                "georest.esri.services.queryFeatureService") as query:
            got = call("feature_service_query", url="https://metadata.example/FeatureServer/0")
        self.assertIn("not a public address", got["error"])
        query.assert_not_called()

    def test_the_stac_tools_guard_a_catalog_url(self):
        calls = {
            "stac_collections": {},
            "stac_search": {'collections': ["x"], 'bbox': BOX},
            "stac_assets": {'collection': "x", 'item': "y"},
        }
        for name, args in calls.items():
            with self.subTest(tool=name), self.resolve_to("169.254.169.254"), \
                 mock.patch.object(server.stac, "collections") as c, \
                 mock.patch.object(server.stac, "search") as s, \
                 mock.patch.object(server.stac, "assets") as a:
                got = call(name, catalog="https://metadata.example/stac", **args)
                self.assertIn("not a public address", got["error"])
                for m in (c, s, a):
                    m.assert_not_called()

    def test_a_registry_name_is_not_resolved_over_dns(self):
        with mock.patch("socket.getaddrinfo") as dns, \
             mock.patch.object(server.stac, "search", return_value=scenes_frame()):
            call("stac_search", catalog="earth-search", collections=["x"], bbox=BOX)
        dns.assert_not_called()


if __name__ == "__main__":
    unittest.main()
