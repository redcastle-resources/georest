"""Private stdlib-only HTTP/JSON helper for the georest.noaa modules.

Four NOAA services sit behind this provider - the NWS API
(api.weather.gov), the National Water Prediction Service
(api.water.noaa.gov), CO-OPS Tides and Currents
(api.tidesandcurrents.noaa.gov) and the National Hurricane Center
(www.nhc.noaa.gov). What they share, and what this module handles:

* **Transient failures are common.** The NWS API answers 500, 502 and 503
  for requests that succeed a moment later, and connections are dropped
  under load. Those are retried briefly; anything else is raised at once.
* **Some answers carry their status in the body.** CO-OPS reports a bad
  request as HTTP 200 with ``{"error": {"message": ...}}``; callers check
  for it, and :class:`NOAAError` carries the HTTP status where there is
  one, so "this point has no NWS forecast" (404) can be told apart from
  "the service is down".

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

_TIMEOUT = 30
#: The NWS API asks every caller to identify itself in the User-Agent.
_USER_AGENT = f"georest/{_VERSION} (+https://github.com/redcastle-resources/georest)"

_RETRIES = 3
_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_RETRY_BASE = 1.0  # seconds; attempt n sleeps _RETRY_BASE * 2**n (1, 2 s)

_urlopen = urllib.request.urlopen
_sleep = time.sleep


class NOAAError(RuntimeError):
    """A NOAA service refused the request or could not be reached.

    ``status`` is the HTTP status, or ``None`` when no response came back.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _reset(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ConnectionResetError, ConnectionAbortedError,
                               TimeoutError))


def fetch_json(url: str, params: dict | None = None, timeout: int | None = None,
               *, accept: str = "application/json") -> dict:
    """GET ``url`` with ``params``; return the parsed JSON.

    Raises:
        NOAAError: On an HTTP error status (``status`` set) or a connection
            that failed on every attempt (``status`` None).
        ValueError: If the body is not JSON.
    """
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(full, headers={"User-Agent": _USER_AGENT,
                                                "Accept": accept})
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
            detail = ""
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace"))
                detail = body.get("detail") or body.get("title") or ""
            except Exception:  # noqa: BLE001 - a body we can't read is not worth a second error
                pass
            raise NOAAError(f"Request failed ({exc.code}): {full}"
                            + (f"\n{detail}" if detail else ""), status=exc.code) from exc
        except (urllib.error.URLError, OSError) as exc:
            if _reset(exc) and not last:
                _sleep(_RETRY_BASE * 2**attempt)
                continue
            raise NOAAError(f"Request error: {getattr(exc, 'reason', exc)}: {full}") from exc
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {full!r} but got:\n{text[:400]}") from exc
