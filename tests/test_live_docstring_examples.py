"""Run every ``Example::`` block in georest's docstrings against the live services.

An example in a docstring is documentation people copy. These run each one,
extracted from the source rather than transcribed, so an example that drifts
from what the code or the server does fails here instead of in a reader's
notebook. ``test_live_examples.py`` checks what two of edw's examples return;
this one checks that every example in every module runs.

Each module's examples run only when its server's switch is on (see
``support.py``): ``SERVICES_LIVE``, ``EDW_LIVE``, ``USGS_LIVE``, ``FIA_LIVE``,
``LCMS_LIVE``, or ``EDW_LIVE`` for all of them. Examples that sign in through
a browser never run unattended, and Earth Engine ones need ``EE_LIVE``.
"""

import ast
import importlib
import pathlib
import textwrap
import unittest

from .support import LIVE, LIVE_EE, LIVE_FIA, LIVE_LCMS, LIVE_SERVICES, LIVE_USGS, retry

#: module -> whether its examples may run now.
MODULES = {
    "georest.esri.services": LIVE_SERVICES,
    "georest.esri.portal": LIVE_SERVICES,
    "georest.esri.edw": LIVE,
    "georest.usgs.waterdata": LIVE_USGS,
    "georest.usgs.nldi": LIVE_USGS,
    "georest.usgs.nationalmap": LIVE_USGS,
    "georest.usgs.earthquakes": LIVE_USGS,
    "georest.usfs.vocab": LIVE_FIA,
    "georest.usfs.fia": LIVE_FIA,
    "georest.usfs.lcms": LIVE_LCMS,
    "georest.usfs.align": LIVE_FIA and LIVE_LCMS,
    "georest.usfs.lcms_ee": LIVE_EE,
    "georest.esri.auth": False,   # signs in through a browser
}

#: An example containing any of these needs a person or credentials.
NEEDS_A_PERSON = ("auth.login(", "GIS(\"Pro\")", "georest-login")


def examples(module_name):
    """(function name, code) for every ``Example::`` block in a module."""
    source = pathlib.Path(importlib.import_module(module_name).__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.Module)):
            continue
        doc = ast.get_docstring(node)
        if not doc:
            continue
        name = getattr(node, "name", "<module>")
        lines = doc.splitlines()
        for i, line in enumerate(lines):
            if not line.strip().endswith("Example::"):
                continue
            block = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.startswith("    "):
                    break
                block.append(nxt)
            found.append((name, textwrap.dedent("\n".join(block)).strip()))
    return found


class EveryExampleRuns(unittest.TestCase):
    def test_every_example_runs(self):
        ran = 0
        for module_name, enabled in MODULES.items():
            for name, code in examples(module_name):
                with self.subTest(module=module_name, function=name):
                    if not enabled:
                        self.skipTest(f"{module_name}: its live switch is off")
                    if any(marker in code for marker in NEEDS_A_PERSON):
                        self.skipTest(f"{name}: signs in interactively")
                    retry(lambda: exec(compile(code, f"<{module_name}.{name}>", "exec"), {}))
                    ran += 1
        if not ran:
            self.skipTest("no live switches set (EDW_LIVE=1 runs them all)")


class ExamplesAreWellFormed(unittest.TestCase):
    """Offline: every example at least compiles, so a typo cannot ship."""

    def test_every_example_compiles(self):
        for module_name in MODULES:
            for name, code in examples(module_name):
                with self.subTest(module=module_name, function=name):
                    compile(code, f"<{module_name}.{name}>", "exec")


if __name__ == "__main__":
    unittest.main()
