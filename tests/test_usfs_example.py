"""The usfs example notebook teaches the API the way a reader should.

It is the only example for usfs and doubles as its documentation, so
there is no second example to notice when it drifts. Ported from geeViz's
fsInsights_examples.ipynb, whose own test caught three ways it had taught
the wrong thing.
"""

import json
import pathlib
import re
import unittest

NB = pathlib.Path(__file__).resolve().parents[1] / "examples" / "usfs_examples.ipynb"


def _cells(kind):
    nb = json.loads(NB.read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == kind]


class ExampleNotebookTest(unittest.TestCase):
    def test_calls_name_their_module_like_the_other_providers(self):
        """fia.estimate and lcms.lcms_summary carry their module, as
        edw.search_edw_services and waterdata.get_daily_values do. The flat
        fs.estimate spelling came from geeViz's fsInsights."""
        code = "\n".join(_cells("code"))
        self.assertIn("from georest.usfs import", code)
        self.assertRegex(code, r"\bfia\.estimate\(")
        self.assertNotRegex(code, r"\bfs\.\w+")

    def test_nothing_points_back_at_geeviz_fsinsights(self):
        everything = "\n".join(_cells("code") + _cells("markdown"))
        self.assertNotIn("fsInsights", everything)
        self.assertNotIn(".geeViz/", everything)

    def test_it_shows_a_combined_estimate_across_states(self):
        code = "\n".join(_cells("code"))
        self.assertRegex(code, r"fia\.estimate\(\[[^\]]+\]")

    def test_no_output_is_cut_short(self):
        code = "\n".join(_cells("code"))
        self.assertIsNone(re.search(r"str\(e\)\[:\d+\]", code))

    def test_earth_engine_is_only_imported_where_it_is_used(self):
        """ee and geeViz are optional, so they are imported inside the
        RUN_EE cells, never at the top of the notebook."""
        first = _cells("code")[0]
        self.assertNotIn("import ee", first)
        self.assertNotIn("geeViz", first)


if __name__ == "__main__":
    unittest.main()
