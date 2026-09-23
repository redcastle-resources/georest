"""Guards on the package structure introduced by the georest restructure.

These are cheap, offline, and protect invariants that are easy to break
silently: the top-level re-export identity, and the stdlib-only guarantee.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

import georest
from georest.restesri import edw, portal, services


class TestReExports(unittest.TestCase):
    def test_reexports_are_the_same_objects(self):
        """georest.edw must BE georest.restesri.edw, not a copy.

        tests/support.py patches the network layer with setattr() on the
        module object. If the two import paths yielded distinct modules, a
        patch applied via one would silently fail to apply to the other.
        """
        self.assertIs(georest.edw, edw)
        self.assertIs(georest.portal, portal)
        self.assertIs(georest.services, services)

    def test_from_import_goes_through_pep562(self):
        from georest import edw as lazy_edw
        self.assertIs(lazy_edw, edw)

    def test_subpackage_is_reachable_both_ways(self):
        """`from georest import restesri` and `georest.restesri` agree, and
        both are the module `edw` actually lives in."""
        from georest import restesri as imported
        self.assertIs(imported, georest.restesri)
        self.assertIs(imported.edw, edw)

    def test_dunder_all_is_resolvable(self):
        for name in georest.__all__:
            if name != "__version__":
                with self.subTest(name=name):
                    self.assertTrue(hasattr(georest, name))

    def test_unknown_attribute_still_raises(self):
        with self.assertRaises(AttributeError):
            georest.no_such_module  # noqa: B018 - the attribute access IS the test

    def test_http_is_not_reexported_at_top_level(self):
        """_http is private; it should only be reachable via the full path."""
        with self.assertRaises(AttributeError):
            georest._http  # noqa: B018 - the attribute access IS the test

    def test_private_modules_are_not_lazily_exported(self):
        for name in ("_http", "_ogc", "_core"):
            with self.subTest(name=name):
                self.assertNotIn(name, georest._LAZY)
                self.assertNotIn(name, georest.__all__)

    def test_restusgs_reexports_are_the_same_objects(self):
        """Same identity rule as restesri: tests patch `_ogc` by setattr, and
        `georest.waterdata` must be the module that actually calls it."""
        from georest.restusgs import waterdata
        self.assertIs(georest.waterdata, waterdata)
        from georest import restusgs
        self.assertIs(restusgs, georest.restusgs)
        self.assertIs(restusgs.waterdata, waterdata)

    def test_version_is_pep440ish(self):
        self.assertRegex(georest.__version__, r"^\d+\.\d+\.\d+")


class TestUserAgent(unittest.TestCase):
    def test_user_agent_tracks_the_package_version(self):
        """It read "hostedServiceTools/1.0" for a while — a dead project name
        sent on every outbound request. Keep it pinned to the real version."""
        from georest.restesri import _http
        from georest.restusgs import _http as usgs_http
        self.assertIn(f"georest/{georest.__version__}", _http._USER_AGENT)
        self.assertEqual(usgs_http._USER_AGENT, _http._USER_AGENT)


class TestAttribution(unittest.TestCase):
    """Every shipped module carries the same copyright and license header."""

    EXPECTED = "Copyright 2026 Ryan Rock and Ian Housman"

    def test_every_module_has_a_consistent_header(self):
        root = pathlib.Path(georest.__file__).parent
        for path in sorted(root.rglob("*.py")):
            with self.subTest(module=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(self.EXPECTED, text)
                self.assertIn("Apache License, Version 2.0", text)


class TestCliHelp(unittest.TestCase):
    def test_help_text_excludes_the_license_header(self):
        """The license lives in the module docstring, which argparse would
        otherwise dump into --help. Regressed once; guarded now."""
        from georest.restesri.cli import check_themes
        self.assertNotIn("Apache License", check_themes._HELP)
        self.assertNotIn("Copyright", check_themes._HELP)
        self.assertIn("_SERVICE_THEMES", check_themes._HELP)

    def test_help_text_keeps_the_usage_examples_indented(self):
        """The default argparse formatter reflows these into one paragraph."""
        from georest.restesri.cli import check_themes
        self.assertIn("    georest-check-themes --emit", check_themes._HELP)


class TestNoThirdPartyImports(unittest.TestCase):
    """Turns the stdlib-only design goal into an enforced invariant."""

    ALLOWED = {
        "__future__", "argparse", "ast", "collections", "contextlib", "datetime",
        "html", "importlib",
        "json", "os", "pathlib", "re", "sys", "threading", "time", "typing",
        "unittest",
        "urllib", "xml", "georest",
    }

    def test_package_imports_only_stdlib(self):
        root = pathlib.Path(georest.__file__).parent
        checked = 0
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            checked += 1
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        with self.subTest(file=path.name, module=top):
                            self.assertIn(top, self.ALLOWED)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    top = (node.module or "").split(".")[0]
                    if top:
                        with self.subTest(file=path.name, module=top):
                            self.assertIn(top, self.ALLOWED)
        self.assertGreaterEqual(checked, 6, "expected to scan the whole package")

    def test_declared_runtime_dependencies_are_empty(self):
        import importlib.metadata as md
        try:
            reqs = md.requires("georest") or []
        except md.PackageNotFoundError:
            self.skipTest("georest is not installed as a distribution")
        runtime = [r for r in reqs if "extra ==" not in r]
        self.assertEqual(runtime, [], f"unexpected runtime dependencies: {runtime}")


class TestThemeHelpersMoved(unittest.TestCase):
    """VALID_THEMES/theme_drift moved from tests.support into edw so the
    shipped CLI could stop importing the test suite. Both paths must work."""

    def test_available_on_edw(self):
        self.assertTrue(edw.VALID_THEMES)
        self.assertTrue(callable(edw.theme_drift))

    def test_support_reexport_is_the_same_object(self):
        from .support import VALID_THEMES, theme_drift
        self.assertIs(VALID_THEMES, edw.VALID_THEMES)
        self.assertIs(theme_drift, edw.theme_drift)

    def test_every_themed_entry_uses_a_valid_theme(self):
        bad = {name: meta.get("theme")
               for name, meta in edw._SERVICE_THEMES.items()
               if meta.get("theme") not in edw.VALID_THEMES}
        self.assertEqual(bad, {})


class TestVersionSingleSource(unittest.TestCase):
    def test_pyproject_reads_version_from_the_package(self):
        """hatchling must point at the one place __version__ is defined."""
        root = pathlib.Path(__file__).resolve().parent.parent
        pyproject = root / "pyproject.toml"
        if not pyproject.exists():
            self.skipTest("running from an installed copy, not the repo")
        text = pyproject.read_text(encoding="utf-8")
        self.assertRegex(text, r'path\s*=\s*"src/georest/__init__\.py"')
        # The wheel cannot be built without this; see the plan's step 3.
        self.assertRegex(text, r'packages\s*=\s*\["src/georest"\]')
        self.assertNotRegex(text, r'^\s*version\s*=\s*"', )

    def test_no_duplicate_version_in_subpackage(self):
        from georest import restesri, restusgs
        for package in (restesri, restusgs):
            with self.subTest(package=package.__name__):
                self.assertFalse(hasattr(package, "__version__"))


if __name__ == "__main__":
    unittest.main()
