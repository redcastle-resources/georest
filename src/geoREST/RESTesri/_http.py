"""
Shared stdlib-only HTTP/JSON helpers used across the geoREST.RESTesri modules.

No third-party dependencies — GET/POST + JSON parsing via urllib only.

Copyright 2026 Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__ as _VERSION

_TIMEOUT = 60  # default seconds for HTTP requests; override per-call via `timeout`

#: Sent on every outbound request. Derived from the package version so the
#: servers we query can attribute traffic to a specific release.
_USER_AGENT = f"geoREST/{_VERSION} (+https://github.com/redcastle-resources/georest)"


def fetch_json(url: str, params: dict[str, str] | None = None, timeout: int | None = None) -> dict:
    """GET a URL with optional query params, return parsed JSON.

    Args:
        url: The base URL to request.
        params: Optional query parameters, URL-encoded and appended to `url`.
        timeout: Request timeout in seconds. Defaults to `_TIMEOUT`. Some
            ArcGIS Image Service operations against large mosaic catalogs
            (hundreds of contributing rasters) or fine `pixelSize` requests
            over a sizable area can genuinely take 30-45+ seconds server-side
            even for a simple answer (verified against live services) — pass
            a larger value for those rather than assuming a hang.

    Returns:
        The response body parsed as JSON.

    Raises:
        RuntimeError: If the request fails with an HTTP error status or a
            connection/URL error.
        ValueError: If the response body is not valid JSON.
    """
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Request failed ({exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request error: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {url!r} but got:\n{raw[:400]}") from exc


def fetch_text(url: str, params: dict[str, str] | None = None, timeout: int | None = None) -> str:
    """GET a URL with optional query params, return the raw response body as text.

    Use for endpoints that return XML/HTML rather than JSON (e.g. the Esri
    REST `/metadata` operation).

    Args:
        timeout: Request timeout in seconds. Defaults to `_TIMEOUT`.

    Raises:
        RuntimeError: If the request fails with an HTTP error status or a
            connection/URL error.
    """
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Request failed ({exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request error: {exc.reason}") from exc


def fetch_bytes(url: str, params: dict[str, str] | None = None, timeout: int | None = None) -> tuple[bytes, str]:
    """GET a URL with optional query params, return (raw bytes, content-type).

    Use for binary endpoints (e.g. the Esri REST `exportImage` operation)
    where `fetch_text`'s UTF-8 decode would corrupt the response body.

    Args:
        timeout: Request timeout in seconds. Defaults to `_TIMEOUT`.

    Raises:
        RuntimeError: If the request fails with an HTTP error status or a
            connection/URL error.
    """
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Request failed ({exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request error: {exc.reason}") from exc


def post_json(url: str, params: dict[str, str], timeout: int | None = None) -> dict:
    """POST form-encoded params, return parsed JSON. Used for large queries.

    Args:
        url: The URL to POST to.
        params: Form parameters, URL-encoded into the request body.
        timeout: Request timeout in seconds. Defaults to `_TIMEOUT`.

    Returns:
        The response body parsed as JSON.

    Raises:
        RuntimeError: If the request fails with an HTTP error status or a
            connection/URL error.
        ValueError: If the response body is not valid JSON.
    """
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Request failed ({exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request error: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {url!r} but got:\n{raw[:400]}") from exc


def build_params(base: dict, token: str | None) -> dict:
    """Merge `token` into a params dict if supplied."""
    if token:
        return {**base, "token": token}
    return base


def format_esri_error(err: dict) -> str:
    """Format an Esri error object for a raised exception message.

    Esri's `err["message"]` is frequently a generic, unhelpful string (e.g.
    "Invalid or missing input parameters") while the actually-diagnostic
    text lives in `err["details"]` (e.g. "The requested image exceeds the
    size limit.") — verified against a live service, where the bare message
    alone was actively misleading. Always include details when present.

    Lives here rather than in `services.py` so `portal.py` can use it too:
    `services` imports from `portal`, so the reverse import would be a cycle.
    """
    msg = f"{err.get('code')} — {err.get('message', str(err))}"
    details = err.get("details")
    if details:
        msg += f" ({'; '.join(str(d) for d in details)})"
    return msg
