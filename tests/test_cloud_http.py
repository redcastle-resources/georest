"""`cloud/_http.py`: the retry policy, error-body surfacing, headers, POST bodies.

Everything here runs against a fake `urlopen` and a fake clock bound onto the
module, so no test sleeps or touches the network. The policy under test is
tuned for a tool call inside an agent: it must never block for long, and a
429 must come back as something a caller can relay.
"""
from __future__ import annotations

import contextlib
import email.message
import io
import json
import unittest
import urllib.error

from georest.cloud import _frames, _http


def _message(headers=None):
    msg = email.message.Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return msg


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(payload=None):
    return FakeResponse(json.dumps(payload if payload is not None else {"ok": True}).encode())


def http_error(code, body=b"", headers=None):
    return urllib.error.HTTPError("https://stac.example/search", code, "err",
                                  _message(headers), io.BytesIO(body))


class FakeUrlopen:
    """Replays canned outcomes in order; an exception entry is raised."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@contextlib.contextmanager
def patched(*outcomes):
    """Bind a fake urlopen and a recording fake sleep onto the module."""
    urlopen, sleeps = FakeUrlopen(*outcomes), []
    saved = _http._urlopen, _http._sleep
    _http._urlopen, _http._sleep = urlopen, sleeps.append
    try:
        yield urlopen, sleeps
    finally:
        _http._urlopen, _http._sleep = saved


class TestFetch(unittest.TestCase):
    def test_returns_parsed_json_and_sends_identifying_headers(self):
        with patched(ok({"id": "c"})) as (urlopen, _):
            self.assertEqual(_http.fetch_json("https://stac.example/collections"), {"id": "c"})
        req = urlopen.requests[0]
        self.assertTrue(req.get_header("User-agent").startswith("georest/"))
        self.assertIn("json", req.get_header("Accept"))

    def test_params_are_encoded_onto_the_url(self):
        with patched(ok()) as (urlopen, _):
            _http.fetch_json("https://stac.example/search", {"limit": 5, "q": "a b"})
        self.assertEqual(urlopen.requests[0].full_url,
                         "https://stac.example/search?limit=5&q=a+b")

    def test_extra_headers_are_merged_over_the_defaults(self):
        with patched(ok()) as (urlopen, _):
            _http.fetch_json("https://stac.example/", headers={"Authorization": "Bearer t"})
        self.assertEqual(urlopen.requests[0].get_header("Authorization"), "Bearer t")

    def test_a_non_json_body_is_a_value_error_that_shows_the_start_of_it(self):
        with patched(FakeResponse(b"<html>maintenance</html>")):
            with self.assertRaisesRegex(ValueError, "maintenance"):
                _http.fetch_json("https://stac.example/")


class TestPost(unittest.TestCase):
    def test_body_is_json_with_a_json_content_type(self):
        body = {"collections": ["sentinel-2-l2a"], "limit": 10}
        with patched(ok({"features": []})) as (urlopen, _):
            out = _http.post_json_body("https://stac.example/search", body)
        req = urlopen.requests[0]
        self.assertEqual(out, {"features": []})
        self.assertEqual(json.loads(req.data), body)
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_method(), "POST")


class TestRetries(unittest.TestCase):
    def test_a_gateway_error_is_retried_with_backoff(self):
        with patched(http_error(503), http_error(502), ok({"n": 1})) as (urlopen, sleeps):
            self.assertEqual(_http.fetch_json("https://stac.example/"), {"n": 1})
        self.assertEqual(len(urlopen.requests), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_gives_up_after_the_attempts_and_says_what_the_server_said(self):
        errs = [http_error(503, b'{"description": "down for upgrade"}') for _ in range(3)]
        with patched(*errs) as (urlopen, _):
            with self.assertRaises(_http.CloudError) as caught:
                _http.fetch_json("https://stac.example/")
        self.assertEqual(len(urlopen.requests), 3)
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("down for upgrade", str(caught.exception))

    def test_a_server_error_500_is_not_retried(self):
        """From a STAC API a 500 means the query failed, not that it should be repeated."""
        with patched(http_error(500, b'{"description": "bad filter"}')) as (urlopen, sleeps):
            with self.assertRaisesRegex(_http.CloudError, "bad filter"):
                _http.fetch_json("https://stac.example/")
        self.assertEqual(len(urlopen.requests), 1)
        self.assertEqual(sleeps, [])

    def test_a_client_error_carries_its_status_and_the_start_of_the_body(self):
        with patched(http_error(400, b'{"description": "bbox is not valid"}')):
            with self.assertRaises(_http.CloudError) as caught:
                _http.fetch_json("https://stac.example/search")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("Request failed (400)", str(caught.exception))
        self.assertIn("bbox is not valid", str(caught.exception))

    def test_retries_one_disables_retrying(self):
        with patched(http_error(503)) as (urlopen, _):
            with self.assertRaises(_http.CloudError):
                _http.fetch_json("https://stac.example/", retries=1)
        self.assertEqual(len(urlopen.requests), 1)

    def test_a_dead_host_is_not_retried(self):
        with patched(urllib.error.URLError("name not known")) as (urlopen, sleeps):
            with self.assertRaisesRegex(_http.CloudError, "name not known"):
                _http.fetch_json("https://stac.example/")
        self.assertEqual(len(urlopen.requests), 1)
        self.assertEqual(sleeps, [])


class TestRateLimit(unittest.TestCase):
    def test_a_short_wait_is_honoured_then_retried(self):
        with patched(http_error(429, headers={"Retry-After": "2"}), ok({"n": 1})) as (_, sleeps):
            self.assertEqual(_http.fetch_json("https://stac.example/"), {"n": 1})
        self.assertEqual(sleeps, [2])

    def test_a_long_wait_is_raised_with_retry_after_for_the_caller(self):
        with patched(http_error(429, headers={"Retry-After": "120"})) as (urlopen, sleeps):
            with self.assertRaises(_http.CloudError) as caught:
                _http.fetch_json("https://stac.example/")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 120)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(urlopen.requests), 1)

    def test_a_429_with_no_retry_after_is_raised_at_once(self):
        with patched(http_error(429)):
            with self.assertRaises(_http.CloudError) as caught:
                _http.fetch_json("https://stac.example/")
        self.assertIsNone(caught.exception.retry_after)
        self.assertIn("no Retry-After", str(caught.exception))

    def test_cloud_error_is_a_runtime_error(self):
        self.assertTrue(issubclass(_http.CloudError, RuntimeError))


class TestFrames(unittest.TestCase):
    def test_an_empty_result_keeps_its_columns(self):
        df = _frames.frame([], ["id", "datetime"])
        self.assertEqual(list(df.columns), ["id", "datetime"])
        self.assertEqual(len(df), 0)

    def test_records_become_rows_in_the_given_column_order(self):
        df = _frames.frame([{"datetime": "2024-01-01", "id": "a"}], ["id", "datetime"])
        self.assertEqual(list(df.columns), ["id", "datetime"])
        self.assertEqual(df.loc[0, "id"], "a")

    def test_check_bbox_accepts_a_valid_box(self):
        self.assertEqual(_frames.check_bbox([-111.7, 36.8, -111.5, 37.0]),
                         (-111.7, 36.8, -111.5, 37.0))

    def test_check_bbox_rejects_a_reversed_box_with_the_rule(self):
        with self.assertRaisesRegex(ValueError, "west < east and south < north"):
            _frames.check_bbox([-111.5, 36.8, -111.7, 37.0])

    def test_check_bbox_rejects_the_wrong_shape(self):
        with self.assertRaisesRegex(ValueError, "four numbers"):
            _frames.check_bbox([1, 2, 3])


if __name__ == "__main__":
    unittest.main()
