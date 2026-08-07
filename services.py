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

import urllib.error
import urllib.parse
from typing import Any

from ._http import fetch_json
from .portal import _resolve_url

_FEATURE_QUERY_SUFFIX = "/query"


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
    token: str | None = None,
) -> int:
    """Return the number of features matching *where* on a Feature Service layer.

    Args:
        url_or_result (str or dict): Feature Service layer URL
            (e.g. ``".../FeatureServer/0"``), or a
            :func:`portal.searchPortal` result dict.
        where (str, optional): SQL WHERE clause. Defaults to ``"1=1"``.
        token (str, optional): ArcGIS token for secured services.

    Raises:
        ConnectionError: If the service URL is unreachable.
        ValueError: If the service returns an error.
    """
    url = _resolve_url(url_or_result)
    if url.lower().endswith("featureserver"):
        url = f"{url}/0"

    params: dict[str, Any] = {"where": where, "returnCountOnly": "true", "f": "json"}
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
    out_fields: str = "*",
    max_features: int = 1000,
    out_sr: int = 4326,
    token: str | None = None,
) -> dict[str, Any]:
    """Fetch features from an ArcGIS Feature Service layer as GeoJSON.

    Performs a ``returnCountOnly=true`` pre-flight before fetching geometry.
    If the result count exceeds *max_features*, a :class:`ValueError` is
    raised with a remediation message instead of fetching a huge payload.

    Args:
        url_or_result (str or dict): Feature Service or sub-layer URL
            (e.g. ``".../FeatureServer/0"``), or a
            :func:`portal.searchPortal` result dict. If the URL points to
            the FeatureServer root rather than a specific layer, ``/0`` is
            appended automatically.
        where (str, optional): SQL WHERE clause sent to the service for
            server-side filtering. Defaults to ``"1=1"`` (all features).
        out_fields (str, optional): Comma-separated field names or ``"*"``.
        max_features (int, optional): Hard cap on feature count. Defaults
            to 1000.
        out_sr (int, optional): Output spatial reference WKID. Defaults to
            4326 (WGS84).
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

    feature_count = queryFeatureServiceCount(url, where=where, token=token)
    if feature_count > max_features:
        raise ValueError(
            f"Feature service has {feature_count:,} features "
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
