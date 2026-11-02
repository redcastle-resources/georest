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


if __name__ == "__main__":
    unittest.main()
