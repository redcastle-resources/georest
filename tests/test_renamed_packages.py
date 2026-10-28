"""The 0.4.0 subpackage names keep working after the rename to esri and usgs.

``georest.restesri`` and ``georest.restusgs`` shipped in 0.4.0, and other
packages import them (geeViz's esriLib uses ``georest.restesri._http``). They
must resolve to the same module objects as the new names, so a patch applied
through either spelling is seen through both, and they must warn.

Each check runs in a fresh interpreter: once an old name has been imported in
a process, it stays in sys.modules and a second import does not warn again.
"""

import subprocess
import sys
import textwrap
import unittest


def _run(code):
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True)


class RenamedPackagesTest(unittest.TestCase):
    def test_new_names_import_without_warnings(self):
        r = _run("""
            import warnings
            warnings.simplefilter("error")
            import georest.esri.edw, georest.esri.portal, georest.usgs.waterdata, georest.usfs.fia
        """)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_old_names_are_the_same_modules(self):
        r = _run("""
            import warnings
            warnings.simplefilter("ignore")
            import georest.esri, georest.esri.edw, georest.esri._http, georest.usgs.waterdata
            import georest.restesri.edw as old_edw
            from georest.restesri import _http as old_http
            from georest.restesri.cli import check_themes
            from georest.restusgs.waterdata import get_daily_values
            import sys
            import georest
            # Compare sys.modules entries, not attributes: a second copy
            # loaded under the old name would also overwrite the package
            # attribute, so attribute identity would hold even then.
            mods = sys.modules
            for sub in ("edw", "_http", "cli.check_themes"):
                assert mods["georest.restesri." + sub] is mods["georest.esri." + sub], sub
            assert mods["georest.restusgs.waterdata"] is mods["georest.usgs.waterdata"]
            assert old_edw is mods["georest.esri.edw"]
            assert old_http is mods["georest.esri._http"]
            assert check_themes is mods["georest.esri.cli.check_themes"]
            assert get_daily_values is mods["georest.usgs.waterdata"].get_daily_values
            assert georest.restesri is georest.esri
            assert georest.restusgs is georest.usgs
        """)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_old_names_warn_and_say_what_to_use(self):
        for old, new in (("restesri", "esri"), ("restusgs", "usgs")):
            with self.subTest(old=old):
                r = _run(f"""
                    import warnings
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        import georest.{old}
                    messages = [str(w.message) for w in caught
                                if issubclass(w.category, DeprecationWarning)]
                    assert any("georest.{new}" in m for m in messages), messages
                """)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_old_names_are_not_advertised(self):
        import georest
        self.assertNotIn("restesri", georest.__all__)
        self.assertNotIn("restusgs", georest.__all__)


if __name__ == "__main__":
    unittest.main()
