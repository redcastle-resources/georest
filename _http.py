"""
Shared stdlib-only HTTP/JSON helpers used across hostedServiceTools modules.

No third-party dependencies — GET/POST + JSON parsing via urllib only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT = 30  # seconds for HTTP requests
_USER_AGENT = "hostedServiceTools/1.0"


def fetch_json(url: str, params: dict[str, str] | None = None) -> dict:
    """GET a URL with optional query params, return parsed JSON.

    Args:
        url: The base URL to request.
        params: Optional query parameters, URL-encoded and appended to `url`.

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
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Request failed ({exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request error: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {url!r} but got:\n{raw[:400]}") from exc


def fetch_text(url: str, params: dict[str, str] | None = None) -> str:
    """GET a URL with optional query params, return the raw response body as text.

    Use for endpoints that return XML/HTML rather than JSON (e.g. the Esri
    REST `/metadata` operation).

    Raises:
        RuntimeError: If the request fails with an HTTP error status or a
            connection/URL error.
    """
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Request failed ({exc.code}): {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request error: {exc.reason}") from exc


def post_json(url: str, params: dict[str, str]) -> dict:
    """POST form-encoded params, return parsed JSON. Used for large queries.

    Args:
        url: The URL to POST to.
        params: Form parameters, URL-encoded into the request body.

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
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
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
