"""`restusgs/_http.py`: the retry policy, error-body surfacing, headers.

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

from georest.restusgs import _http


def _message(headers=None):
    msg = email.message.Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return msg


class FakeResponse:
    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = _message(headers)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(payload=None, headers=None):
    body = json.dumps(payload if payload is not None else {"ok": True}).encode()
    return FakeResponse(body, headers)


def http_error(code, body=b"", headers=None):
    return urllib.error.HTTPError("https://api.example/x", code, "err", _message(headers),
                                  io.BytesIO(body))


class FakeNet:
    """Plays a script of responses/exceptions; records requests and sleeps."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.sleeps = []

    def urlopen(self, req, timeout=None):
        self.requests.append(req)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def sleep(self, seconds):
        self.sleeps.append(seconds)


@contextlib.contextmanager
def net(*script):
    fake = FakeNet(*script)
    saved = (_http._urlopen, _http._sleep)
    _http._urlopen, _http._sleep = fake.urlopen, fake.sleep
    try:
        yield fake
    finally:
        _http._urlopen, _http._sleep = saved


URL = "https://api.example/x"


class RateLimitTests(unittest.TestCase):
    def test_short_retry_after_is_honoured_then_succeeds(self):
        with net(http_error(429, headers={"Retry-After": "3"}), ok({"v": 1})) as fake:
            self.assertEqual(_http.fetch_json(URL), {"v": 1})
        self.assertEqual(fake.sleeps, [3])
        self.assertEqual(len(fake.requests), 2)

    def test_long_retry_after_raises_immediately(self):
        with net(http_error(429, headers={"Retry-After": "42"})) as fake:
            with self.assertRaises(_http.RateLimitError) as ctx:
                _http.fetch_json(URL)
        self.assertEqual(ctx.exception.retry_after, 42)
        self.assertEqual(fake.sleeps, [], "must not block inside a tool call")
        self.assertEqual(len(fake.requests), 1)
        self.assertIn("429", str(ctx.exception))
        self.assertIn("42", str(ctx.exception))
        self.assertIn("USGS_API_KEY", str(ctx.exception))

    def test_missing_retry_after_raises_with_none(self):
        with net(http_error(429)) as fake:
            with self.assertRaises(_http.RateLimitError) as ctx:
                _http.fetch_json(URL)
        self.assertIsNone(ctx.exception.retry_after)
        self.assertEqual(fake.sleeps, [])

    def test_rate_limit_error_is_a_runtimeerror(self):
        """Callers catching the transport contract's RuntimeError still see it."""
        self.assertTrue(issubclass(_http.RateLimitError, RuntimeError))

    def test_http_date_retry_after_is_treated_as_unknown(self):
        with net(http_error(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})):
            with self.assertRaises(_http.RateLimitError) as ctx:
                _http.fetch_json(URL)
        self.assertIsNone(ctx.exception.retry_after)

    def test_last_attempt_429_raises_even_when_short(self):
        with net(http_error(429, headers={"Retry-After": "1"}),
                 http_error(429, headers={"Retry-After": "1"}),
                 http_error(429, headers={"Retry-After": "1"})) as fake:
            with self.assertRaises(_http.RateLimitError):
                _http.fetch_json(URL)
        self.assertEqual(fake.sleeps, [1, 1])


class BackoffTests(unittest.TestCase):
    def test_503_backs_off_one_then_two_seconds(self):
        with net(http_error(503), http_error(503), ok()) as fake:
            _http.fetch_json(URL)
        self.assertEqual(fake.sleeps, [1.0, 2.0])
        self.assertEqual(len(fake.requests), 3)

    def test_exhausted_retries_raise_the_last_code(self):
        with net(http_error(503), http_error(502), http_error(504)) as fake:
            with self.assertRaises(RuntimeError) as ctx:
                _http.fetch_json(URL)
        self.assertIn("Request failed (504)", str(ctx.exception))
        self.assertEqual(fake.sleeps, [1.0, 2.0])

    def test_retries_one_never_sleeps(self):
        with net(http_error(503)) as fake:
            with self.assertRaises(RuntimeError):
                _http.fetch_json(URL, retries=1)
        self.assertEqual(fake.sleeps, [])
        self.assertEqual(len(fake.requests), 1)

    def test_400_raises_at_once_with_the_body(self):
        body = json.dumps({"code": "InvalidParameterValue",
                           "description": "datetime is not a valid interval"}).encode()
        with net(http_error(400, body)) as fake:
            with self.assertRaises(RuntimeError) as ctx:
                _http.fetch_json(URL)
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith(f"Request failed (400): {URL}"), msg)
        self.assertIn("datetime is not a valid interval", msg)
        self.assertEqual(fake.sleeps, [])
        self.assertEqual(len(fake.requests), 1)

    def test_500_is_not_retried(self):
        """From these APIs a 500 means the query failed, not 'try again'."""
        with net(http_error(500, b"boom")) as fake:
            with self.assertRaises(RuntimeError):
                _http.fetch_json(URL)
        self.assertEqual(len(fake.requests), 1)

    def test_error_body_is_capped(self):
        with net(http_error(400, b"x" * 2000)):
            with self.assertRaises(RuntimeError) as ctx:
                _http.fetch_json(URL)
        self.assertLess(len(str(ctx.exception)), 600)

    def test_unreadable_error_body_is_not_a_second_error(self):
        err = http_error(400)
        err.read = lambda: (_ for _ in ()).throw(OSError("closed"))
        with net(err):
            with self.assertRaises(RuntimeError) as ctx:
                _http.fetch_json(URL)
        self.assertEqual(str(ctx.exception), f"Request failed (400): {URL}")

    def test_urlerror_is_not_retried(self):
        with net(urllib.error.URLError("name resolution failed")) as fake:
            with self.assertRaises(RuntimeError) as ctx:
                _http.fetch_json(URL)
        self.assertIn("Request error", str(ctx.exception))
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(fake.sleeps, [])


class HeaderTests(unittest.TestCase):
    def test_user_agent_is_always_sent(self):
        with net(ok()) as fake:
            _http.fetch_json(URL)
        self.assertEqual(fake.requests[0].get_header("User-agent"), _http._USER_AGENT)

    def test_extra_headers_merge_over_the_defaults(self):
        with net(ok()) as fake:
            _http.fetch_json(URL, headers={"X-Api-Key": "k", "User-Agent": "custom/1"})
        req = fake.requests[0]
        self.assertEqual(req.get_header("X-api-key"), "k")
        self.assertEqual(req.get_header("User-agent"), "custom/1")

    def test_headers_are_resent_on_retry(self):
        with net(http_error(503), ok()) as fake:
            _http.fetch_json(URL, headers={"X-Api-Key": "k"})
        self.assertEqual([r.get_header("X-api-key") for r in fake.requests], ["k", "k"])

    def test_api_key_never_appears_in_a_raised_message(self):
        secret = "sekret-key-value"
        with net(http_error(400, b'{"description": "bad"}')):
            with self.assertRaises(RuntimeError) as ctx:
                _http.fetch_json(URL, {"q": "1"}, headers={"X-Api-Key": secret})
        self.assertNotIn(secret, str(ctx.exception))
        with net(http_error(429, headers={"Retry-After": "99"})):
            with self.assertRaises(_http.RateLimitError) as ctx:
                _http.fetch_json(URL, headers={"X-Api-Key": secret})
        self.assertNotIn(secret, str(ctx.exception))

    def test_query_params_are_encoded_onto_the_url(self):
        with net(ok()) as fake:
            _http.fetch_json(URL, {"f": "json", "limit": "2"})
        self.assertEqual(fake.requests[0].full_url, URL + "?f=json&limit=2")

    def test_fetch_json_with_headers_returns_the_response_headers(self):
        with net(ok({"a": 1}, headers={"X-RateLimit-Limit": "1000",
                                       "X-RateLimit-Remaining": "998"})):
            data, headers = _http.fetch_json_with_headers(URL)
        self.assertEqual(data, {"a": 1})
        self.assertEqual(headers["X-RateLimit-Remaining"], "998")

    def test_timeout_default_and_override(self):
        calls = []

        def urlopen(req, timeout=None):
            calls.append(timeout)
            return ok()

        saved = _http._urlopen
        _http._urlopen = urlopen
        try:
            _http.fetch_json(URL)
            _http.fetch_json(URL, timeout=7)
        finally:
            _http._urlopen = saved
        self.assertEqual(calls, [_http._TIMEOUT, 7])


class BodyTests(unittest.TestCase):
    def test_non_json_body_is_a_valueerror(self):
        with net(FakeResponse(b"<html>degraded</html>")):
            with self.assertRaises(ValueError) as ctx:
                _http.fetch_json(URL)
        self.assertIn("Expected JSON", str(ctx.exception))
        self.assertIn("degraded", str(ctx.exception))

    def test_fetch_text_returns_the_body(self):
        with net(FakeResponse(b"a,b\n1,2\n")):
            self.assertEqual(_http.fetch_text(URL), "a,b\n1,2\n")

    def test_post_json_is_form_encoded(self):
        with net(ok()) as fake:
            _http.post_json(URL, {"a": "1", "b": "x y"})
        req = fake.requests[0]
        self.assertEqual(req.data, b"a=1&b=x+y")
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")

    def test_post_json_body_sends_json(self):
        body = {"op": "in", "args": [{"property": "parameter_code"}, ["00060"]]}
        with net(ok()) as fake:
            _http.post_json_body(URL, body)
        req = fake.requests[0]
        self.assertEqual(json.loads(req.data.decode()), body)
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_user_agent_tracks_the_package_version(self):
        import georest
        self.assertIn(f"georest/{georest.__version__}", _http._USER_AGENT)


if __name__ == "__main__":
    unittest.main()
