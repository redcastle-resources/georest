"""Tests for the searchPortal org-scoping fix.

Ported from the geeViz esriLib patch (redcastle-resources) to georest.restesri.portal — same style as the existing
suite: all HTTP mocked via unittest.mock, no network required.

Covers:
- _is_agol_org_portal: org URLs vs www.arcgis.com vs Enterprise
- _resolve_org_id: portals/self call, success-only caching, REST error bodies,
  malformed ids, recovery after a transient failure
- auto mode scopes an AGOL org URL
- auto mode does NOT scope www.arcgis.com / portal="agol"  (regression)
- auto mode does NOT scope when raw_q is supplied            (regression)
- org_scoped=True forces scoping even with raw_q
- org_scoped=False disables scoping entirely                 (regression)
- unresolvable org id FAILS CLOSED (never silently global)
- _resolve_org_id caches per (portal, token), not per portal (wrong org)
- **filters naming q / f / num raise TypeError, not ignored  (bypass)
- empty query + data_only is not an all-negative group        (empty results)
- built-in portal="nasa" auto-scopes
- unbalanced raw_q rejected when scoping                     (scope escape)
- unbalanced free-text query cleaned, not rejected           (regression)
"""

import threading
import time
import unittest
from unittest.mock import patch

from georest.restesri import portal as el

HOU = "https://mycity.maps.arcgis.com"
HOU_ORG = "NummVBqZSIJKUeVR"

_EMPTY_SEARCH = {"results": []}


def _clear_cache():
    el._ORG_ID_CACHE.clear()


class TestIsAgolOrgPortal(unittest.TestCase):
    def test_org_url_is_org(self):
        self.assertTrue(el._is_agol_org_portal(HOU))

    def test_www_arcgis_is_not_org(self):
        self.assertFalse(el._is_agol_org_portal("https://www.arcgis.com"))

    def test_enterprise_portal_is_not_agol_org(self):
        self.assertFalse(el._is_agol_org_portal("https://gis.myagency.gov/portal"))

    def test_case_insensitive(self):
        self.assertTrue(el._is_agol_org_portal("https://MyCity.Maps.ArcGIS.com"))


class TestResolveOrgId(unittest.TestCase):
    def setUp(self):
        _clear_cache()

    def test_reads_id_from_portals_self(self):
        with patch.object(el, "fetch_json", return_value={"id": HOU_ORG}) as m:
            self.assertEqual(el._resolve_org_id(HOU), HOU_ORG)
        self.assertIn("/sharing/rest/portals/self", m.call_args[0][0])

    def test_result_is_cached(self):
        with patch.object(el, "fetch_json", return_value={"id": HOU_ORG}) as m:
            el._resolve_org_id(HOU)
            el._resolve_org_id(HOU)
        self.assertEqual(m.call_count, 1)

    def test_failure_raises_and_is_not_cached(self):
        """A transient failure must NOT be cached — caching it would silently
        and permanently disable scoping, returning other orgs' data."""
        with patch.object(el, "fetch_json", side_effect=OSError("boom")) as m:
            with self.assertRaises(RuntimeError):
                el._resolve_org_id(HOU)
            with self.assertRaises(RuntimeError):
                el._resolve_org_id(HOU)
        self.assertEqual(m.call_count, 2)          # retried, not cached
        self.assertEqual(el._ORG_ID_CACHE, {})

    def test_recovers_after_transient_failure(self):
        calls = {"n": 0}
        def side(url, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient")
            return {"id": HOU_ORG}
        with patch.object(el, "fetch_json", side_effect=side):
            with self.assertRaises(RuntimeError):
                el._resolve_org_id(HOU)
            self.assertEqual(el._resolve_org_id(HOU), HOU_ORG)

    def test_rest_error_payload_raises(self):
        with patch.object(el, "fetch_json",
                          return_value={"error": {"message": "Invalid token"}}):
            with self.assertRaises(RuntimeError):
                el._resolve_org_id(HOU)

    def test_malformed_org_id_rejected(self):
        for bad in [None, "", "abc def", "x)y", 12345, "a" * 100]:
            with self.subTest(bad=bad):
                el._ORG_ID_CACHE.clear()
                with patch.object(el, "fetch_json", return_value={"id": bad}):
                    with self.assertRaises(RuntimeError):
                        el._resolve_org_id(HOU)

    def test_cache_is_per_token(self):
        """portals/self reports the token owner's org, so one base URL can map
        to several orgs. A URL-only cache key handed the first caller's org id
        to every later token - silently scoping them to the wrong org."""
        orgs = {"TOKALICE": "ORGalice", "TOKBOB": "ORGbob", None: "ORGpublic"}
        def side(url, params=None):
            return {"id": orgs[(params or {}).get("token")]}
        with patch.object(el, "fetch_json", side_effect=side) as m:
            self.assertEqual(el._resolve_org_id(HOU, token="TOKALICE"), "ORGalice")
            self.assertEqual(el._resolve_org_id(HOU, token="TOKBOB"), "ORGbob")
            self.assertEqual(el._resolve_org_id(HOU), "ORGpublic")
            self.assertEqual(el._resolve_org_id(HOU, token="TOKALICE"), "ORGalice")
        self.assertEqual(m.call_count, 3)          # repeat Alice still cached

    def test_empty_token_shares_anonymous_cache_entry(self):
        """'' sends no token, exactly like None, so they are the same lookup."""
        with patch.object(el, "fetch_json", return_value={"id": HOU_ORG}) as m:
            el._resolve_org_id(HOU, token=None)
            el._resolve_org_id(HOU, token="")
        self.assertEqual(m.call_count, 1)

    def test_token_forwarded(self):
        with patch.object(el, "fetch_json", return_value={"id": HOU_ORG}) as m:
            el._resolve_org_id(HOU, token="TOK")
        self.assertEqual(m.call_args[0][1].get("token"), "TOK")


class TestSearchPortalOrgScoping(unittest.TestCase):
    def setUp(self):
        _clear_cache()

    def _q(self, mock):
        """Extract the q param from the *search* call (last call)."""
        return mock.call_args[0][1]["q"]

    def _search_side_effect(self, search_result=None):
        """portals/self returns the org id; everything else returns a search body."""
        def side(url, params=None):
            if url.endswith("/portals/self"):
                return {"id": HOU_ORG}
            return search_result or _EMPTY_SEARCH
        return side

    def test_auto_scopes_agol_org(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("evacuation", portal=HOU)
        q = self._q(m)
        self.assertIn(f"orgid:{HOU_ORG}", q)
        self.assertIn("evacuation", q)

    def test_auto_does_not_scope_global_agol(self):
        with patch.object(el, "fetch_json", return_value=_EMPTY_SEARCH) as m:
            el.searchPortal("wildfire", portal="agol")
        self.assertNotIn("orgid:", self._q(m))

    def test_auto_does_not_scope_enterprise_portal(self):
        with patch.object(el, "fetch_json", return_value=_EMPTY_SEARCH) as m:
            el.searchPortal("hydrology", portal="https://gis.myagency.gov/portal")
        self.assertNotIn("orgid:", self._q(m))

    def test_auto_does_not_scope_when_raw_q_given(self):
        with patch.object(el, "fetch_json", return_value=_EMPTY_SEARCH) as m:
            el.searchPortal("", portal=HOU, raw_q='type:"Feature Service"')
        q = self._q(m)
        self.assertNotIn("orgid:", q)
        self.assertEqual(q, 'type:"Feature Service"')

    def test_explicit_true_scopes_even_with_raw_q(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("", portal=HOU, raw_q='type:"Feature Service"',
                            org_scoped=True)
        q = self._q(m)
        self.assertIn(f"orgid:{HOU_ORG}", q)
        self.assertIn('type:"Feature Service"', q)

    def test_explicit_false_disables_scoping(self):
        with patch.object(el, "fetch_json", return_value=_EMPTY_SEARCH) as m:
            el.searchPortal("evacuation", portal=HOU, org_scoped=False)
        self.assertNotIn("orgid:", self._q(m))

    def test_unresolvable_org_fails_closed(self):
        """Fail closed, not open. Silently returning global results would be
        the exact bug this patch exists to fix."""
        def side(url, params=None):
            if url.endswith("/portals/self"):
                raise OSError("portal down")
            return _EMPTY_SEARCH
        with patch.object(el, "fetch_json", side_effect=side):
            with self.assertRaises(RuntimeError):
                el.searchPortal("evacuation", portal=HOU)

    def test_reserved_filters_raise_before_any_request(self):
        """`**filters` used to merge last, so filters['q'] silently replaced
        the org-scoped query and searched globally. Silently ignoring it
        instead would run a search the caller didn't ask for, so it raises -
        before portals/self or the search goes out."""
        for kwargs in ({"q": "something else"}, {"f": "html"}, {"num": 9999},
                       {"q": "x", "num": 5}):
            for portal in (HOU, "agol"):
                with self.subTest(kwargs=kwargs, portal=portal):
                    with patch.object(el, "fetch_json",
                                      side_effect=self._search_side_effect()) as m:
                        with self.assertRaises(TypeError):
                            el.searchPortal("evacuation", portal=portal, **kwargs)
                    m.assert_not_called()

    def test_reserved_filter_error_names_the_params(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()):
            with self.assertRaisesRegex(TypeError, r"num, q"):
                el.searchPortal("evacuation", portal=HOU, q="x", num=5)

    def test_ordinary_filters_still_forwarded_when_scoping(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("evacuation", portal=HOU, sortField="title",
                            bbox="-120,35,-110,42")
        params = m.call_args[0][1]
        self.assertEqual(params["sortField"], "title")
        self.assertEqual(params["bbox"], "-120,35,-110,42")

    def _assert_scope_intact(self, q):
        """The wrapper must survive whole: prefix, final ')', and an inner
        query that cannot close it early."""
        prefix = f"orgid:{HOU_ORG} AND ("
        self.assertTrue(q.startswith(prefix), q)
        self.assertTrue(q.endswith(")"), q)
        self.assertNotIn("\\", q)
        el._assert_balanced_parens(q[len(prefix):-1])

    def test_unbalanced_raw_q_rejected_when_scoping(self):
        """A crafted ')' in caller-authored DSL could close the orgid wrapper
        and escape the scope."""
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()):
            with self.assertRaises(ValueError):
                el.searchPortal("", portal=HOU, org_scoped=True,
                                raw_q="evac) OR (orgid:OTHER")

    def test_unbalanced_free_text_is_neutralized_not_rejected(self):
        """The same crafted ')' in free text is cleaned, not raised - and still
        cannot escape the scope."""
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("evac) OR (orgid:OTHER", portal=HOU, data_only=False)
        self._assert_scope_intact(self._q(m))

    def test_ordinary_typed_punctuation_does_not_raise(self):
        """Regression: a stray paren or quote a user typed used to raise
        ValueError on org portals while the identical call worked on agol."""
        for typed in ["evacuation (2023", '12" pipeline', "flood)", 'say "hi']:
            for data_only in (True, False):
                with self.subTest(typed=typed, data_only=data_only):
                    with patch.object(el, "fetch_json",
                                      side_effect=self._search_side_effect()) as m:
                        el.searchPortal(typed, portal=HOU, data_only=data_only)
                    self._assert_scope_intact(self._q(m))

    def test_trailing_backslash_cannot_escape_wrapper(self):
        """'foo\\' passes the balance check but would escape the closing ')'."""
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("foo\\", portal=HOU, data_only=False)
        self.assertEqual(self._q(m), f"orgid:{HOU_ORG} AND (foo)")

    def test_well_formed_free_text_passes_through_unchanged(self):
        """Neutralizing only kicks in for text that could break the wrapper;
        phrases and grouping in a well-formed query keep their meaning."""
        typed = '"fire perimeter" (flood OR fire)'
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal(typed, portal=HOU, data_only=False)
        self.assertEqual(self._q(m), f"orgid:{HOU_ORG} AND ({typed})")

    def test_free_text_untouched_when_not_scoping(self):
        with patch.object(el, "fetch_json", return_value=_EMPTY_SEARCH) as m:
            el.searchPortal('12" pipeline', portal="agol", data_only=False)
        self.assertEqual(self._q(m), '12" pipeline')

    def test_balanced_parens_allowed(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("(flood OR fire)", portal=HOU, data_only=False)
        self.assertIn(f"orgid:{HOU_ORG}", self._q(m))

    def test_unbalanced_parens_fine_when_not_scoping(self):
        with patch.object(el, "fetch_json", return_value=_EMPTY_SEARCH):
            el.searchPortal("evac)", portal="agol")   # no wrapper, no guard

    def test_data_only_exclusions_survive_scoping(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("evacuation", portal=HOU, data_only=True)
        q = self._q(m)
        self.assertIn(f"orgid:{HOU_ORG}", q)
        self.assertIn('-type:"Dashboard"', q)

    def test_empty_query_scopes_cleanly(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("", portal=HOU, data_only=False)
        self.assertEqual(self._q(m), f"orgid:{HOU_ORG}")

    def test_empty_query_with_exclusions_is_not_an_all_negative_group(self):
        """`orgid:X AND (-type:"A" ...)` groups nothing but prohibitions, which
        Lucene matches against nothing - so "list the org's data" came back
        empty. The exclusions belong beside the orgid clause, ungrouped."""
        for typed in ("", "   ", '("'):          # '("' neutralizes to nothing
            with self.subTest(typed=typed):
                with patch.object(el, "fetch_json",
                                  side_effect=self._search_side_effect()) as m:
                    el.searchPortal(typed, portal=HOU, data_only=True)
                q = self._q(m)
                self.assertTrue(q.startswith(f'orgid:{HOU_ORG} -type:"'), q)
                self.assertNotIn("(", q)
                self.assertIn('-type:"Dashboard"', q)

    def test_query_with_terms_still_grouped(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("evacuation", portal=HOU, data_only=True)
        q = self._q(m)
        self.assertTrue(q.startswith(f"orgid:{HOU_ORG} AND (evacuation -type:"), q)
        self.assertTrue(q.endswith(")"), q)

    def test_empty_raw_q_scopes_cleanly(self):
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("ignored", portal=HOU, raw_q="", org_scoped=True)
        self.assertEqual(self._q(m), f"orgid:{HOU_ORG}")

    def test_builtin_nasa_portal_is_auto_scoped(self):
        """PORTALS["nasa"] is an AGOL org URL, so it scopes by default. Pinned
        so the docstring's claim can't drift from the behaviour."""
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("mars", portal="nasa")
        self.assertIn(f"orgid:{HOU_ORG}", self._q(m))
        with patch.object(el, "fetch_json", side_effect=self._search_side_effect()) as m:
            el.searchPortal("mars", portal="nasa", org_scoped=False)
        self.assertNotIn("orgid:", self._q(m))


class TestParenLexer(unittest.TestCase):
    """The wrapper is ``orgid:X AND (<q>)``. Anything that can close that paren
    early escapes the org scope, which is the whole bug."""

    def test_quoted_parens_do_not_count(self):
        # Literal parens inside quotes are not structural.
        el._assert_balanced_parens('title:"(" AND title:")"')

    def test_codex_bypass_string_is_rejected(self):
        """Regression for the exact bypass Codex found: a naive counter sees
        this as balanced, but structurally it closes the wrapper before
        ``OR orgid:OTHER``."""
        with self.assertRaises(ValueError):
            el._assert_balanced_parens('title:"(" x) OR orgid:OTHER OR (x ")"')

    def test_escaped_quote_does_not_open_string(self):
        el._assert_balanced_parens(r'title:"a\"b" AND (x)')

    def test_unterminated_quote_rejected(self):
        with self.assertRaises(ValueError):
            el._assert_balanced_parens('title:"oops AND (x)')

    def test_early_close_rejected(self):
        with self.assertRaises(ValueError):
            el._assert_balanced_parens('x) OR orgid:OTHER')

    def test_trailing_backslash_rejected(self):
        """Balanced, but the backslash would escape the wrapper's closing
        paren: ``orgid:X AND (foo\\)``."""
        for q in ["foo\\", "(flood OR fire) \\", "a\\\\\\"]:
            with self.subTest(q=q):
                with self.assertRaises(ValueError):
                    el._assert_balanced_parens(q)

    def test_escaped_backslash_at_end_ok(self):
        # An even run of backslashes is a literal backslash, not an escape.
        el._assert_balanced_parens("foo\\\\")

    def test_trailing_backslash_raw_q_rejected_end_to_end(self):
        with patch.object(el, "fetch_json",
                          side_effect=lambda u, p=None: {"id": HOU_ORG}
                          if u.endswith("/portals/self") else _EMPTY_SEARCH):
            with self.assertRaises(ValueError):
                el.searchPortal("", portal=HOU, org_scoped=True,
                                raw_q='type:"Feature Service" foo\\')

    def test_plain_balanced_ok(self):
        el._assert_balanced_parens("(flood OR fire) AND storm")

    def test_bypass_rejected_end_to_end(self):
        with patch.object(el, "fetch_json",
                          side_effect=lambda u, p=None: {"id": HOU_ORG}
                          if u.endswith("/portals/self") else _EMPTY_SEARCH):
            with self.assertRaises(ValueError):
                el.searchPortal("", portal=HOU, org_scoped=True,
                                raw_q='title:"(" x) OR orgid:OTHER OR (x ")"')

    def test_bypass_neutralized_in_free_text(self):
        """The same bypass string typed as free text is cleaned, and the
        orgid clause still binds every result."""
        with patch.object(el, "fetch_json",
                          side_effect=lambda u, p=None: {"id": HOU_ORG}
                          if u.endswith("/portals/self") else _EMPTY_SEARCH) as m:
            el.searchPortal('title:"(" x) OR orgid:OTHER OR (x ")"',
                            portal=HOU, data_only=False)
        q = m.call_args[0][1]["q"]
        prefix = f"orgid:{HOU_ORG} AND ("
        self.assertTrue(q.startswith(prefix) and q.endswith(")"), q)
        el._assert_balanced_parens(q[len(prefix):-1])


class TestResolveOrgIdRobustness(unittest.TestCase):
    def setUp(self):
        _clear_cache()

    def test_non_dict_json_raises_connection_error(self):
        for bad in ([], "a string", 42, None):
            with self.subTest(bad=bad):
                _clear_cache()
                with patch.object(el, "fetch_json", return_value=bad):
                    with self.assertRaises(RuntimeError):
                        el._resolve_org_id(HOU)

    def test_non_dict_error_value_raises_connection_error(self):
        with patch.object(el, "fetch_json", return_value={"error": "boom"}):
            with self.assertRaises(RuntimeError):
                el._resolve_org_id(HOU)

    def test_concurrent_resolution_is_single_flight(self):
        """8 threads race on a cold cache; exactly one portals/self request
        should go out and all 8 should get the id.

        The fetcher sleeps so the other threads are guaranteed to arrive while
        it is still in flight — without single-flight they would each issue
        their own request.
        """
        calls = []
        calls_lock = threading.Lock()

        def slow(url, params=None):
            with calls_lock:
                calls.append(url)
            time.sleep(0.25)          # hold the lock so the others pile up
            return {"id": HOU_ORG}

        results = []
        results_lock = threading.Lock()

        def worker():
            try:
                r = el._resolve_org_id(HOU)
            except Exception as exc:            # noqa: BLE001
                r = exc
            with results_lock:
                results.append(r)

        with patch.object(el, "fetch_json", side_effect=slow):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        self.assertEqual(len(results), 8)
        self.assertTrue(all(r == HOU_ORG for r in results), results)
        self.assertEqual(len(calls), 1, f"expected single-flight, got {len(calls)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
