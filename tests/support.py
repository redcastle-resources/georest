"""Shared helpers for the geoREST test suite.

Offline tests are the default and never touch the network. Live tests are
opt-in via the EDW_LIVE environment variable, because the EDW server is
genuinely unreliable — it intermittently answers 404, 500, "Layer not found",
or 200-with-no-layers for services that work moments later.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import unittest
from pathlib import Path

from geoREST.RESTesri import edw, services

# VALID_THEMES and theme_drift now live in edw itself, so the shipped
# `georest-check-themes` CLI can use them without importing the test suite.
# Re-exported here so the existing tests keep importing them from support.
from geoREST.RESTesri.edw import VALID_THEMES, theme_drift  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"

_TRUTHY = ("1", "true", "yes", "on")

#: Live (network) tests run only when EDW_LIVE is set to a truthy value.
LIVE = os.environ.get("EDW_LIVE", "").lower() in _TRUTHY

requires_live = unittest.skipUnless(
    LIVE, "network test; set EDW_LIVE=1 to enable"
)

#: Live tests for `services.py` are gated separately, because they hit
#: public ArcGIS hosts (geoplatform.gov, arcgisonline.com) rather than EDW —
#: different servers, different uptime. EDW_LIVE also enables them so a
#: single flag still turns on the whole network suite.
LIVE_SERVICES = (
    os.environ.get("SERVICES_LIVE", "").lower() in _TRUTHY or LIVE
)

requires_live_services = unittest.skipUnless(
    LIVE_SERVICES,
    "network test; set SERVICES_LIVE=1 (or EDW_LIVE=1) to enable",
)

def load_layer_cache() -> dict[str, list[dict]]:
    """Real MapServer layer trees captured from the live catalog.

    Lets the layer-role logic be exercised against every service's real
    shape without a network round-trip. Regenerate with
    `python tools/refresh_fixtures.py`.
    """
    with open(FIXTURES / "layer_cache.json", encoding="utf-8") as handle:
        return json.load(handle)


#: How EDW's transient failures reach us. _http raises RuntimeError for
#: HTTP/URL errors, but ValueError when a response won't parse as JSON —
#: which is what a degraded HTML error page served with status 200 looks
#: like. Both are noise about the server, not signal about the code.
TRANSIENT_ERRORS = (RuntimeError, ValueError)


def retry(func, tries: int = 6, delay: float = 3.0):
    """Call func, retrying through EDW's transient failures. Live tests only.

    Skips the test rather than failing it once the retries are exhausted: a
    server that is simply down says nothing about the code under test.
    """
    last = None
    for attempt in range(tries):
        try:
            return func()
        except TRANSIENT_ERRORS as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(delay)
    raise unittest.SkipTest(
        f"EDW unavailable after {tries} attempts: {type(last).__name__}: {last}"
    )


class FakeServer:
    """An ArcGIS layer that truncates every response to `max_record_count`.

    Models the behaviour that made the hardcoded 2000-record stride lose
    data: the server silently returns fewer rows than asked for.
    """

    def __init__(self, max_record_count: int = 1000, total: int = 5000):
        self.max_record_count = max_record_count
        self.total = total
        self.requests: list[dict] = []

    def post_json(self, url, params, *args, **kwargs):
        self.requests.append(dict(params))
        if params.get("returnIdsOnly") == "true":
            return {"objectIdFieldName": "objectid",
                    "objectIds": list(range(1, self.total + 1))}
        if params.get("returnCountOnly") == "true":
            return {"count": self.total}

        where = params.get("where", "")
        if " IN (" in where:
            inner = where.split(" IN (", 1)[1].rstrip(")")
            ids = [int(x) for x in inner.split(",") if x.strip()]
            kept = ids[: self.max_record_count]
            return {
                "type": "FeatureCollection",
                "features": [self._feature(i) for i in kept],
                "exceededTransferLimit": len(kept) < len(ids),
            }

        requested = int(params.get("resultRecordCount", self.max_record_count))
        offset = int(params.get("resultOffset", 0))
        count = max(0, min(requested, self.max_record_count, self.total - offset))
        out = {"type": "FeatureCollection",
               "features": [self._feature(offset + i + 1) for i in range(count)]}
        if offset + count < self.total:
            out["exceededTransferLimit"] = True
        return out

    @staticmethod
    def _feature(oid):
        return {"type": "Feature", "properties": {"objectid": oid},
                "geometry": None}


@contextlib.contextmanager
def patched(**attrs):
    """Temporarily replace attributes on the edw module, restoring after.

    Used to stand in for the network layer (fetch_json / post_json /
    get_layer_info) without touching the real server.
    """
    saved = {name: getattr(edw, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(edw, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(edw, name, value)
        edw._MAX_RECORD_COUNT_CACHE.clear()


@contextlib.contextmanager
def patched_services(**attrs):
    """Temporarily replace attributes on the `services` module, restoring after.

    The `services` counterpart of :func:`patched`. `services.py` binds the
    HTTP helpers into its own namespace at import time
    (`from ._http import fetch_json, ...`), so patching `_http` directly has
    no effect — the names have to be replaced on `services` itself.
    """
    saved = {name: getattr(services, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(services, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(services, name, value)


class RecordingFetch:
    """Stand-in for `fetch_json` that records calls and replays canned answers.

    Construct with either a single response (returned for every call) or a
    list of responses returned in order. An entry that is an exception
    instance (or class) is raised rather than returned, which is how the
    transport-failure and format-fallback paths get exercised offline.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        item = (self.responses[len(self.calls) - 1]
                if len(self.calls) <= len(self.responses)
                else self.responses[-1])
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        return item

    @property
    def last_params(self) -> dict:
        return self.calls[-1][1]

    @property
    def last_url(self) -> str:
        return self.calls[-1][0]


@contextlib.contextmanager
def fake_layers(layers):
    """Serve a fixed MapServer layer list to the layer-inspection helpers."""
    with patched(fetch_json=lambda url, params=None, *a, **k: {"layers": layers}):
        yield


def layer(layer_id, name, type_="Feature Layer", parent=-1, min_scale=0,
          max_scale=0):
    """Build a MapServer layer entry with EDW's field names."""
    return {"id": layer_id, "name": name, "type": type_,
            "parentLayerId": parent, "defaultVisibility": True,
            "minScale": min_scale, "maxScale": max_scale}
