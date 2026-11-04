"""Private stdlib-only HTTP/JSON helper for the georest.cloud modules.

The services behind this provider are STAC APIs: Earth Search, Planetary
Computer, USGS LandsatLook, NASA CMR-STAC and any other server that speaks
the STAC API specification. What they share, and what this module handles:

* **Searches are POSTs with a JSON body** (CQL2-JSON filters), so there is a
  :func:`post_json_body` beside :func:`fetch_json`, and both take
  ``headers=`` for a bearer token.
* **Gateways fail briefly.** 502, 503 and 504 are retried with a short
  backoff, and a 429 only when the server's ``Retry-After`` is short. The
  policy is tuned for a tool call inside an agent: it never blocks for more
  than a few seconds, and a longer wait is raised with ``retry_after`` set
  so the caller can relay it.
* **The first part of an error body is kept.** STAC servers explain a
  rejected query in the body (``description``, ``detail``); the message
  carries it so the caller can say what to change.

Self-contained like the other providers' transports.

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

_TIMEOUT = 60  # default seconds for HTTP requests; override per call via `timeout`

#: Sent on every outbound request, so the servers queried can attribute
#: traffic to a specific release.
_USER_AGENT = f"georest/{_VERSION} (+https://github.com/redcastle-resources/georest)"

#: Total attempts per request (so 3 means two retries); `retries=1` disables retrying.
_RETRIES = 3
#: Transient gateway failures worth a short backoff. 500 is absent on purpose:
#: from a STAC API it means "your query failed", not "try again".
_RETRY_STATUSES = frozenset({502, 503, 504})
_RETRY_BASE = 1.0  # seconds; attempt n sleeps _RETRY_BASE * 2**n (1 s, 2 s, ...)
#: A 429 is retried only if the server asks for at most this many seconds.
_RATE_LIMIT_MAX_WAIT = 5
_ERROR_BODY_CHARS = 400

# Module-level bindings so tests can stand in for the network and the clock.
_urlopen = urllib.request.urlopen
_sleep = time.sleep


class CloudError(RuntimeError):
    """A STAC server refused the request or could not be reached.

    ``status`` is the HTTP status, or ``None`` when no response came back.
    ``retry_after`` is the wait in seconds a 429 asked for, or ``None``.
    """

    def __init__(self, message: str, status: int | None = None,
                 retry_after: int | None = None):
        super().__init__(message)
        self.status = status
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
    """An integer-seconds `Retry-After`. The HTTP-date form is treated as unknown."""
    if value is None:
        return None
    try:
        return max(0, int(value.strip()))
    except (ValueError, AttributeError):
        return None


def _request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
             timeout: int | None = None, retries: int | None = None) -> bytes:
    """Perform one request with the retry policy; return the body.

    Raises:
        CloudError: On an HTTP error status (after retries for 502/503/504 and
            a short 429), or a connection error. The message keeps the
            ``Request failed (<code>): <url>`` prefix and appends the start of
            the error body when there is one.
    """
    attempts = _RETRIES if retries is None else max(1, int(retries))
    merged = {"User-Agent": _USER_AGENT, "Accept": "application/geo+json, application/json"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged)

    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            with _urlopen(req, timeout=timeout or _TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            code = exc.code
            if code == 429:
                wait = _retry_after_seconds(exc.headers.get("Retry-After"))
                if not last and wait is not None and wait <= _RATE_LIMIT_MAX_WAIT:
                    _sleep(wait)
                    continue
                hint = ("the server gave no Retry-After" if wait is None
                        else f"retry after {wait} s")
                raise CloudError(f"Rate limit exceeded (429): {url}; {hint}.",
                                 status=429, retry_after=wait) from exc
            if code in _RETRY_STATUSES and not last:
                _sleep(_RETRY_BASE * 2**attempt)
                continue
            msg = f"Request failed ({code}): {url}"
            body = _read_error_body(exc)
            if body:
                msg += "\n" + body
            raise CloudError(msg, status=code) from exc
        except urllib.error.URLError as exc:
            # Not retried: a refused connection or failed DNS lookup is not
            # transient in the 502/503 sense, and retrying would triple the
            # wall-clock cost of every dead-host call.
            raise CloudError(f"Request error: {exc.reason}: {url}") from exc
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


def _parse_json(raw: bytes, url: str) -> dict:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {url!r} but got:\n{text[:400]}") from exc


def fetch_json(url: str, params: dict | None = None, timeout: int | None = None, *,
               headers: dict[str, str] | None = None, retries: int | None = None) -> dict:
    """GET a URL with optional query params, return parsed JSON.

    Args:
        url: The base URL to request.
        params: Optional query parameters, URL-encoded and appended to `url`.
        timeout: Request timeout in seconds. Defaults to `_TIMEOUT`.
        headers: Extra request headers, merged over the defaults.
        retries: Total attempts for transient failures. Defaults to `_RETRIES`.

    Raises:
        CloudError: On an HTTP error status or a connection error.
        ValueError: If the response body is not valid JSON.
    """
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    return _parse_json(_request(full, headers=headers, timeout=timeout, retries=retries), full)


def post_json_body(url: str, body: dict | list, timeout: int | None = None, *,
                   headers: dict[str, str] | None = None, retries: int | None = None) -> dict:
    """POST a JSON-serialised body, return parsed JSON.

    STAC search takes its CQL2-JSON filter this way.

    Raises:
        CloudError, ValueError: As for :func:`fetch_json`.
    """
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    raw = _request(url, data=json.dumps(body).encode("utf-8"), headers=merged,
                   timeout=timeout, retries=retries)
    return _parse_json(raw, url)
