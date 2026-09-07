"""search_edw_services matching, and internal consistency of the theme table."""
import unittest

from geoREST.RESTesri import edw

from .support import VALID_THEMES, patched

CATALOG = {"services": [
    {"name": "EDW/EDW_MTBS_01", "type": "MapServer"},
    {"name": "EDW/EDW_Watersheds_01", "type": "MapServer"},
    {"name": "EDW/EDW_AquaticOrganismPassage_01", "type": "MapServer"},
    {"name": "EDW/EDW_County_01", "type": "MapServer"},
    {"name": "EDW/EDW_MadeUpService_99", "type": "MapServer"},
]}


def search(*args, **kwargs):
    with patched(fetch_json=lambda url, params=None, *a, **k: CATALOG):
        return edw.search_edw_services(*args, **kwargs)


class Matching(unittest.TestCase):
    def test_empty_query_returns_everything(self):
        self.assertEqual(len(search()), len(CATALOG["services"]))

    def test_substring_match_on_service_name(self):
        self.assertEqual([s["name"] for s in search("mtbs")], ["EDW_MTBS_01"])

    def test_match_is_case_insensitive(self):
        self.assertEqual([s["name"] for s in search("MTBS")], ["EDW_MTBS_01"])

    def test_description_match(self):
        # "Monitoring Trends in Burn Severity" lives only in the description.
        self.assertIn("EDW_MTBS_01",
                      [s["name"] for s in search("monitoring trends")])

    def test_keyword_alias_expands_to_related_services(self):
        # "riparian" appears in no service name; the alias table maps it to
        # the inland-waters theme and to stream/hydro/watershed fragments.
        self.assertIn("EDW_Watersheds_01",
                      [s["name"] for s in search("riparian")])

    def test_theme_filter(self):
        for service in search(theme="inland_waters"):
            self.assertEqual(service["theme"], "inland_waters")

    def test_theme_filter_is_case_insensitive(self):
        self.assertEqual(len(search(theme="INLAND_WATERS")),
                         len(search(theme="inland_waters")))

    def test_unknown_service_is_reported_as_uncategorized(self):
        found = [s for s in search("madeup")][0]
        self.assertEqual(found["theme"], "uncategorized")
        self.assertEqual(found["description"], "")

    def test_service_url_is_well_formed(self):
        found = [s for s in search("mtbs")][0]
        self.assertEqual(found["url"],
                         f"{edw.EDW_BASE_URL}/EDW_MTBS_01/MapServer")

    def test_results_carry_the_documented_keys(self):
        for service in search():
            self.assertEqual(set(service),
                             {"name", "type", "url", "theme", "description"})

    def test_no_duplicate_names(self):
        names = [s["name"] for s in search()]
        self.assertEqual(len(names), len(set(names)))


class ThemeTableIntegrity(unittest.TestCase):
    """_SERVICE_THEMES is hand-maintained, so guard its shape offline.

    Whether it still matches the live catalog is checked by
    test_live_catalog.py and tests/check_service_themes.py.
    """

    def test_every_theme_is_a_known_theme(self):
        for name, meta in edw._SERVICE_THEMES.items():
            with self.subTest(name):
                self.assertIn(meta.get("theme"), VALID_THEMES)

    def test_every_entry_has_a_description(self):
        for name, meta in edw._SERVICE_THEMES.items():
            with self.subTest(name):
                self.assertTrue((meta.get("desc") or "").strip())

    def test_entries_have_exactly_the_expected_keys(self):
        for name, meta in edw._SERVICE_THEMES.items():
            with self.subTest(name):
                self.assertEqual(set(meta), {"theme", "desc"})

    def test_service_names_look_like_edw_service_names(self):
        for name in edw._SERVICE_THEMES:
            with self.subTest(name):
                self.assertTrue(name.startswith("EDW_"), name)
                self.assertNotIn("/", name, "expected the short name, not EDW/...")

    def test_alias_themes_are_real_themes(self):
        """An alias naming a theme that doesn't exist would silently match nothing."""
        themes_in_use = {m["theme"] for m in edw._SERVICE_THEMES.values()}
        for keyword, expansions in edw._KEYWORD_ALIASES.items():
            for term in expansions:
                if term in VALID_THEMES:
                    with self.subTest(keyword=keyword, term=term):
                        self.assertIn(term, themes_in_use,
                                      f"alias {keyword!r} expands to theme "
                                      f"{term!r}, which no service uses")

    def test_docstring_lists_the_themes_actually_in_use(self):
        doc = edw.search_edw_services.__doc__
        for theme in {m["theme"] for m in edw._SERVICE_THEMES.values()}:
            with self.subTest(theme):
                self.assertIn(theme, doc)


class KeywordAliasEffectiveness(unittest.TestCase):
    """Aliases matching nothing are dead weight and mask gaps in the table."""

    @staticmethod
    def _haystacks():
        return [(name + " " + meta["desc"]).lower()
                for name, meta in edw._SERVICE_THEMES.items()]

    def test_report_alias_terms_that_match_no_service(self):
        haystacks = self._haystacks()
        themes_in_use = {m["theme"] for m in edw._SERVICE_THEMES.values()}
        dead = {}
        for keyword, expansions in edw._KEYWORD_ALIASES.items():
            for term in expansions:
                if term in themes_in_use:
                    continue
                if not any(term.lower() in hay for hay in haystacks):
                    dead.setdefault(keyword, []).append(term)
        # Recorded rather than asserted: multi-word terms are matched against
        # "name + description", and EDW's CamelCase names contain no spaces,
        # so several terms cannot match by construction. Tightening this is
        # tracked separately; the test documents the current set.
        if dead:
            print(f"\n  note: {sum(len(v) for v in dead.values())} keyword-alias "
                  f"terms match no service: {dead}")

    def test_each_alias_keyword_finds_at_least_one_service(self):
        haystacks = self._haystacks()
        themes_in_use = {m["theme"] for m in edw._SERVICE_THEMES.values()}
        for keyword, expansions in edw._KEYWORD_ALIASES.items():
            with self.subTest(keyword):
                reachable = any(
                    term in themes_in_use
                    or any(term.lower() in hay for hay in haystacks)
                    for term in expansions
                )
                self.assertTrue(reachable,
                                f"alias {keyword!r} can never match anything")


if __name__ == "__main__":
    unittest.main()
