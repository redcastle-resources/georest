"""Shared helpers for the georest test suite.

Offline tests are the default and never touch the network. Live tests are
opt-in via the EDW_LIVE environment variable, because the EDW server is
genuinely unreliable - it intermittently answers 404, 500, "Layer not found",
or 200-with-no-layers for services that work moments later.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import unittest
import urllib.parse
from pathlib import Path

from georest.cloud import stac
from georest.esri import edw, services

# VALID_THEMES and theme_drift now live in edw itself, so the shipped
# `georest-check-themes` CLI can use them without importing the test suite.
# Re-exported here so the existing tests keep importing them from support.
from georest.esri.edw import VALID_THEMES, theme_drift  # noqa: F401
from georest.usgs import _ogc

FIXTURES = Path(__file__).parent / "fixtures"

_TRUTHY = ("1", "true", "yes", "on")

#: Live (network) tests run only when EDW_LIVE is set to a truthy value.
LIVE = os.environ.get("EDW_LIVE", "").lower() in _TRUTHY

requires_live = unittest.skipUnless(
    LIVE, "network test; set EDW_LIVE=1 to enable"
)

#: Live tests for `services.py` are gated separately, because they hit
#: public ArcGIS hosts (geoplatform.gov, arcgisonline.com) rather than EDW -
#: different servers, different uptime. EDW_LIVE also enables them so a
#: single flag still turns on the whole network suite.
LIVE_SERVICES = (
    os.environ.get("SERVICES_LIVE", "").lower() in _TRUTHY or LIVE
)

requires_live_services = unittest.skipUnless(
    LIVE_SERVICES,
    "network test; set SERVICES_LIVE=1 (or EDW_LIVE=1) to enable",
)

#: Live tests for `usgs.waterdata` hit api.waterdata.usgs.gov - a third
#: server with its own uptime and an anonymous rate limit of a few queries per
#: hour, so they are gated separately. EDW_LIVE turns on everything.
LIVE_USGS = (
    os.environ.get("USGS_LIVE", "").lower() in _TRUTHY or LIVE
)

requires_live_usgs = unittest.skipUnless(
    LIVE_USGS,
    "network test; set USGS_LIVE=1 (or EDW_LIVE=1) to enable",
)

#: `usfs` talks to two more servers: FIADB-API (apps.fs.usda.gov) and the
#: LCMS API (lcms-dashboard.fs2c.usda.gov), each with its own uptime.
LIVE_FIA = os.environ.get("FIA_LIVE", "").lower() in _TRUTHY or LIVE
LIVE_LCMS = os.environ.get("LCMS_LIVE", "").lower() in _TRUTHY or LIVE

#: `fema` talks to hazards.fema.gov, which resets connections at random, so
#: its live tests are gated on their own flag too.
LIVE_FEMA = os.environ.get("FEMA_LIVE", "").lower() in _TRUTHY or LIVE

requires_live_fema = unittest.skipUnless(
    LIVE_FEMA,
    "network test; set FEMA_LIVE=1 (or EDW_LIVE=1) to enable",
)

#: `noaa` talks to four NOAA servers (api.weather.gov, api.water.noaa.gov,
#: api.tidesandcurrents.noaa.gov, www.nhc.noaa.gov), gated together.
LIVE_NOAA = os.environ.get("NOAA_LIVE", "").lower() in _TRUTHY or LIVE

requires_live_noaa = unittest.skipUnless(
    LIVE_NOAA,
    "network test; set NOAA_LIVE=1 (or EDW_LIVE=1) to enable",
)

#: `cloud` talks to several STAC servers (Earth Search, Planetary Computer,
#: LandsatLook, NASA CMR-STAC), gated together.
LIVE_STAC = os.environ.get("STAC_LIVE", "").lower() in _TRUTHY or LIVE

requires_live_stac = unittest.skipUnless(
    LIVE_STAC,
    "network test; set STAC_LIVE=1 (or EDW_LIVE=1) to enable",
)

#: Earth Engine examples need credentials and `pip install "georest[ee]"`,
#: so EDW_LIVE does not turn them on.
LIVE_EE = os.environ.get("EE_LIVE", "").lower() in _TRUTHY

def load_layer_cache() -> dict[str, list[dict]]:
    """Real MapServer layer trees captured from the live catalog.

    Lets the layer-role logic be exercised against every service's real
    shape without a network round-trip. Regenerate with
    `python tools/refresh_fixtures.py`.
    """
    with open(FIXTURES / "layer_cache.json", encoding="utf-8") as handle:
        return json.load(handle)


#: How EDW's transient failures reach us. _http raises RuntimeError for
#: HTTP/URL errors, but ValueError when a response won't parse as JSON -
#: which is what a degraded HTML error page served with status 200 looks
#: like. Both are noise about the server, not signal about the code.
TRANSIENT_ERRORS = (RuntimeError, ValueError)


def retry(func, tries: int = 6, delay: float = 3.0):
    """Call func, retrying through a server's transient failures. Live tests only.

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
        f"server unavailable after {tries} attempts: {type(last).__name__}: {last}"
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
def patched_module(module, **attrs):
    """Temporarily replace attributes on any module, restoring after.

    Every provider module binds its HTTP helpers into its own namespace at
    import time (`from ._http import fetch_json, ...`), so patching `_http`
    directly has no effect - the names have to be replaced on the module
    that calls them.
    """
    saved = {name: getattr(module, name) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


@contextlib.contextmanager
def patched_services(**attrs):
    """Temporarily replace attributes on the `services` module, restoring after.

    The `services` counterpart of :func:`patched`. `services.py` binds the
    HTTP helpers into its own namespace at import time
    (`from ._http import fetch_json, ...`), so patching `_http` directly has
    no effect - the names have to be replaced on `services` itself.
    """
    with patched_module(services, **attrs):
        yield


def patched_stac(**attrs):
    """`patched_module` for `georest.cloud.stac`, which binds `fetch_json`
    into its own namespace; swap it (usually for a :class:`FakeStacServer`)."""
    return patched_module(stac, **attrs)


def patched_ogc(**attrs):
    """`patched_module` for `georest.usgs._ogc`, the one place usgs
    touches the network. `waterdata` delegates every request to it, so
    swapping `_ogc.fetch_json_with_headers` (usually for a
    :class:`FakeOgcServer`) covers the whole provider."""
    return patched_module(_ogc, **attrs)


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
        self.headers: list[dict] = []

    def __call__(self, url, params=None, timeout=None, headers=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        self.headers.append(dict(headers or {}))
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


def observation(i, time="2024-01-01", value="1.0", series="ts-1",
                site="USGS-09380000", **extra):
    """Build one USGS observation Feature the way the OGC API shapes it:
    `value` is a string, geometry is a Point, `id` is a UUID-ish string."""
    props = {"time_series_id": series, "monitoring_location_id": site,
             "parameter_code": "00060", "statistic_id": "00003",
             "time": time, "value": value, "unit_of_measure": "ft^3/s",
             "approval_status": "Approved", "qualifier": None}
    props.update(extra)
    return {"type": "Feature", "id": f"obs-{i}", "properties": props,
            "geometry": {"type": "Point", "coordinates": [-111.588, 36.864]}}


class FakeOgcServer:
    """An OGC API - Features endpoint with cursor paging, offline.

    Stands in for `_ogc.fetch_json_with_headers`: returns `(page, headers)`.
    `/collections/{id}/items` serves `features` at most `page_cap` per page
    regardless of the requested `limit` - the real server caps too - and
    emits a `rel="next"` link whose `cursor` is the next offset and which
    echoes every other query parameter, exactly as the USGS API does. The
    other endpoints answer from `collections` / `queryables`. Records every
    URL and header set so tests can assert on what was sent.

    `rate_limit=(limit, remaining)` adds `X-RateLimit-*` headers to every
    response; `fail_first=n` raises a 503-shaped RuntimeError for the first
    n calls.
    """

    def __init__(self, features=(), page_cap=10_000, rate_limit=None,
                 fail_first=0, collections=None, queryables=None):
        self.features = list(features)
        self.page_cap = page_cap
        self.rate_limit = rate_limit
        self.fail_first = fail_first
        self.collections = collections or [{"id": "daily"}, {"id": "monitoring-locations"}]
        self.queryables = queryables or {"parameter_code": {"type": "string"}}
        self.urls: list[str] = []
        self.headers: list[dict] = []

    def __call__(self, url, params=None, timeout=None, headers=None, **kwargs):
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        self.urls.append(url)
        self.headers.append(dict(headers or {}))
        if self.fail_first > 0:
            self.fail_first -= 1
            raise RuntimeError(f"Request failed (503): {url}")

        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        path = parsed.path
        resp_headers = {}
        if self.rate_limit:
            resp_headers = {"X-RateLimit-Limit": str(self.rate_limit[0]),
                            "X-RateLimit-Remaining": str(self.rate_limit[1])}

        if path.endswith("/collections"):
            return {"collections": self.collections}, resp_headers
        if path.endswith("/queryables"):
            return {"properties": self.queryables}, resp_headers
        if "/items/" in path:
            item_id = urllib.parse.unquote(path.rsplit("/items/", 1)[1])
            for feat in self.features:
                if feat.get("id") == item_id:
                    return feat, resp_headers
            raise RuntimeError(f"Request failed (404): {url}")
        if not path.endswith("/items"):
            return {"id": path.rsplit("/", 1)[1]}, resp_headers

        limit = int(query.get("limit", 100))
        offset = int(query.get("cursor", 0))
        batch = self.features[offset: offset + max(0, min(limit, self.page_cap))]
        page = {"type": "FeatureCollection", "features": batch,
                "numberReturned": len(batch), "links": []}
        if offset + len(batch) < len(self.features):
            nxt = dict(query)
            nxt["cursor"] = str(offset + len(batch))
            page["links"].append({"rel": "next", "type": "application/geo+json",
                                  "href": f"{parsed.scheme}://{parsed.netloc}{path}?"
                                          + urllib.parse.urlencode(nxt)})
        return page, resp_headers

    def query(self, index=0) -> dict:
        """The parsed query string of the index-th request (default: the first)."""
        return dict(urllib.parse.parse_qsl(
            urllib.parse.urlsplit(self.urls[index]).query, keep_blank_values=True))

    @property
    def last_query(self) -> dict:
        return self.query(-1)


def stac_collection(cid, title=None, description="A collection.", start="2015-01-01T00:00:00Z",
                    end=None, license="proprietary", keywords=()):
    """Build one STAC Collection the way an API lists it."""
    return {"type": "Collection", "id": cid, "title": title, "description": description,
            "license": license, "keywords": list(keywords),
            "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]]},
                       "temporal": {"interval": [[start, end]]}}}


class FakeStacServer:
    """A STAC API's collection list, offline, with the paging real servers do.

    Stands in for `stac.fetch_json` (GET, returns the parsed JSON).
    `/collections` serves at most `page_cap` collections per page whatever
    `limit` asks for - NASA's CMR-STAC sends 20 - and emits a `rel="next"` GET
    link carrying a `cursor`, as CMR does. `relative_links=True` makes that
    link relative, which some servers send. Anything else is a 404. Records
    every URL (query string included) in `urls`.
    """

    def __init__(self, collections=(), page_cap=100, relative_links=False):
        self.collections = list(collections)
        self.page_cap = page_cap
        self.relative_links = relative_links
        self.urls: list[str] = []

    def __call__(self, url, params=None, timeout=None, headers=None, **kwargs):
        from georest.cloud._http import CloudError
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        self.urls.append(url)
        parsed = urllib.parse.urlsplit(url)
        if not parsed.path.endswith("/collections"):
            raise CloudError(f"Request failed (404): {url}", status=404)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        offset = int(query.get("cursor", 0))
        size = max(1, min(int(query.get("limit", self.page_cap)), self.page_cap))
        batch = self.collections[offset: offset + size]
        page = {"collections": batch, "links": [{"rel": "root", "href": "/"}]}
        if offset + len(batch) < len(self.collections):
            nxt = urllib.parse.urlencode({"cursor": offset + len(batch)})
            href = (f"{parsed.path}?{nxt}" if self.relative_links
                    else f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{nxt}")
            page["links"].append({"rel": "next", "href": href, "type": "application/geo+json"})
        return page


def stac_item(iid, collection="sentinel-2-l2a", datetime="2024-07-01T18:00:00Z", cloud=5.0,
              platform="sentinel-2a", assets=None, geometry=None):
    """Build one STAC Item the way an API returns it from a search."""
    return {"type": "Feature", "stac_version": "1.0.0", "id": iid, "collection": collection,
            "bbox": [-114.2, 46.8, -113.8, 47.1],
            "geometry": geometry or {"type": "Polygon", "coordinates": [
                [[-114.2, 46.8], [-113.8, 46.8], [-113.8, 47.1], [-114.2, 46.8]]]},
            "properties": {"datetime": datetime, "eo:cloud_cover": cloud, "platform": platform},
            "assets": assets if assets is not None else {
                "thumbnail": {"href": f"https://img.example.org/{iid}.jpg", "type": "image/jpeg",
                              "roles": ["thumbnail"]},
                "B04": {"href": f"https://data.example.org/{iid}/B04.tif", "title": "Red",
                        "type": "image/tiff", "roles": ["data"]}}}


class FakeStacSearch:
    """A STAC API's /search and item endpoints, offline.

    Stands in for both `stac.post_json_body` (search, returns parsed JSON) and
    `stac.fetch_json` (item GETs and GET next links): pass the instance as
    each. Serves `items` in the order given, at most `page_cap` per page
    whatever `limit` asks for. `paging` picks how the next page is linked:
    "post" sends a POST link with a body to merge (Earth Search, Planetary
    Computer), "get" a GET link with an `offset` (NASA CMR-STAC). `honor_query`
    False makes the server ignore `query`, as some do. `matched_in` puts the
    total in `numberMatched` or in `context.matched`. Records every request in
    `calls` as (method, url, body-or-None).
    """

    def __init__(self, items=(), page_cap=50, paging="post", honor_query=True,
                 matched_in="numberMatched"):
        self.items = list(items)
        self.page_cap = page_cap
        self.paging = paging
        self.honor_query = honor_query
        self.matched_in = matched_in
        self.calls: list[tuple[str, str, dict | None]] = []

    # -- search ----------------------------------------------------------
    def post_json_body(self, url, body, timeout=None, **kwargs):
        self.calls.append(("POST", url, json.loads(json.dumps(body))))
        return self._page(url, body, int(body.get("offset", 0)))

    def _page(self, url, body, offset):
        pool = self.items
        cap = (body.get("query") or {}).get("eo:cloud_cover", {}).get("lte")
        if cap is not None and self.honor_query:
            pool = [i for i in pool if i["properties"].get("eo:cloud_cover", 0) <= cap]
        size = max(1, min(int(body.get("limit", self.page_cap)), self.page_cap))
        batch = pool[offset: offset + size]
        page = {"type": "FeatureCollection", "features": batch, "links": []}
        if self.matched_in == "numberMatched":
            page["numberMatched"] = len(pool)
        elif self.matched_in == "context":
            page["context"] = {"matched": len(pool), "returned": len(batch)}
        if offset + len(batch) < len(pool):
            if self.paging == "post":
                page["links"].append({"rel": "next", "method": "POST", "href": url,
                                      "merge": True, "body": {"offset": offset + len(batch)}})
            else:
                page["links"].append({"rel": "next", "href": f"{url}?offset={offset + len(batch)}"})
        return page

    # -- GET: next links and items ----------------------------------------
    def fetch_json(self, url, params=None, timeout=None, **kwargs):
        from georest.cloud._http import CloudError
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        self.calls.append(("GET", url, None))
        parsed = urllib.parse.urlsplit(url)
        if parsed.path.endswith("/search"):
            offset = int(dict(urllib.parse.parse_qsl(parsed.query)).get("offset", 0))
            first = next((c[2] for c in self.calls if c[0] == "POST"), {})
            return self._page(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", first, offset)
        if "/items/" in parsed.path:
            iid = urllib.parse.unquote(parsed.path.rsplit("/items/", 1)[1])
            for item in self.items:
                if item["id"] == iid:
                    return item
        raise CloudError(f"Request failed (404): {url}", status=404)

    def bodies(self):
        """The JSON bodies of every POST made."""
        return [c[2] for c in self.calls if c[0] == "POST"]
