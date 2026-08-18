"""
ArcGIS / Esri service query helpers: tile URL construction for Image/Map
Services, and GeoJSON feature fetching for Feature Services.

Ported from geeViz's esriLib.py, with the geeViz-viewer rendering calls
(gv.Map.addTileLayer / gv.Map.addLayer) removed — these functions return
data (a tile URL template, a GeoJSON dict) instead of rendering it anywhere.

Quick start::

    import services

    # Image/Map Service tile URL template
    tile_url = services.getImageServiceTileUrl("https://.../ImageServer")

    # Feature Service query (pre-flight count + fetch), like edw.query_features
    # but for arbitrary (non-EDW) ArcGIS Feature Services
    geojson = services.queryFeatureService(
        "https://services.arcgis.com/.../FeatureServer/0",
        where="STATE='UT'",
        max_features=2000,
    )

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
from typing import Any

from ._http import build_params, fetch_json
from .portal import _resolve_url

_FEATURE_QUERY_SUFFIX = "/query"
_MAX_RECORD_COUNT = 2000  # ArcGIS server default max


def getImageServiceTileUrl(url_or_result: str | dict, token: str | None = None) -> str:
    """Build the ``{z}/{y}/{x}`` tile URL template for an ArcGIS Image
    Service or cached Map Service.

    .. note::
        ArcGIS tile URLs use ``{z}/{y}/{x}`` order (y before x), not the
        XYZ standard ``{z}/{x}/{y}``.

    Args:
        url_or_result (str or dict): Service URL, or a
            :func:`portal.searchPortal` result dict.
        token (str, optional): ArcGIS token appended to tile requests as
            ``?token=<>``.

    Returns:
        str: Tile URL template, e.g.
        ``"https://.../ImageServer/tile/{z}/{y}/{x}"``.
    """
    url = _resolve_url(url_or_result)
    tile_url = f"{url}/tile/{{z}}/{{y}}/{{x}}"
    if token:
        tile_url = f"{tile_url}?token={urllib.parse.quote(token, safe='')}"
    return tile_url


def queryFeatureServiceCount(
    url_or_result: str | dict,
    where: str = "1=1",
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    in_sr: int = 4326,
    token: str | None = None,
) -> int:
    """Return the number of features matching *where* (and an optional
    spatial filter) on a Feature Service, Map Service, or mosaic layer.

    Args:
        url_or_result (str or dict): Layer URL (e.g. ``".../FeatureServer/0"``,
            ``".../MapServer/160"``), or a :func:`portal.searchPortal`
            result dict.
        where (str, optional): SQL WHERE clause. Defaults to ``"1=1"``.
        geometry: Spatial filter geometry. Can be a GeoJSON geometry dict, an
            Esri JSON geometry dict/string, a bbox string
            ``"xmin,ymin,xmax,ymax"``, or ``None`` for no spatial filter.
        geometry_type (str, optional): Esri geometry type, e.g.
            ``esriGeometryEnvelope``, ``esriGeometryPolygon``.
        spatial_rel (str, optional): Spatial relationship. Defaults to
            ``esriSpatialRelIntersects``.
        in_sr (int, optional): Spatial reference WKID of *geometry*.
            Defaults to 4326 (WGS84).
        token (str, optional): ArcGIS token for secured services.

    Raises:
        ConnectionError: If the service URL is unreachable.
        ValueError: If the service returns an error.
    """
    url = _resolve_url(url_or_result)
    if url.lower().endswith("featureserver"):
        url = f"{url}/0"

    params: dict[str, Any] = {"where": where, "returnCountOnly": "true", "f": "json"}
    if geometry is not None:
        params["geometry"] = _convert_geometry(geometry, geometry_type)
        params["geometryType"] = geometry_type
        params["spatialRel"] = spatial_rel
        params["inSR"] = str(in_sr)
    if token:
        params["token"] = token

    count_url = f"{url}{_FEATURE_QUERY_SUFFIX}"
    try:
        resp = fetch_json(count_url, params)
    except urllib.error.URLError as exc:
        raise ConnectionError(f"Could not reach Feature Service at {count_url!r}: {exc}") from exc

    if "error" in resp:
        err = resp["error"]
        raise ValueError(f"Feature Service returned an error: {err.get('code')} — {err.get('message', str(err))}")

    return resp.get("count", 0)


def queryFeatureService(
    url_or_result: str | dict,
    where: str = "1=1",
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    out_fields: str = "*",
    max_features: int = 1000,
    out_sr: int = 4326,
    token: str | None = None,
) -> dict[str, Any]:
    """Fetch features from an ArcGIS Feature Service, Map Service, or mosaic
    layer as GeoJSON, optionally filtered by spatial intersection.

    Performs a ``returnCountOnly=true`` pre-flight (honoring the same
    spatial filter) before fetching geometry. If the result count exceeds
    *max_features*, a :class:`ValueError` is raised with a remediation
    message instead of fetching a huge payload.

    Args:
        url_or_result (str or dict): Layer URL (e.g. ``".../FeatureServer/0"``,
            ``".../MapServer/160"`` for a mosaic layer), or a
            :func:`portal.searchPortal` result dict. If the URL points to
            the FeatureServer root rather than a specific layer, ``/0`` is
            appended automatically.
        where (str, optional): SQL WHERE clause sent to the service for
            server-side filtering. Defaults to ``"1=1"`` (all features).
        geometry: Spatial filter geometry. Can be a GeoJSON geometry dict, an
            Esri JSON geometry dict/string, a bbox string
            ``"xmin,ymin,xmax,ymax"``, or ``None`` for no spatial filter.
        geometry_type (str, optional): Esri geometry type, e.g.
            ``esriGeometryEnvelope``, ``esriGeometryPolygon``.
        spatial_rel (str, optional): Spatial relationship. Defaults to
            ``esriSpatialRelIntersects``.
        out_fields (str, optional): Comma-separated field names or ``"*"``.
        max_features (int, optional): Hard cap on feature count. Defaults
            to 1000.
        out_sr (int, optional): Output spatial reference WKID. Also used as
            the input spatial reference for *geometry*. Defaults to 4326
            (WGS84).
        token (str, optional): ArcGIS token for secured services.

    Returns:
        dict: GeoJSON FeatureCollection.

    Raises:
        ValueError: If the feature count exceeds *max_features*, or the
            service returns an error.
        ConnectionError: If the service URL is unreachable.
    """
    url = _resolve_url(url_or_result)
    if url.lower().endswith("featureserver"):
        url = f"{url}/0"

    feature_count = queryFeatureServiceCount(
        url,
        where=where,
        geometry=geometry,
        geometry_type=geometry_type,
        spatial_rel=spatial_rel,
        in_sr=out_sr,
        token=token,
    )
    if feature_count > max_features:
        raise ValueError(
            f"Service layer has {feature_count:,} matching features "
            f"(max_features={max_features:,}).\n"
            f"Increase max_features OR pass a `where` clause to filter, "
            f"e.g. where=\"STATE_FIPS='06'\"."
        )

    query_params: dict[str, Any] = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "geojson",
    }
    if geometry is not None:
        query_params["geometry"] = _convert_geometry(geometry, geometry_type)
        query_params["geometryType"] = geometry_type
        query_params["spatialRel"] = spatial_rel
        query_params["inSR"] = str(out_sr)
    if token:
        query_params["token"] = token

    query_url = f"{url}{_FEATURE_QUERY_SUFFIX}"
    try:
        geojson = fetch_json(query_url, query_params)
    except urllib.error.URLError as exc:
        raise ConnectionError(f"Could not fetch features from {query_url!r}: {exc}") from exc

    if "error" in geojson:
        err = geojson["error"]
        raise ValueError(f"Feature Service query returned an error: {err.get('code')} — {err.get('message', str(err))}")

    return geojson


def _convert_geometry(geometry: dict | str, geometry_type: str) -> str:
    """Convert geometry input to an Esri-compatible JSON string for query params.

    Handles:
    - Bbox string "xmin,ymin,xmax,ymax" → Esri envelope JSON
    - GeoJSON geometry dict → Esri JSON
    - Already-Esri JSON dict → pass through
    - String → pass through
    """
    if isinstance(geometry, str):
        parts = geometry.split(",")
        if len(parts) == 4:
            try:
                xmin, ymin, xmax, ymax = [float(p.strip()) for p in parts]
                return json.dumps({"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})
            except ValueError:
                pass
        return geometry

    if isinstance(geometry, dict):
        if "xmin" in geometry:
            return json.dumps(geometry)
        if "rings" in geometry or "x" in geometry or "points" in geometry:
            return json.dumps(geometry)

        geom_type = geometry.get("type", "")
        coords = geometry.get("coordinates")

        if geom_type == "Point" and coords:
            return json.dumps({"x": coords[0], "y": coords[1]})
        if geom_type == "Polygon" and coords:
            return json.dumps({"rings": coords})
        if geom_type == "MultiPolygon" and coords:
            rings = []
            for polygon in coords:
                rings.extend(polygon)
            return json.dumps({"rings": rings})

        return json.dumps(geometry)

    return json.dumps(geometry)


def getLayerInfo(url_or_result: str | dict, token: str | None = None) -> dict[str, Any]:
    """Get detailed metadata for a single service layer: fields, geometry
    type, and capabilities.

    Works for any ArcGIS layer URL — a FeatureServer/MapServer sub-layer,
    or an Image Service mosaic layer (e.g. ``.../MapServer/160``).

    Args:
        url_or_result (str or dict): Layer URL (e.g. ``".../MapServer/160"``,
            ``".../FeatureServer/0"``), or a :func:`portal.searchPortal`
            result dict.
        token (str, optional): ArcGIS token for secured services.

    Returns:
        dict: Keys: ``name``, ``geometryType``, ``description``, ``fields``
        (list of ``{name, type, alias}``), ``extent``, ``maxRecordCount``,
        ``capabilities`` (list of supported operations, e.g.
        ``["Map", "Query"]``), ``advancedQueryCapabilities``,
        ``supportedQueryFormats``.

    Raises:
        ConnectionError: If the URL is unreachable.
        ValueError: If the service returns an error.
    """
    url = _resolve_url(url_or_result)
    params = build_params({"f": "pjson"}, token)
    try:
        data = fetch_json(url, params)
    except urllib.error.URLError as exc:
        raise ConnectionError(f"Could not reach layer at {url!r}: {exc}") from exc

    if "error" in data:
        err = data["error"]
        raise ValueError(f"Layer returned an error: {err.get('code')} — {err.get('message', str(err))}")

    sub_layers = data.get("subLayers") or []
    if sub_layers:
        options = ", ".join(f"{s.get('name')} ({s.get('id')})" for s in sub_layers)
        raise ValueError(
            f"{url} is a {data.get('type', 'container layer')} ({data.get('name', '')!r}) — "
            f"it has no geometry of its own and is not directly queryable. "
            f"Point at one of its sub-layers instead: {options}."
        )

    fields = [
        {
            "name": f.get("name"),
            "type": f.get("type", "").replace("esriFieldType", ""),
            "alias": f.get("alias", ""),
        }
        for f in (data.get("fields") or [])
    ]
    capabilities = [c.strip() for c in data.get("capabilities", "").split(",") if c.strip()]

    return {
        "name": data.get("name", ""),
        "geometryType": data.get("geometryType", ""),
        "description": data.get("description", ""),
        "fields": fields,
        "extent": data.get("extent", {}),
        "maxRecordCount": data.get("maxRecordCount", _MAX_RECORD_COUNT),
        "capabilities": capabilities,
        "advancedQueryCapabilities": data.get("advancedQueryCapabilities", {}),
        "supportedQueryFormats": data.get("supportedQueryFormats", ""),
    }
