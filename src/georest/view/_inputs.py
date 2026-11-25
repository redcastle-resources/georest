"""What Map.addLayer was given: shared by georest.view and geeViz, so both
read a first argument the same way. No network, no Earth Engine import.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
import json
import re
from urllib.parse import parse_qs, urlparse

ACCEPTED = ("an ArcGIS service URL (.../FeatureServer, MapServer or ImageServer), a tile URL with {z}, {x} "
            "and {y}, a GeoJSON URL (.geojson, .json or f=geojson), GeoJSON (text, a dict or features), "
            "or, in geeViz, an Earth Engine asset id or object")

_ESRI = re.compile(r"/rest/services/.+/(FeatureServer|MapServer|ImageServer)(/\d+)?$", re.I)
_ASSET = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)+$")
_GEOJSON_TYPES = {"Feature", "FeatureCollection", "Point", "MultiPoint", "LineString", "MultiLineString",
                  "Polygon", "MultiPolygon", "GeometryCollection"}


def classify(obj):
    """(kind, value) for addLayer's first argument. kind is "esri", "xyz",
    "geojson_url", "geojson" (parsed GeoJSON text), "asset" (an Earth Engine
    asset id), or None for anything that is not a string or a search result
    (the caller handles objects). ValueError for a string none of these."""
    # a portal search result: a dict with a url, whose "type" (if any) is
    # the item's ("Map Service"), not a GeoJSON type
    if isinstance(obj, dict) and "url" in obj and obj.get("type") not in _GEOJSON_TYPES:
        obj = str(obj["url"] or "")
        if not obj:
            raise ValueError("this search result has no service url")
    if not isinstance(obj, str):
        return None, obj
    s = obj.strip()
    if s[:1] in ("{", "["):
        try:
            return "geojson", json.loads(s)
        except ValueError:
            raise ValueError("This looks like GeoJSON but is not valid JSON. addLayer takes " + ACCEPTED) from None
    u = urlparse(s)
    if u.scheme in ("http", "https") and u.netloc:
        if all(t in s for t in ("{z}", "{x}", "{y}")):
            return "xyz", s
        path = u.path.rstrip("/")
        if _ESRI.search(path):
            if "token" in {k.lower() for k in parse_qs(u.query)}:
                raise ValueError("This URL carries a token. Give the service URL without it and pass the token "
                                 "as token=, so it stays out of the map document and saved pages.")
            # the viewer writes its own queries: the service is the URL without one
            return "esri", "{}://{}{}".format(u.scheme, u.netloc, path)
        q = {k.lower(): [x.lower() for x in v] for k, v in parse_qs(u.query).items()}
        if path.lower().endswith((".geojson", ".json")) or "geojson" in q.get("f", []):
            return "geojson_url", s
    elif _ASSET.match(s):
        return "asset", s
    raise ValueError("Cannot tell what {!r} is. addLayer takes {}.".format(obj, ACCEPTED))


# ---- a reducer for ArcGIS image services: how overlapping rasters combine ----
_OPS = {"first": "MT_FIRST", "last": "MT_LAST", "min": "MT_MIN", "max": "MT_MAX", "mean": "MT_MEAN",
        "sum": "MT_SUM", "blend": "MT_BLEND"}
_EE_NAMES = {"firstnonnull": "first", "lastnonnull": "last", "minimum": "min", "maximum": "max"}


def _reducer_name(reducer):
    if isinstance(reducer, str):
        return reducer
    ser = getattr(reducer, "serialize", None)
    if ser is None:
        return str(reducer)
    m = re.search(r'"functionName"\s*:\s*"Reducer\.(\w+)"', ser())
    return m.group(1) if m else str(reducer)


def mosaic_rule(reducer):
    """An ArcGIS mosaicRule for a reducer name ("max") or an ee.Reducer
    (ee.Reducer.max()): overlapping rasters combined by that operation."""
    name = _reducer_name(reducer).strip().lower()
    name = _EE_NAMES.get(name, name)
    if name not in _OPS:
        raise ValueError("An ArcGIS image service combines rasters by first, last, min, max, mean, sum, blend; "
                         "got {!r}.".format(_reducer_name(reducer)))
    return {"mosaicMethod": "esriMosaicNone", "mosaicOperation": _OPS[name]}
