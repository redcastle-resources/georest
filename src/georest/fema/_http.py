"""Private stdlib-only HTTP/JSON helper for the georest.fema modules.

FEMA's map services (hazards.fema.gov) fail in two ways a plain fetch
misses:

* **Reset connections.** The server drops connections at random - a
  request fails with "connection reset by peer" and the same request
  succeeds a second later. Unlike a refused connection or a failed DNS
  lookup, that is transient, so it is retried here, briefly.
* **Errors under HTTP 200.** Like every ArcGIS server, a query that fails
  answers 200 with ``{"error": {...}}`` in the body. Read as an empty
  result it would say "no flood zone here" when nothing was checked, so
  :func:`fetch_json` raises instead.

Self-contained like the other providers' transports (``esri._http``,
``usgs._http``, ``usfs._http``).

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

_TIMEOUT = 30  # seconds per request; FEMA answers a point query in well under 1 s
_USER_AGENT = f"georest/{_VERSION} (+https://github.com/redcastle-resources/georest)"

#: Total attempts per request. Tuned for a tool call inside an agent: the
#: worst case is a few seconds, never a long block.
_RETRIES = 4
_RETRY_STATUSES = frozenset({502, 503, 504})
_RETRY_BASE = 1.0  # seconds; attempt n sleeps _RETRY_BASE * 2**n (1, 2, 4 s)

# Module-level bindings so tests can stand in for the network and the clock.
_urlopen = urllib.request.urlopen
_sleep = time.sleep


def _reset(exc: BaseException) -> bool:
    """Whether a failure is a dropped connection worth one more try."""
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ConnectionResetError, ConnectionAbortedError,
                               TimeoutError))


def fetch_json(url: str, params: dict[str, str] | None = None,
               timeout: int | None = None) -> dict:
    """GET ``url`` with ``params`` and return the parsed JSON.

    Raises:
        RuntimeError: On an HTTP error status, a connection that failed on
            every attempt, or an ArcGIS error object in a 200 response. The
            message names the URL and, for an ArcGIS error, its message.
        ValueError: If the body is not JSON.
    """
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(full, headers={"User-Agent": _USER_AGENT})
    for attempt in range(_RETRIES):
        last = attempt == _RETRIES - 1
        try:
            with _urlopen(req, timeout=timeout or _TIMEOUT) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_STATUSES and not last:
                _sleep(_RETRY_BASE * 2**attempt)
                continue
            raise RuntimeError(f"Request failed ({exc.code}): {full}") from exc
        except (urllib.error.URLError, OSError) as exc:
            if _reset(exc) and not last:
                _sleep(_RETRY_BASE * 2**attempt)
                continue
            raise RuntimeError(
                f"Request error: {getattr(exc, 'reason', exc)}: {full}") from exc
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {full!r} but got:\n{text[:400]}") from exc
    if isinstance(data, dict) and "error" in data:
        err = data["error"] or {}
        raise RuntimeError(
            f"FEMA's service answered with an error "
            f"({err.get('code')}: {err.get('message')}): {full}")
    return data
