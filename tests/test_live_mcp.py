"""The georest MCP server, live: a real MCP client, a real subprocess, real services.

Starts ``python -m georest.mcp`` over stdio the way Claude Code or the
geeViz agent would, and calls every tool once with a small request.
Behind EDW_LIVE=1 (the switch that enables every live test).
"""

import asyncio
import json
import os
import sys
import unittest

from .support import LIVE

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    ClientSession = None

# One small, known-good call per tool, and what its answer must contain.
CALLS = [
    ("search_fia", {"query": "forest land area", "kind": "attributes", "limit": 5}, "rows"),
    ("search_fia", {"kind": "evaluations", "state": "Oregon", "limit": 3}, "rows"),
    ("describe_fia_grouping", {"label": "Ownership group - Major"}, "description"),
    ("lcms_summary", {"product": "Land_Cover", "state": "Colorado", "county": "Larimer",
                      "year": 2020}, "rows"),
    ("water_sites", {"bbox": [-111.7, 36.8, -111.5, 37.0], "site_type_code": "ST"}, "rows"),
    ("water_series", {"site": "USGS-09380000"}, "rows"),
    ("water_values", {"site": "USGS-09380000", "start": "2024-01-01", "end": "2024-01-10"}, "rows"),
    ("water_values", {"site": "USGS-09380000", "kind": "peaks", "start": "2015-01-01"}, "rows"),
    ("river_sites", {"lon": -111.596, "lat": 36.866, "mode": "upstream_tributaries",
                     "distance_km": 60}, "rows"),
    ("elevation", {"points": [[-105.6836, 40.2549], [-111.596, 36.866]]}, "rows"),
    ("earthquakes", {"start": "2024-01-01", "end": "2024-12-31", "min_magnitude": 7}, "rows"),
    ("sciencebase_search", {"query": "burn severity", "limit": 3}, "rows"),
    ("sciencebase_files", {"item_id": "6331e928d34e900e86c52b9e"}, "rows"),
    ("water_quality", {"kind": "results", "sites": ["USGS-09380000"],
                       "characteristic": ["Temperature, water"],
                       "start": "2025-01-01", "end": "2025-03-31"}, "rows"),
    ("edw_search", {"query": "fire"}, "rows"),
    ("edw_layers", {"service_name": "EDW_MTBS_01"}, "rows"),
    ("edw_query", {"service_name": "EDW_MTBS_01", "layer_id": 63,
                   "where": "fire_name = 'CAMERON PEAK'", "out_fields": "fire_name,year,acres"}, "rows"),
    ("edw_query", {"service_name": "EDW_MTBS_01", "layer_id": 63, "where": "year = 2020",
                   "count_only": True}, "count"),
    ("portal_search", {"query": "naip 2023", "limit": 3}, "rows"),
    ("feature_service_query", {"url": "https://sampleserver6.arcgisonline.com/arcgis/rest/services/"
                                      "Earthquakes_Since1970/FeatureServer/0",
                               "where": "magnitude >= 8.5", "out_fields": "name,magnitude"}, "rows"),
]


async def _run_all():
    params = StdioServerParameters(command=sys.executable, args=["-m", "georest.mcp"],
                                   env=dict(os.environ))
    results = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            results["_server"] = init.serverInfo.name
            results["_instructions"] = init.instructions or ""
            results["_tools"] = sorted(t.name for t in tools.tools)
            for i, (name, args, _) in enumerate(CALLS):
                out = await session.call_tool(name, args)
                results[i] = json.loads(out.content[0].text)
    return results


@unittest.skipUnless(LIVE, "set EDW_LIVE=1 to run against live services")
@unittest.skipIf(ClientSession is None, 'needs the "mcp" extra')
class LiveMcpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = asyncio.run(_run_all())

    def test_the_server_introduces_itself(self):
        self.assertEqual(self.r["_server"], "georest")
        self.assertIn("se_pct", self.r["_instructions"])
        self.assertEqual(len(self.r["_tools"]), 18)

    def test_every_tool_answers_with_data(self):
        for i, (name, args, key) in enumerate(CALLS):
            got = self.r[i]
            with self.subTest(tool=name, args=args):
                self.assertNotIn("error", got, got.get("error"))
                self.assertIn(key, got)
                if key == "rows":
                    self.assertGreater(len(got["rows"]), 0, "no rows")
                    self.assertEqual(got["count"], len(got["rows"]))

    def test_the_fia_and_usgs_answers_carry_what_matters(self):
        attrs = self.r[0]["rows"]
        self.assertIn("snum", attrs[0])
        flow = self.r[6]["rows"]
        self.assertIsInstance(flow[0]["value"], float)
        sites = self.r[4]["rows"]
        self.assertTrue(all(s["lon"] is not None for s in sites))


if __name__ == "__main__":
    unittest.main()
