"""Esri JSON -> GeoJSON conversion, shared by every module that reads f=json.

Lived in ``edw.py`` alone until the ``services.py`` f=json fallback was found
using its own simpler copy, which handed Esri's flat ring list straight to one
GeoJSON Polygon (so a multipart polygon's other parts became holes in its
first part) and overwrote every feature id with its position. One
implementation now, so the two paths cannot drift apart again.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import json


def _ring_signed_area(ring: list) -> float:
    """Shoelace signed area of a ring. Positive = counter-clockwise."""
    total = 0.0
    count = len(ring)
    for i in range(count):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % count][0], ring[(i + 1) % count][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _esri_rings_to_geojson(rings: list) -> dict | None:
    """Split a flat Esri ring list into a GeoJSON Polygon or MultiPolygon.

    Esri packs both multipart polygons and interior holes into a single flat
    `rings` list, distinguished only by winding: clockwise (negative signed
    area) rings are exterior, counter-clockwise rings are holes belonging to
    the most recently seen exterior ring. GeoJSON instead nests holes inside
    their parent polygon and treats *every* ring after the first as a hole —
    so handing Esri's flat list straight to a GeoJSON Polygon silently turns
    the separate parts of e.g. Maui County into holes punched out of Maui.

    Output rings follow the RFC 7946 right-hand rule (exterior
    counter-clockwise, holes clockwise) — the opposite of Esri's convention,
    so each ring is reversed on the way out.
    """
    polygons: list[list[list]] = []
    for ring in rings:
        if not ring:
            continue
        # A degenerate (zero-area) ring can't be classified by winding; treat
        # it as exterior so its coordinates aren't silently absorbed as a
        # hole. A hole with no preceding exterior ring is likewise promoted
        # rather than dropped.
        if _ring_signed_area(ring) > 0 and polygons:
            polygons[-1].append(list(reversed(ring)))  # hole: CCW -> CW
        else:
            polygons.append([list(reversed(ring))])  # exterior: CW -> CCW

    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _esri_geometry_to_geojson(geometry: dict | None) -> dict | None:
    """Convert an Esri JSON geometry (x/y, points, paths, or rings) to GeoJSON."""
    if not geometry:
        return None
    if "x" in geometry and "y" in geometry:
        return {"type": "Point", "coordinates": [geometry["x"], geometry["y"]]}
    if "points" in geometry:
        return {"type": "MultiPoint", "coordinates": geometry["points"]}
    if "paths" in geometry:
        paths = geometry["paths"]
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}
    if "rings" in geometry:
        return _esri_rings_to_geojson(geometry["rings"])
    return None


def _esri_json_to_geojson(data: dict) -> dict:
    """Convert an Esri JSON (f=json) query response into a GeoJSON FeatureCollection."""
    features = [
        {
            "type": "Feature",
            "properties": feat.get("attributes", {}),
            "geometry": _esri_geometry_to_geojson(feat.get("geometry")),
        }
        for feat in data.get("features", [])
    ]
    return {"type": "FeatureCollection", "features": features}


# Object-ID attribute names to fall back on when a feature carries no "id".
# The f=json path (_esri_json_to_geojson) produces features without one,
# unlike f=geojson which sets it from the layer's object-id field.
_OBJECT_ID_KEYS = ("objectid", "OBJECTID", "fid", "FID", "oid", "OID")


def _sanitize_geojson(geojson: dict) -> dict:
    """Sanitize a GeoJSON FeatureCollection for downstream compatibility.

    - Removes properties with dots in the name (e.g. 'SHAPE.LEN') — many
      consumers (e.g. Earth Engine) reject dotted property keys
    - Gives each feature a string 'id' it doesn't already have, preferring
      its object-id attribute and falling back to the positional index.

    The id is only ever filled in, never overwritten. A positional index is
    not stable — it shifts with paging, filtering and result ordering, so the
    same feature would answer to a different id on every call — and clobbering
    a server-supplied OBJECTID with one would destroy the only durable handle
    the caller has on a feature.
    """
    for i, feat in enumerate(geojson.get("features", [])):
        # `or {}` — properties is legally null in GeoJSON, and iterating None
        # raises.
        props = feat.get("properties") or {}
        for key in [k for k in props if "." in k]:
            del props[key]

        feature_id = feat.get("id")
        if feature_id is None:
            feature_id = next(
                (props[k] for k in _OBJECT_ID_KEYS if props.get(k) is not None),
                None,
            )
        feat["id"] = str(feature_id) if feature_id is not None else str(i)
    return geojson


# ── GeoJSON -> Esri, for query geometry ────────────────────────────────


def _orient(ring: list, clockwise: bool) -> list:
    """``ring`` wound the requested way (reversed copy if it is not)."""
    is_cw = _ring_signed_area(ring) < 0
    return list(ring) if is_cw == clockwise else list(reversed(ring))


def _polygon_rings(polygon: list) -> list:
    """One GeoJSON polygon's rings, wound the way Esri reads them.

    RFC 7946 winds an exterior counter-clockwise and holes clockwise; Esri
    is the reverse -- clockwise rings are exteriors, counter-clockwise ones
    holes. Copied as-is, a GeoJSON polygon reached the server as a hole
    with no exterior, and a polygon WITH a hole sent the hole as a second
    exterior, so a spatial filter searched exactly the area meant to be
    excluded. Winding is set explicitly, so either input winding works.
    """
    return [_orient(r, clockwise=(i == 0)) for i, r in enumerate(polygon) if r]


def _geojson_to_esri_geometry(geometry: dict) -> dict | None:
    """A GeoJSON geometry as Esri JSON, or None if it is not one we convert."""
    t, c = geometry.get("type"), geometry.get("coordinates")
    if not c:
        return None
    if t == "Point":
        return {"x": c[0], "y": c[1]}
    if t == "MultiPoint":
        return {"points": [list(p) for p in c]}
    if t == "LineString":
        return {"paths": [c]}
    if t == "MultiLineString":
        return {"paths": list(c)}
    if t == "Polygon":
        return {"rings": _polygon_rings(c)}
    if t == "MultiPolygon":
        return {"rings": [r for poly in c for r in _polygon_rings(poly)]}
    return None


def _convert_query_geometry(geometry: dict | str) -> str:
    """Query ``geometry`` -> the Esri JSON string the REST API takes.

    A ``"xmin,ymin,xmax,ymax"`` string becomes an envelope; a GeoJSON
    geometry is converted (``_geojson_to_esri_geometry``); Esri JSON and
    any other string pass through.
    """
    if isinstance(geometry, str):
        parts = geometry.split(",")
        if len(parts) == 4:
            try:
                xmin, ymin, xmax, ymax = [float(p.strip()) for p in parts]
                return json.dumps({"xmin": xmin, "ymin": ymin,
                                   "xmax": xmax, "ymax": ymax})
            except ValueError:
                pass
        return geometry
    if isinstance(geometry, dict):
        if any(k in geometry for k in ("xmin", "rings", "paths", "points", "x")):
            return json.dumps(geometry)
        converted = _geojson_to_esri_geometry(geometry)
        return json.dumps(converted if converted is not None else geometry)
    return json.dumps(geometry)


def _geometry_type_for(geometry_json: str, requested: str) -> str:
    """The Esri ``geometryType`` that matches converted query geometry.

    Every query defaulted ``geometryType`` to ``esriGeometryEnvelope``
    whatever geometry it carried, so a GeoJSON polygon -- converted to
    Esri ``rings`` -- went out labelled an envelope and the server
    answered HTTP 400. The type now follows the geometry; an explicit
    non-default type from the caller is kept as given.
    """
    if requested and requested != "esriGeometryEnvelope":
        return requested
    try:
        g = json.loads(geometry_json)
    except (TypeError, ValueError):
        return requested
    if isinstance(g, dict):
        if "rings" in g:
            return "esriGeometryPolygon"
        if "paths" in g:
            return "esriGeometryPolyline"
        if "points" in g:
            return "esriGeometryMultipoint"
        if "x" in g and "y" in g:
            return "esriGeometryPoint"
    return requested
