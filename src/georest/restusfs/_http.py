"""Private stdlib-only HTTP/JSON helpers for the georest.restusfs modules.

Both upstreams are public, unauthenticated, and occasionally slow. They
also fail in different ways, and one of them fails in a way that is easy
to miss:

* **FIADB-API** signals most failures with an HTTP status, the ordinary
  case, but delivers its server errors as an HTML error page under HTTP
  200.
* **LCMS** returns **HTTP 200 with a ``ParameterError`` inside the body**
  when the requested summary area does not exist. A client that checks
  the status alone treats that as success and hands back an empty
  result, which is the most likely source of silent wrong answers in
  this package. :func:`get_json` therefore inspects the payload, not just
  the status line.

Self-contained like the other providers' transports (``restesri._http``,
``restusgs._http``): this one knows the two Forest Service error dialects.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import __version__ as _VERSION

logger = logging.getLogger(__name__)

#: Both APIs are government-hosted and can take several seconds on a cold
#: cache. A 60s ceiling is generous enough that a slow-but-working call
#: succeeds, and short enough that a hung one surfaces during a notebook
#: session rather than at the end of it.
DEFAULT_TIMEOUT = 60

#: Retries apply to transport errors and 5xx only, never to a 4xx, which
#: means the request was wrong and will stay wrong.
DEFAULT_RETRIES = 2

_USER_AGENT = f"georest/{_VERSION} (+https://github.com/redcastle-resources/georest)"

# Module-level bindings so tests can stand in for the network and the clock
# without touching urllib or sleeping for real.
_urlopen = urllib.request.urlopen
_sleep = time.sleep


class USFSError(RuntimeError):
    """Base for every error raised by georest.restusfs."""


class UpstreamError(USFSError):
    """The API was reached but refused or failed the request.

    Carries ``url`` and, when the upstream provided one, the parameters
    it echoed back. LCMS returns those, and they are usually enough to
    see the mistake without re-reading the call site.
    """

    def __init__(self, message: str, *, url: str = "",
                 provided: dict | None = None):
        super().__init__(message)
        self.url = url
        self.provided = provided or {}


class UpstreamUnavailable(USFSError):
    """The API could not be reached at all.

    Distinct from :class:`UpstreamError` because callers respond to it
    differently: a bad parameter needs a code change, an unreachable
    host needs a retry later or a fall back to cached data.
    """


def get_json(url: str, params: dict | None = None, *,
             method: str = "GET",
             timeout: int = DEFAULT_TIMEOUT,
             retries: int = DEFAULT_RETRIES) -> Any:
    """Request ``url`` and return decoded JSON, raising on either failure mode.

    Args:
        url: Absolute URL. Trailing slashes matter to LCMS; see
            :mod:`georest.restusfs.lcms` for why its paths are written
            the way they are.
        params: Query parameters (GET) or form fields (POST).
        method: ``"GET"`` or ``"POST"``. FIADB-API accepts both for
            ``/fullreport``; POST keeps a long ``strFilter`` out of the
            URL.
        timeout: Seconds before giving up on a single attempt.
        retries: Extra attempts after the first, for transport errors,
            5xx responses and FIADB-API's HTML error pages only.

    Returns:
        The decoded JSON body, usually a ``dict`` or ``list``.

    Raises:
        UpstreamError: The request was rejected, returned a non-2xx, was
            not JSON, or carried an in-body error (LCMS's
            ``ParameterError``).
        UpstreamUnavailable: The host could not be reached within
            ``timeout`` across all attempts.
    """
    method = method.upper()
    query = urllib.parse.urlencode(params or {}, doseq=True)
    full_url = f"{url}?{query}" if query and method == "GET" else url
    data = query.encode("utf-8") if query and method == "POST" else None
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    for attempt in range(retries + 1):
        last = attempt == retries
        req = urllib.request.Request(full_url, data=data, headers=headers,
                                     method=method)
        try:
            with _urlopen(req, timeout=timeout) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "")
                text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = _read_error_body(exc)
            if exc.code >= 500 and not last:
                _sleep(1 + attempt)
                continue
            raise UpstreamError(
                f"HTTP {exc.code} from {full_url}" + (f": {body}" if body else ""),
                url=full_url,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if not last:
                # Linear, not exponential: these are public services with
                # no published rate limit, and the failures seen are
                # transient DNS/TLS rather than throttling.
                _sleep(1 + attempt)
                continue
            raise UpstreamUnavailable(
                f"could not reach {full_url} after {retries + 1} attempt(s): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            payload = json.loads(text)
        except ValueError as exc:
            # HTML where JSON was asked for. Two very different causes,
            # and telling them apart matters because the advice differs.
            server_err = _evalidator_error(text)
            if server_err:
                # FIADB-API delivers its 500s as an HTML error page under
                # HTTP 200 ("Internal Server Error: list index out of
                # range" and the like). Seen affecting every endpoint at
                # once, including ones that worked minutes earlier, so it
                # behaves like a 5xx and is retried like one.
                if not last:
                    _sleep(1 + attempt)
                    continue
                raise UpstreamError(
                    f"FIADB-API returned a server error (as HTTP {status} with "
                    f"an HTML error page): {server_err}. This is upstream, not "
                    f"a bad request: it has been seen to affect every endpoint "
                    f"at once and clear on its own. Cached vocabularies still "
                    f"work offline; see georest.restusfs.vocab.",
                    url=full_url,
                ) from exc
            raise UpstreamError(
                f"expected JSON from {full_url} but got {content_type or 'unknown'!r}. "
                f"FIADB-API parameter endpoints need outputFormat=JSON; "
                f"without it they serve a browser table.",
                url=full_url,
            ) from exc

        _raise_for_inband_error(payload, full_url)
        return payload

    # Unreachable: the last attempt always returns or raises.
    raise UpstreamUnavailable(f"could not reach {full_url}")


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    """The whole error response body, or "" if it cannot be read."""
    try:
        return exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _evalidator_error(html: str) -> str:
    """Extract FIADB-API's error text from its HTML error page, or ''.

    The API answers failures with a rendered page under HTTP 200::

        EVALIDator | Error Page
        Error Type: Internal Server Error
        Received an Error: list index out of range

    Pulling those two lines out turns "expected JSON, got text/html",
    which reads like a client mistake and sends people looking for a
    missing ``outputFormat``, into the upstream error it actually is.
    """
    # Only the page head is checked for the marker: the error page names
    # itself in its title, and a long ordinary page mentioning EVALIDator
    # in passing further down is not an error page.
    if not html or "EVALIDator" not in html[:4000]:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    # Bounded by the label that follows, not by a character class: the
    # error type is itself capitalized ("Internal Server Error"), so a
    # [^A-Z] run matches nothing at all.
    kind = re.search(r"Error Type:\s*(.+?)\s*(?:Received an Error|API Version|$)",
                     text)
    detail = re.search(r"Received an Error:\s*(.+?)\s*(?:If you used|API Version|$)",
                       text)
    parts = [m.group(1).strip() for m in (kind, detail) if m]
    if parts:
        return " - ".join(parts)
    return "unspecified EVALIDator error" if "Error Page" in text else ""


def _raise_for_inband_error(payload: Any, url: str) -> None:
    """Raise if ``payload`` carries an error despite a 2xx status.

    LCMS answers an unknown summary area with 200 and::

        {"Result": {"ParameterError": "Invalid Summary Area for ...",
                    "ProvidedParameters": {...}}}

    The echoed parameters are genuinely useful, so they are attached to
    the exception rather than discarded.
    """
    if not isinstance(payload, dict):
        return
    result = payload.get("Result")
    if not isinstance(result, dict):
        return
    err = result.get("ParameterError")
    if not err:
        return
    provided = result.get("ProvidedParameters")
    provided = provided if isinstance(provided, dict) else {}
    detail = (f" (sent: {', '.join(f'{k}={v!r}' for k, v in provided.items())})"
              if provided else "")
    raise UpstreamError(f"{err}{detail}", url=url, provided=provided)
