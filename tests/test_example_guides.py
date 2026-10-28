"""The guide notebooks are published with their output, so it must be clean.

``examples/guide_*.ipynb`` are run before they are committed, and georest.io
shows their saved charts, maps and tables as they are. A guide that was
saved half-run, saved with a traceback, or saved with a warning printed
under a cell would publish exactly that. They also teach the API, so they
use it the way the rest of georest does: public modules only, called
through the module (``fia.estimate``, ``edw.query_features``).
"""

import json
import pathlib
import unittest

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"
GUIDES = sorted(EXAMPLES.glob("guide_*.ipynb"))


def _code_cells(path):
    nb = json.loads(path.read_text(encoding="utf-8"))
    return [c for c in nb["cells"] if c["cell_type"] == "code"]


class GuideNotebooksTest(unittest.TestCase):
    def test_there_are_guides(self):
        self.assertGreaterEqual(len(GUIDES), 4)

    def test_every_code_cell_was_run_and_saved_its_output(self):
        for path in GUIDES:
            for i, cell in enumerate(_code_cells(path)):
                with self.subTest(guide=path.name, cell=i):
                    self.assertIsNotNone(cell.get("execution_count"), "cell was never run")

    def test_no_errors_or_warnings_were_saved(self):
        for path in GUIDES:
            for i, cell in enumerate(_code_cells(path)):
                for out in cell.get("outputs", []):
                    with self.subTest(guide=path.name, cell=i):
                        self.assertNotEqual(out.get("output_type"), "error")
                        self.assertFalse(out.get("output_type") == "stream"
                                         and out.get("name") == "stderr",
                                         "".join(out.get("text", "")))

    def test_they_use_the_public_modules_the_documented_way(self):
        for path in GUIDES:
            code = "\n".join("".join(c["source"]) for c in _code_cells(path))
            with self.subTest(guide=path.name):
                self.assertRegex(code, r"from georest\.\w+ import ")
                self.assertNotRegex(code, r"\bfs\.\w+")
                self.assertNotRegex(code, r"import[^\n]*\b_\w+|\._\w+\b")


if __name__ == "__main__":
    unittest.main()
