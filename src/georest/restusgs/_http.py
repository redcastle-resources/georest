"""
Private stdlib-only HTTP/JSON helpers used across the georest.restusgs modules.

No third-party dependencies — GET/POST + JSON parsing via urllib only.

Duplicated from ``georest.restesri._http`` on purpose: the two providers are
kept self-contained for now, and the two transports are candidates for a
future shared ``georest._core``. This copy adds what the USGS Water Data API
needs and the Esri one does not:

- ``headers=`` on every fetcher (the API key travels in ``X-Api-Key``).
- A bounded retry on 502/503/504, and on 429 only when the server's
  ``Retry-After`` is short. The policy is tuned for a tool call inside an
  agent, not a batch job: it never blocks for more than a few seconds.
- The first 400 characters of an HTTP error body in the raised message —
  the USGS API answers 400 with a JSON ``description`` that says what was
  actually wrong.
- ``fetch_json_with_headers`` so callers can read ``X-RateLimit-*``, and
  ``post_json_body`` for JSON-bodied POSTs.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__ as _VERSION

_TIMEOUT = 60  # default seconds for HTTP requests; override per-call via `timeout`

#: Sent on every outbound request. Derived from the package version so the
#: servers we query can attribute traffic to a specific release.
_USER_AGENT = f"georest/{_VERSION} (+https://github.com/redcastle-resources/georest)"

#: Total attempts per request (so 3 means two retries). Override per call via
#: `retries=`; `retries=1` disables retrying.
_RETRIES = 3
#: Transient gateway failures worth a short backoff. 500 is deliberately
#: absent: from these APIs it means "your query failed", not "try again".
_RETRY_STATUSES = frozenset({502, 503, 504})
_RETRY_BASE = 1.0  # seconds; attempt n sleeps _RETRY_BASE * 2**n (1 s, 2 s, ...)
#: A 429 is retried only if the server's `Retry-After` is at most this many
#: seconds. Anything longer is raised as RateLimitError immediately so an
#: agent can tell the user rather than sit inside a tool call.
_RATE_LIMIT_MAX_WAIT = 5
_ERROR_BODY_CHARS = 400

# Module-level bindings so tests can stand in for the network and the clock
# without touching urllib or sleeping for real.
_urlopen = urllib.request.urlopen
_sleep = time.sleep


class RateLimitError(RuntimeError):
    """The server answered 429 and asked for a longer wait than we will block for.

    A RuntimeError subclass, so callers catching the transport contract's
    RuntimeError still see it. `retry_after` is the number of seconds the
    server asked for, or None when it gave no `Retry-After` header.
    """

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    """The first `_ERROR_BODY_CHARS` of an error response, or "" if unreadable."""
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 - a body we can't read is not worth a second error
        return ""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace").strip()[:_ERROR_BODY_CHARS]


def _retry_after_seconds(value: str | None) -> int | None:
    """Parse an integer-seconds `Retry-After`. The HTTP-date form is treated as
    unknown rather than parsed — it would cost another stdlib import for a
    form these APIs have not been seen to send."""
    if value is None:
        return None
    try:
        return max(0, int(value.strip()))
    except (ValueError, AttributeError):
        return None


def _request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Perform one request with the retry policy; return (body, response headers).

    Raises:
        RateLimitError: On 429 when the server's wait is too long to honour.
        RuntimeError: On any other HTTP error status (after retries for
            502/503/504) or a connection/URL error. The message keeps the
            `Request failed (<code>): <url>` prefix and appends the start of
            the error body when there is one.
    """
    attempts = _RETRIES if retries is None else max(1, int(retries))
    merged = {"User-Agent": _USER_AGENT}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged)

    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            with _urlopen(req, timeout=timeout or _TIMEOUT) as resp:
                return resp.read(), dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            code = exc.code
            if code == 429:
                wait = _retry_after_seconds(exc.headers.get("Retry-After"))
                if not last and wait is not None and wait <= _RATE_LIMIT_MAX_WAIT:
                    _sleep(wait)
                    continue
                if wait is None:
                    hint = "the server gave no Retry-After"
                else:
                    hint = f"retry after {wait} s"
                raise RateLimitError(
                    f"Rate limit exceeded (429): {url}; {hint}. "
                    "Set USGS_API_KEY for a higher limit.",
                    retry_after=wait,
                ) from exc
            if code in _RETRY_STATUSES and not last:
                _sleep(_RETRY_BASE * 2**attempt)
                continue
            msg = f"Request failed ({code}): {url}"
            body = _read_error_body(exc)
            if body:
                msg += "\n" + body
            raise RuntimeError(msg) from exc
        except urllib.error.URLError as exc:
            # Not retried: a refused connection or failed DNS lookup is not
            # transient in the 502/503 sense, and retrying would triple the
            # wall-clock cost of every dead-host call.
            raise RuntimeError(f"Request error: {exc.reason}") from exc
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


def _with_query(url: str, params: dict[str, str] | None) -> str:
    if params:
        return url + "?" + urllib.parse.urlencode(params)
    return url


def _parse_json(raw: bytes, url: str) -> dict:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {url!r} but got:\n{text[:400]}") from exc


def fetch_json(
    url: str,
    params: dict[str, str] | None = None,
    timeout: int | None = None,
    *,
    headers: dict[str, str] | None = None,
    retries: int | None = None,
) -> dict:
    """GET a URL with optional query params, return parsed JSON.

    Args:
        url: The base URL to request.
        params: Optional query parameters, URL-encoded and appended to `url`.
        timeout: Request timeout in seconds. Defaults to `_TIMEOUT`.
        headers: Extra request headers, merged over the default `User-Agent`.
        retries: Total attempts for transient failures. Defaults to `_RETRIES`.

    Returns:
        The response body parsed as JSON.

    Raises:
        RateLimitError: If the server answers 429 with a wait longer than
            `_RATE_LIMIT_MAX_WAIT` seconds (or no wait at all).
        RuntimeError: If the request fails with any other HTTP error status
            or a connection/URL error.
        ValueError: If the response body is not valid JSON.
    """
    return fetch_json_with_headers(url, params, timeout, headers=headers, retries=retries)[0]


def fetch_json_with_headers(
    url: str,
    params: dict[str, str] | None = None,
    timeout: int | None = None,
    *,
    headers: dict[str, str] | None = None,
    retries: int | None = None,
) -> tuple[dict, dict[str, str]]:
    """Like :func:`fetch_json`, but also return the response headers.

    The USGS APIs report the caller's remaining hourly budget in
    `X-RateLimit-Limit` / `X-RateLimit-Remaining`; this is how a caller gets
    at them. Same arguments and exceptions as :func:`fetch_json`.
    """
    full = _with_query(url, params)
    raw, resp_headers = _request(full, headers=headers, timeout=timeout, retries=retries)
    return _parse_json(raw, full), resp_headers


def fetch_text(
    url: str,
    params: dict[str, str] | None = None,
    timeout: int | None = None,
    *,
    headers: dict[str, str] | None = None,
    retries: int | None = None,
) -> str:
    """GET a URL with optional query params, return the raw response body as text.

    Use for endpoints that return CSV/XML/HTML rather than JSON.

    Raises:
        RateLimitError, RuntimeError: As for :func:`fetch_json`.
    """
    full = _with_query(url, params)
    raw, _ = _request(full, headers=headers, timeout=timeout, retries=retries)
    return raw.decode("utf-8", errors="replace")


def post_json(
    url: str,
    params: dict[str, str],
    timeout: int | None = None,
    *,
    headers: dict[str, str] | None = None,
    retries: int | None = None,
) -> dict:
    """POST form-encoded params, return parsed JSON.

    Raises:
        RateLimitError, RuntimeError, ValueError: As for :func:`fetch_json`.
    """
    data = urllib.parse.urlencode(params).encode("utf-8")
    merged = {"Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        merged.update(headers)
    raw, _ = _request(url, data=data, headers=merged, timeout=timeout, retries=retries)
    return _parse_json(raw, url)


def post_json_body(
    url: str,
    body: dict | list,
    timeout: int | None = None,
    *,
    headers: dict[str, str] | None = None,
    retries: int | None = None,
) -> dict:
    """POST a JSON-serialised body, return parsed JSON.

    The OGC API's CQL2-JSON filter form takes a JSON document rather than
    form fields; this is the helper for it.

    Raises:
        RateLimitError, RuntimeError, ValueError: As for :func:`fetch_json`.
    """
    data = json.dumps(body).encode("utf-8")
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    raw, _ = _request(url, data=data, headers=merged, timeout=timeout, retries=retries)
    return _parse_json(raw, url)
