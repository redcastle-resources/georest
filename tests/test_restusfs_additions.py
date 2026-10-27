"""What georest.restusfs does beyond the fsInsights code it was moved from.

* The transport is stdlib ``urllib`` (fsInsights used ``requests``), so its
  retry policy and both Forest Service error dialects are pinned here.
* ``estimate`` takes several evaluations in one request: the only way to
  get a correct combined standard error (DE 4.51% and MD 2.37% came back
  as 2.15% for the two together, matching the independent-strata formula,
  checked against the live API 2026-10-27).
* Warnings FIADB-API attaches to an estimate travel on the frame.
* Search returns every match, best first, instead of the first 25.

All offline: the network is replaced at ``_http._urlopen``.
"""

import io
import json
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from georest import restusfs as fs
from georest.restusfs import _http, fia, vocab

DE, MD = 102025, 242025


class _Resp:
    """Just enough of an http.client.HTTPResponse for get_json."""

    def __init__(self, body, status=200, content_type="application/json"):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body=""):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.replies = []
        self._patches = [
            mock.patch.object(_http, "_urlopen", self._fake_urlopen),
            mock.patch.object(_http, "_sleep", lambda s: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _fake_urlopen(self, req, timeout=None):
        self.requests.append(req)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def test_get_sends_params_in_the_url(self):
        self.replies = [_Resp('{"ok": 1}')]
        self.assertEqual(_http.get_json("https://h/p", {"a": "x y"}), {"ok": 1})
        self.assertEqual(self.requests[0].get_method(), "GET")
        self.assertIn("a=x+y", self.requests[0].full_url)

    def test_post_sends_params_in_the_body(self):
        self.replies = [_Resp('{"ok": 1}')]
        _http.get_json("https://h/p", {"strFilter": "plot.unitcd in (0, 1)"}, method="POST")
        req = self.requests[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "https://h/p")
        self.assertEqual(urllib.parse.parse_qs(req.data.decode())["strFilter"],
                         ["plot.unitcd in (0, 1)"])

    def test_a_5xx_is_retried(self):
        self.replies = [_http_error(503), _Resp('{"ok": 1}')]
        self.assertEqual(_http.get_json("https://h/p"), {"ok": 1})
        self.assertEqual(len(self.requests), 2)

    def test_a_4xx_is_not_retried_and_keeps_the_whole_body(self):
        body = "bad parameter " + "x" * 3000
        self.replies = [_http_error(400, body)]
        with self.assertRaises(_http.UpstreamError) as cm:
            _http.get_json("https://h/p")
        self.assertEqual(len(self.requests), 1)
        self.assertIn(body, str(cm.exception))

    def test_unreachable_raises_upstream_unavailable_after_all_attempts(self):
        self.replies = [urllib.error.URLError("dns")] * 3
        with self.assertRaises(_http.UpstreamUnavailable):
            _http.get_json("https://h/p", retries=2)
        self.assertEqual(len(self.requests), 3)

    def test_an_evalidator_error_page_under_200_is_retried_then_raised(self):
        page = ("<html><title>EVALIDator | Error Page</title>Error Type: Internal "
                "Server Error Received an Error: list index out of range</html>")
        self.replies = [_Resp(page, content_type="text/html")] * 3
        with self.assertRaises(_http.UpstreamError) as cm:
            _http.get_json("https://h/p", retries=2)
        self.assertEqual(len(self.requests), 3)
        self.assertIn("list index out of range", str(cm.exception))

    def test_an_lcms_parameter_error_under_200_raises(self):
        body = {"Result": {"ParameterError": "Invalid Summary Area",
                           "ProvidedParameters": {"county": "Nowhere"}}}
        self.replies = [_Resp(json.dumps(body))]
        with self.assertRaises(_http.UpstreamError) as cm:
            _http.get_json("https://h/p")
        self.assertEqual(cm.exception.provided, {"county": "Nowhere"})


def _njson(evalgrps, warnings=None, grm=None):
    meta = {"FIAorRPA": "FIADEF", "evalGrps": evalgrps}
    if warnings is not None:
        meta["warnings"] = warnings
    if grm is not None:
        meta["grmRatioWarning"] = grm
    return {"estimates": [{"GRP1": "`0010 Delaware", "ESTIMATE": 344017.7,
                           "SE_PERCENT": 4.51, "PLOT_COUNT": 114}],
            "totals": [{"ESTIMATE": 2734971.2, "SE_PERCENT": 2.15, "PLOT_COUNT": 527}],
            "metadata": meta}


class EstimateTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake_get_json(url, params=None, **kw):
            self.calls.append((url, dict(params or {}), kw))
            return self.reply

        self.reply = _njson([DE])
        patcher = mock.patch.object(fia, "get_json", fake_get_json)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_several_evaluations_go_in_one_request(self):
        self.reply = _njson([DE, MD])
        df = fs.estimate([DE, MD], 2, rselected="State code")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["wc"], f"{DE},{MD}")
        total = df[df["row"] == "Total"].iloc[0]
        self.assertAlmostEqual(total["se_pct"], 2.15)
        self.assertEqual(total["plots"], 527)

    def test_one_evaluation_is_still_a_plain_code(self):
        fs.estimate(DE, 2)
        self.assertEqual(self.calls[0][1]["wc"], str(DE))

    def test_every_evaluation_is_validated(self):
        with self.assertRaises(fs.FIAValidationError) as cm:
            fs.estimate([DE, 999999], 2)
        self.assertIn("999999", str(cm.exception))
        self.assertEqual(self.calls, [], "an invalid code must fail before the request")

    def test_api_warnings_travel_on_the_frame(self):
        self.reply = _njson([DE], warnings=["Ratio estimate across evaluations"],
                            grm="GRM ratio caution")
        with self.assertLogs("georest.restusfs.fia", level="WARNING"):
            df = fs.estimate(DE, 2)
        self.assertEqual(df.attrs["warnings"],
                         ["Ratio estimate across evaluations", "GRM ratio caution"])

    def test_no_warnings_is_an_empty_list(self):
        df = fs.estimate(DE, 2)
        self.assertEqual(df.attrs["warnings"], [])

    def test_a_long_filter_goes_by_post(self):
        fs.estimate(DE, 2, str_filter="plot.unitcd in (" + ", ".join(["1"] * 600) + ")")
        self.assertEqual(self.calls[0][2].get("method"), "POST")

    def test_a_short_filter_stays_get(self):
        fs.estimate(DE, 2, str_filter="plot.unitcd in (0, 1, 2)")
        self.assertEqual(self.calls[0][2].get("method"), "GET")


class SearchTest(unittest.TestCase):
    def test_search_returns_every_match(self):
        everything = fs.find_attributes("")
        self.assertEqual(len(everything), len(vocab.load_catalog("snum")))
        self.assertGreater(len(fs.find_attributes("carbon")), 25)

    def test_limit_is_still_available(self):
        self.assertEqual(len(fs.find_attributes("carbon", limit=3)), 3)

    def test_lookups_follow_a_refreshed_catalog(self):
        """The by-number index is rebuilt when the catalog is replaced."""
        self.assertIsNotNone(fs.get_evaluation(DE))
        saved = vocab._MEM_CACHE.get("wc")
        try:
            vocab._remember("wc", [{"EVAL_GRP": 1, "STATE": "Testland"}])
            self.assertIsNone(fs.get_evaluation(DE))
            self.assertEqual(fs.get_evaluation(1)["STATE"], "Testland")
        finally:
            vocab._remember("wc", saved)


if __name__ == "__main__":
    unittest.main()
