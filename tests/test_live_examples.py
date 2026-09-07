"""Execute edw.py's own docstring examples. Enable with EDW_LIVE=1.

The examples are extracted from the live source rather than transcribed, so
these fail if the documentation drifts from what the code and server do. An
earlier version of this module shipped a quick-start that raised, an
`import edw` that could not work, and four examples naming a service that
does not exist.
"""
import ast
import textwrap
import unittest
from pathlib import Path

from geoREST.RESTesri import edw

from .support import requires_live, retry

SOURCE = Path(edw.__file__)
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))


def indented_block_after(docstring, marker):
    """Pull the indented code block that follows `marker` out of a docstring."""
    lines = docstring.splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line) + 1
    block = []
    for line in lines[start:]:
        if not line.strip():
            block.append("")
        elif line.startswith("    "):
            block.append(line)
        else:
            break
    return textwrap.dedent("\n".join(block))


def function_docstring(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_docstring(node)
    raise AssertionError(f"{name} not found in {SOURCE}")


@requires_live
class ModuleQuickStart(unittest.TestCase):
    def setUp(self):
        self.code = indented_block_after(ast.get_docstring(TREE), "Quick start::")

    def test_quick_start_runs_and_returns_the_documented_fire(self):
        namespace = {}
        retry(lambda: exec(compile(self.code, "<quick-start>", "exec"), namespace))
        features = namespace["geojson"]["features"]
        self.assertEqual(len(features), 1)
        props = features[0]["properties"]
        self.assertEqual(props["fire_name"], "CAMERON PEAK")
        self.assertEqual(props["year"], 2020)
        self.assertTrue(namespace["services"])
        self.assertTrue(namespace["info"]["layers"])


@requires_live
class FunctionExamples(unittest.TestCase):
    """Each `Example::` block must execute and return real rows."""

    NAMES = ("query_features_analytic", "top_n_per_group")

    def test_examples_execute(self):
        for name in self.NAMES:
            with self.subTest(name):
                code = indented_block_after(function_docstring(name), "Example::")
                namespace = {name: getattr(edw, name)}
                retry(lambda c=code, n=namespace:
                      exec(compile(c, f"<{name}>", "exec"), n))
                self.assertTrue(namespace["result"]["features"],
                                f"{name} example returned no features")


@requires_live
class ReferencedServicesExist(unittest.TestCase):
    def test_every_service_named_in_the_source_is_in_the_catalog(self):
        live = {s["name"] for s in retry(lambda: edw.search_edw_services())}
        referenced = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for token in node.value.replace('"', " ").replace(",", " ").split():
                    if token.startswith("EDW_") and token[-3:-2] == "_":
                        referenced.add(token)
        missing = sorted(name for name in referenced if name not in live)
        self.assertEqual(missing, [],
                         f"service names in edw.py that no longer exist: {missing}")


if __name__ == "__main__":
    unittest.main()
