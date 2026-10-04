"""
Shared stdlib-only HTTP/JSON helpers used across the georest.restesri modules.

No third-party dependencies — GET/POST + JSON parsing via urllib only.

Copyright 2026 Ryan Rock and Ian Housman

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
_USER_AGENT = f"georest/{_VERSION} (+https://github.com/redcastle-resources/georest)"


def fetch_json(url: str, params: dict[str, str] | None = None, timeout: int | None = None) -> dict:
    """GET a URL with optional query params, return parsed JSON.

    Args:
        url: The base URL to request.
        params: Optional query parameters, URL-encoded and appended to `url`.
            See `_credentials` for how a `token` entry is treated.
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
    return _request(url, params, timeout, post=False, parse_json=True)


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
    body, _ = _request(url, params, timeout, post=False, parse_json=False)
    return body.decode("utf-8")


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
    return _request(url, params, timeout, post=False, parse_json=False)


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
    return _request(url, params, timeout, post=True, parse_json=True)


def _credentials(url: str, params: dict | None) -> tuple[dict, dict[str, str], bool]:
    """Decide which token, if any, a request carries.

    A `token` key in `params` is the caller's explicit choice: a non-empty
    value is sent as-is, and `""` means anonymous for this request (the key is
    dropped). With no `token` key, the signed-in `auth` session's token is
    added when that session covers `url`'s host.

    Returns:
        (params to send, headers, whether the token came from the session).
    """
    params = dict(params or {})
    headers = {"User-Agent": _USER_AGENT}
    if "token" in params:
        if not params["token"]:
            del params["token"]
        return params, headers, False
    from . import auth  # lazy: auth imports this module

    token, extra = auth.credentials_for(url)
    if not token:
        return params, headers, False
    params["token"] = token
    headers.update(extra)
    return params, headers, True


def _request(url: str, params: dict | None, timeout: int | None, *, post: bool, parse_json: bool):
    """Send one request; retry once if the session token was rejected (498)."""
    for attempt in (0, 1):
        sent, headers, from_session = _credentials(url, params)
        if post:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            shown = url
            req = urllib.request.Request(url, data=urllib.parse.urlencode(sent).encode("utf-8"),
                                         headers=headers)
        else:
            shown = url + "?" + urllib.parse.urlencode(sent) if sent else url
            req = urllib.request.Request(shown, headers=headers)
        retry = from_session and attempt == 0
        try:
            with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as resp:
                body = resp.read()
                content_type = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if retry and exc.code == 498 and _refresh_rejected():
                continue
            raise RuntimeError(f"Request failed ({exc.code}): {_redact(shown)}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Request error: {exc.reason}") from exc
        if not parse_json:
            return body, content_type
        raw = body.decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Expected JSON from {_redact(shown)!r} but got:\n{raw[:400]}") from exc
        if retry and _esri_error_code(data) == 498 and _refresh_rejected():
            continue
        return data
    raise AssertionError("unreachable")


def _refresh_rejected() -> bool:
    from . import auth

    return auth._refresh_rejected()


def _esri_error_code(data) -> int | None:
    err = data.get("error") if isinstance(data, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, int) else None


def _redact(url: str) -> str:
    """`url` with any `token` query value replaced, safe for error messages."""
    parts = urllib.parse.urlsplit(url)
    if "token=" not in parts.query.lower():
        return url
    query = urllib.parse.urlencode([
        (k, "REDACTED" if k.lower() == "token" else v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    ])
    return urllib.parse.urlunsplit(parts._replace(query=query))


def build_params(base: dict, token: str | None) -> dict:
    """Merge `token` into a params dict if supplied.

    `None` leaves the choice to the signed-in `auth` session; `""` forces an
    anonymous request (see `_credentials`).
    """
    if token is not None:
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
    if err.get("code") in (498, 499):
        msg += (" [sign in with `georest-login` or georest.restesri.auth.login(); "
                "auth.status() shows the current session]")
    return msg
