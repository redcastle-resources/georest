"""
ArcGIS / Esri service query helpers: tile URL construction for Image/Map
Services, and GeoJSON feature fetching for Feature Services.

Ported from geeViz's esriLib.py, with the geeViz-viewer rendering calls
(gv.Map.addTileLayer / gv.Map.addLayer) removed - these functions return
data (a tile URL template, a GeoJSON dict) instead of rendering it anywhere.

Quick start::

    from georest.esri import services

    # Image/Map Service tile URL template
    tile_url = services.getImageServiceTileUrl("https://.../ImageServer")

    # Feature Service query (pre-flight count + fetch)
    geojson = services.queryFeatureService(
        "https://services.arcgis.com/.../FeatureServer/0",
        where="STATE='UT'",
        max_features=2000,
    )

    # More rows than the server hands out at once: fetched a page at a time
    geojson = services.queryFeatureService(layer_url, max_features=20000, if_more="page")

    # Server-side window functions: the largest feature per group
    top = services.topNPerGroup(layer_url, field="acres", group_by="state")

``georest.usfs.edw`` is built on these: its queries are these functions
pointed at a Forest Service Enterprise Data Warehouse layer.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any

from ._geojson import (  # one conversion for every f=json path, so none can drift
    _convert_query_geometry,
    _esri_geometry_to_geojson,  # noqa: F401 - reached as services.<name>
    _esri_json_to_geojson,
    _geometry_type_for,
    _sanitize_geojson,
)
from ._http import (
    EsriServiceError,  # noqa: F401 - raised here; reached as services.EsriServiceError
    build_params,
    esri_error,
    fetch_bytes,
    fetch_json,
    fetch_text,
    format_esri_error,
    post_json,
)
from .portal import _resolve_url

_FEATURE_QUERY_SUFFIX = "/query"
_MAX_RECORD_COUNT = 2000  # ArcGIS server default max

#: What queryFeatureService does when more features match than max_features.
IF_MORE = ("raise", "truncate", "page")


#: Backwards-compatible alias. The implementation moved to `_http` so
#: `portal.py` can share it - `services` imports from `portal`, so importing
#: the other way around would be a circular import.
_format_esri_error = format_esri_error


def getImageServiceTileUrl(url_or_result: str | dict, token: str | None = None) -> str:
    """Build the ``{z}/{y}/{x}`` tile URL template for an ArcGIS Image
    Service or cached Map Service.

    .. note::
        ArcGIS tile URLs use ``{z}/{y}/{x}`` order (y before x), not the
        XYZ standard ``{z}/{x}/{y}``.

    .. warning::
        This is pure string construction - no request is made, and nothing
        is validated. In particular it does **not** check that the service
        is actually tiled, and it cannot: a service with no tile cache
        yields a well-formed template whose tiles all answer HTTP 404
        (verified against a live, uncached Image Service). Only *cached*
        services serve ``/tile``. Confirm with ``?f=json`` first - a tiled
        service reports ``tileInfo`` (and a Map Service also reports
        ``singleFusedMapCache: true``); an uncached Image Service reports
        neither and must be rendered through :func:`exportImage` instead.

    Args:
        url_or_result (str or dict): Service URL, or a
            :func:`portal.searchPortal` result dict.
        token (str, optional): ArcGIS token appended to tile requests as
            ``?token=<>``.

    Returns:
        str: Tile URL template, e.g.
        ``"https://.../ImageServer/tile/{z}/{y}/{x}"``.

    Raises:
        TypeError: If *url_or_result* is neither a string nor a dict.
        ValueError: If a result dict carries no ``url`` key.
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
    timeout: int | None = None,
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
        timeout (int, optional): Request timeout in seconds. Defaults to
            60 - some services with large mosaic catalogs can genuinely
            take longer than that to respond (verified against a live
            service); pass a larger value rather than assuming a hang.

    Returns:
        int: The number of matching features. ``0`` if the service answers
        without a ``count`` key at all.

    Raises:
        RuntimeError: If the service is unreachable or answers with an HTTP
            error status. Note this is the transport-level failure mode for
            *every* function in this module - ``_http`` converts both
            :class:`urllib.error.HTTPError` and :class:`urllib.error.URLError`
            into :class:`RuntimeError`, so a dead host and a 500 surface the
            same way.
        ValueError: If the service answers 200 with an Esri error object in
            the body (e.g. an invalid *where* clause), or with a body that
            isn't JSON at all (what a degraded HTML error page looks like).

    Example::

        from georest.esri import services

        quakes = "https://sampleserver6.arcgisonline.com/arcgis/rest/services/Earthquakes_Since1970/FeatureServer/0"
        services.queryFeatureServiceCount(quakes, where="magnitude >= 8")   # how many before fetching
    """
    url = _resolve_url(url_or_result)
    if url.lower().endswith("featureserver"):
        url = f"{url}/0"

    params: dict[str, Any] = {"where": where, "returnCountOnly": "true", "f": "json"}
    if geometry is not None:
        params["geometry"] = _convert_geometry(geometry, geometry_type)
        params["geometryType"] = _geometry_type_for(params["geometry"], geometry_type)
        params["spatialRel"] = spatial_rel
        params["inSR"] = str(in_sr)
    if token is not None:
        params["token"] = token

    count_url = f"{url}{_FEATURE_QUERY_SUFFIX}"
    try:
        # A polygon can be too long for a URL, so a spatial count is POSTed.
        if geometry is not None:
            resp = post_json(count_url, params, timeout=timeout)
        else:
            resp = fetch_json(count_url, params, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not reach Feature Service at {count_url!r}: {exc}") from exc

    if "error" in resp:
        raise esri_error("Feature Service returned an error", resp['error'])

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
    timeout: int | None = None,
    in_sr: int | None = None,
    max_allowable_offset: float | None = None,
    if_more: str = "raise",
) -> dict[str, Any]:
    """Fetch features from an ArcGIS Feature Service, Map Service, or mosaic
    layer as GeoJSON, optionally filtered by spatial intersection.

    *if_more* decides what happens when more features match than
    *max_features*:

    * ``"raise"`` (default): a ``returnCountOnly=true`` pre-flight (honoring
      the same spatial filter) runs first, and a :class:`ValueError` with a
      remediation message is raised instead of fetching a huge payload.
    * ``"truncate"``: one request for up to *max_features* features (the
      server may cap it lower, at the layer's ``maxRecordCount``); the rest
      are left behind without an error.
    * ``"page"``: as many requests as it takes, a page of the layer's
      ``maxRecordCount`` at a time, until *max_features* features are in
      hand or none are left.

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
            Include the layer's object-id field if you need stable feature
            ids: some servers omit the GeoJSON ``id`` when it isn't asked for.
        max_features (int, optional): Hard cap on feature count. Defaults
            to 1000.
        out_sr (int, optional): Output spatial reference WKID. Defaults to
            4326 (WGS84).
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds. Defaults to
            60 - some services with large mosaic catalogs can genuinely
            take longer than that to respond; pass a larger value rather
            than assuming a hang.
        in_sr (int, optional): Spatial reference WKID *geometry* is in.
            Defaults to *out_sr* with ``if_more="raise"`` (as this function
            always read it), and to 4326, GeoJSON's own, otherwise.
        max_allowable_offset (float, optional): Simplify each returned
            geometry on the server so no vertex moves more than this, in the
            units of *out_sr* (degrees for 4326; 0.001 is about 100 m). A
            national forest boundary drops from megabytes to kilobytes.
            ``None`` (the default) returns full detail.
        if_more (str, optional): ``"raise"``, ``"truncate"`` or ``"page"``;
            see above.

    Returns:
        dict: GeoJSON ``FeatureCollection``.

        .. note::
            Feature ``id`` values differ depending on which of the two
            response paths served the request - verified against live
            services. On the normal ``f=geojson`` path the ids are whatever
            the server emitted (typically the layer's integer OBJECTIDs,
            e.g. ``20``), and property names are passed through untouched.
            On the ``f=json`` fallback path (see below) ids are filled in
            client-side from the feature's object-id attribute (``OBJECTID``,
            ``FID`` …), falling back to its position only when it has none,
            and properties whose names contain a dot (e.g. ``SHAPE.LEN``) are
            dropped, since many consumers reject dotted keys. Polygons on
            that path are split by ring winding, as on ``f=geojson``: a
            multipart polygon comes back as a ``MultiPolygon`` and holes stay
            holes.

    Note:
        Some servers answer a spatial query against a geometrically complex
        area (many disjoint parts and holes, e.g. a national forest
        boundary) with zero features and ``exceededTransferLimit``, though a
        count of the same query is right. With ``if_more="truncate"`` this
        is detected and worked around: the matching object ids are fetched
        (which is not subject to the limitation) and then the features by
        id, with a plain attribute filter.

    Raises:
        ValueError: If the feature count exceeds *max_features* with
            ``if_more="raise"``, or *if_more* is not one of :data:`IF_MORE`.
        EsriServiceError: If the service answers 200 with an Esri error
            object in the body. It is a ``ValueError`` and a
            ``RuntimeError``.
        RuntimeError: If the service is unreachable or answers with an HTTP
            error status (see :func:`queryFeatureServiceCount`), or, with
            ``if_more="truncate"``, if the server drops features while they
            are fetched by id.

    Example::

        import georest
        from georest.esri import services

        quakes = "https://sampleserver6.arcgisonline.com/arcgis/rest/services/Earthquakes_Since1970/FeatureServer/0"
        big = services.queryFeatureService(quakes, where="magnitude >= 8")
        every = services.queryFeatureService(quakes, where="magnitude >= 6",
                                             max_features=5000, if_more="page")
        georest.to_frame(every)
    """
    if if_more not in IF_MORE:
        raise ValueError(f"if_more must be one of {', '.join(IF_MORE)}; got {if_more!r}")
    url = _layer_url(url_or_result)
    if if_more == "truncate":
        return _query_truncated(url, where, geometry, geometry_type, spatial_rel, out_fields,
                                max_features, out_sr, 4326 if in_sr is None else in_sr,
                                max_allowable_offset, token, timeout)
    if if_more == "page":
        return _query_paged(url, where, geometry, geometry_type, spatial_rel, out_fields,
                            max_features, out_sr, 4326 if in_sr is None else in_sr,
                            max_allowable_offset, token, timeout)
    in_sr = out_sr if in_sr is None else in_sr

    feature_count = queryFeatureServiceCount(
        url,
        where=where,
        geometry=geometry,
        geometry_type=geometry_type,
        spatial_rel=spatial_rel,
        in_sr=in_sr,
        token=token,
        timeout=timeout,
    )
    if feature_count > max_features:
        raise ValueError(
            f"Service layer has {feature_count:,} matching features "
            f"(max_features={max_features:,}).\n"
            f"Increase max_features OR pass a `where` clause to filter, "
            f"e.g. where=\"STATE_FIPS='06'\", or pass if_more=\"page\" to "
            f"fetch them all a page at a time."
        )

    query_params: dict[str, Any] = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "geojson",
    }
    if max_allowable_offset is not None:
        query_params["maxAllowableOffset"] = str(float(max_allowable_offset))
    if geometry is not None:
        query_params["geometry"] = _convert_geometry(geometry, geometry_type)
        query_params["geometryType"] = _geometry_type_for(query_params["geometry"], geometry_type)
        query_params["spatialRel"] = spatial_rel
        query_params["inSR"] = str(in_sr)
    if token is not None:
        query_params["token"] = token

    query_url = f"{url}{_FEATURE_QUERY_SUFFIX}"
    try:
        geojson = fetch_json(query_url, query_params, timeout=timeout)
    except RuntimeError:
        # Some services - notably an ImageServer's native /query, which
        # exposes its raster catalog table - reject f=geojson outright
        # (verified against a live service: HTTP 400, "Output format not
        # supported"). By this point the count pre-flight above has already
        # proven `where`/`geometry` are valid using f=json, so fall back to
        # f=json here too and convert to GeoJSON client-side instead of
        # surfacing this as a hard failure.
        #
        # A genuine transport failure (dead host, 500) also lands here,
        # since _http collapses every HTTP/URL failure into RuntimeError and
        # there's no way to tell "this format is unsupported" apart from
        # "this server is down" without parsing error strings. That costs
        # one extra request on an already-failing call, and the retry's own
        # RuntimeError is re-raised below with the URL attached.
        fallback_params = {**query_params, "f": "json"}
        try:
            esri_json = fetch_json(query_url, fallback_params, timeout=timeout)
        except RuntimeError as exc:
            raise RuntimeError(f"Could not fetch features from {query_url!r}: {exc}") from exc
        if "error" in esri_json:
            raise esri_error("Feature Service query returned an error", esri_json["error"])
        return _sanitize_geojson(_esri_json_to_geojson(esri_json))

    if "error" in geojson:
        raise esri_error("Feature Service query returned an error", geojson["error"])

    return geojson


def _layer_url(url_or_result: str | dict) -> str:
    """The layer URL to query: a FeatureServer root means its layer 0."""
    url = _resolve_url(url_or_result).rstrip("/")
    if url.lower().endswith("featureserver"):
        url = f"{url}/0"
    return url


def _spatial_params(params: dict[str, str], geometry, geometry_type: str,
                    spatial_rel: str, in_sr: int) -> None:
    """Add the spatial filter to query *params*, if there is a geometry."""
    if geometry is None:
        return
    geom_str = _convert_geometry(geometry, geometry_type)
    params["geometry"] = geom_str
    params["geometryType"] = _geometry_type_for(geom_str, geometry_type)
    params["spatialRel"] = spatial_rel
    params["inSR"] = str(in_sr)


def _post_query(url: str, params: dict[str, str], token: str | None, timeout: int | None,
                what: str = "Feature Service query returned an error") -> dict:
    """POST a query (polygons can be too long for a URL); raise on an Esri error."""
    data = post_json(url, build_params(params, token), timeout=timeout)
    if "error" in data:
        raise esri_error(what, data["error"])
    return data


_MAX_RECORD_COUNT_CACHE: dict[str, int] = {}


def _max_record_count(url: str, token: str | None = None, timeout: int | None = None) -> int:
    """The layer's own maxRecordCount, fetched once per layer and cached.

    The server silently truncates every response to this many records, so
    it - not the 2000 fallback - is the only safe page/chunk stride. Striding
    wider than the server will actually return makes the caller skip past
    records it never received: page 2 starts at offset `stride` while the
    server only handed back `maxRecordCount` rows, and the gap is dropped
    silently, with no error and no flag.
    """
    if url not in _MAX_RECORD_COUNT_CACHE:
        try:
            count = int(getLayerInfo(url, token=token, timeout=timeout)["maxRecordCount"])
        except (RuntimeError, ValueError, KeyError, TypeError):
            # A metadata fetch failure shouldn't sink an otherwise fine query;
            # callers pair this with an exceededTransferLimit / short-chunk
            # check so a wrong guess here still can't silently lose records.
            count = 0
        _MAX_RECORD_COUNT_CACHE[url] = count if count > 0 else _MAX_RECORD_COUNT
    return _MAX_RECORD_COUNT_CACHE[url]


def _query_truncated(url, where, geometry, geometry_type, spatial_rel, out_fields,
                     max_features, out_sr, in_sr, max_allowable_offset, token, timeout) -> dict:
    """One request for up to *max_features* (``if_more="truncate"``)."""
    params: dict[str, str] = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "geojson",
        # Sent unclamped: the server caps this at the layer's own
        # maxRecordCount anyway (verified - asking 100000 of a 2000-record
        # layer returns 2000 + exceededTransferLimit, not an error). Clamping
        # to a hardcoded 2000 here only under-fetched layers allowing more.
        "resultRecordCount": str(max_features),
    }
    if max_allowable_offset is not None:
        params["maxAllowableOffset"] = str(float(max_allowable_offset))
    _spatial_params(params, geometry, geometry_type, spatial_rel, in_sr)
    data = _post_query(f"{url}{_FEATURE_QUERY_SUFFIX}", params, token, timeout)

    if geometry is not None and data.get("exceededTransferLimit") and not data.get("features"):
        id_field, ids = _query_object_ids(url, where, geometry, geometry_type, spatial_rel,
                                          in_sr, token, timeout)
        if ids:
            return _fetch_features_by_ids(url, id_field, ids, out_fields, out_sr, max_features,
                                          max_allowable_offset=max_allowable_offset,
                                          token=token, timeout=timeout)
    return _sanitize_geojson(data)


def _query_object_ids(url, where, geometry, geometry_type, spatial_rel, in_sr,
                      token=None, timeout=None) -> tuple[str, list[int]]:
    """Fetch just the object IDs matching a spatial + attribute filter.

    returnIdsOnly is cheap enough that it doesn't hit the exceededTransferLimit
    empty-features bug that a full feature fetch can hit against a
    geometrically complex AOI (verified against the live EDW server: a
    161-part multipolygon returns 0 features via a normal query, but the
    correct 40 object IDs via returnIdsOnly).
    """
    params: dict[str, str] = {"where": where, "f": "json", "returnIdsOnly": "true"}
    _spatial_params(params, geometry, geometry_type, spatial_rel, in_sr)
    data = _post_query(f"{url}{_FEATURE_QUERY_SUFFIX}", params, token, timeout)
    return data.get("objectIdFieldName", "OBJECTID"), data.get("objectIds") or []


def _fetch_features_by_ids(url, id_field, ids, out_fields, out_sr, max_features,
                           chunk_size=None, max_allowable_offset=None,
                           token=None, timeout=None) -> dict:
    """Fetch features for a known list of object IDs, in chunks.

    A plain attribute IN (...) filter carries no geometry payload, so it
    sidesteps the complex-geometry query limitation entirely - used to
    retrieve attributes/geometry for IDs already resolved by
    _query_object_ids.

    chunk_size defaults to the layer's own maxRecordCount. It must not exceed
    it: asking for more IDs than the server will return in one response
    silently drops the overflow, since each chunk is fetched once and never
    revisited.
    """
    ids = ids[:max_features]
    if chunk_size is None:
        chunk_size = _max_record_count(url, token, timeout)
    all_features: list[dict] = []
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start : start + chunk_size]
        id_list = ",".join(str(oid) for oid in chunk)
        result = _query_truncated(url, f"{id_field} IN ({id_list})", None, "esriGeometryEnvelope",
                                  "esriSpatialRelIntersects", out_fields, chunk_size, out_sr, 4326,
                                  max_allowable_offset, token, timeout)
        features = result.get("features", [])
        # Every ID came from this same layer via returnIdsOnly and the filter
        # is a bare IN (...), so a short chunk means records were dropped
        # rather than legitimately filtered out. Fail loudly - silently
        # returning a subset would corrupt any downstream analysis.
        if len(features) < len(chunk):
            raise RuntimeError(
                f"The server returned {len(features)} features for a chunk of "
                f"{len(chunk)} object IDs on {url} (chunk_size={chunk_size}). "
                f"Records were dropped; the layer's maxRecordCount may be "
                f"below the chunk size."
            )
        all_features.extend(features)
    return _sanitize_geojson({"type": "FeatureCollection", "features": all_features})


def _query_paged(url, where, geometry, geometry_type, spatial_rel, out_fields,
                 max_features, out_sr, in_sr, max_allowable_offset, token, timeout) -> dict:
    """Page through results up to *max_features* (``if_more="page"``).

    Pages are taken by resultOffset without an explicit sort. ArcGIS doesn't
    guarantee a stable row order across requests absent orderByFields, so a
    layer that reorders between pages could in principle duplicate or skip
    rows.
    """
    all_features: list[dict] = []
    offset = 0
    # Page at the layer's real limit, not a hardcoded guess: a stride wider
    # than the server will return makes the next offset skip the records it
    # withheld.
    server_max = _max_record_count(url, token, timeout)

    while len(all_features) < max_features:
        page_size = min(max_features - len(all_features), server_max)
        params: dict[str, str] = {
            "where": where,
            "outFields": out_fields,
            "outSR": str(out_sr),
            "f": "geojson",
            "resultRecordCount": str(page_size),
            "resultOffset": str(offset),
        }
        if max_allowable_offset is not None:
            params["maxAllowableOffset"] = str(float(max_allowable_offset))
        _spatial_params(params, geometry, geometry_type, spatial_rel, in_sr)
        data = _post_query(f"{url}{_FEATURE_QUERY_SUFFIX}", params, token, timeout)

        features = data.get("features", [])
        if not features:
            break
        all_features.extend(features)
        offset += len(features)

        # A short page means end-of-data only if the server isn't also
        # flagging more rows behind it - belt and braces in case server_max
        # fell back to a guess after a failed metadata fetch.
        if len(features) < page_size and not data.get("exceededTransferLimit"):
            break

    return _sanitize_geojson({"type": "FeatureCollection", "features": all_features[:max_features]})


# Ranking/numbering analytics are computed over the window itself rather than
# over an input field, and take no onAnalyticField. Any `field` supplied for
# these is dropped rather than forwarded (see _build_out_analytics).
_FIELDLESS_ANALYTICS = frozenset(
    {"RANK", "DENSE_RANK", "ROW_NUMBER", "NTILE", "PERCENT_RANK", "CUME_DIST"}
)


def _build_out_analytics(out_analytics: list[dict[str, Any]]) -> str:
    """Translate simplified analytic defs into Esri's outAnalytics JSON.

    Each input dict has keys: type, field (optional for COUNT/ROW_NUMBER),
    out_name, and optionally order_by (for LAG/LEAD/ranking functions) and
    params (dict merged into analyticParameters, e.g. {"offset": 1} for
    LAG/LEAD or {"buckets": 4} for NTILE).

    Note the key names: queryAnalytic expects onAnalyticField /
    outAnalyticFieldName. The similar-looking onStatisticField /
    outStatisticFieldName belong to the separate outStatistics and
    queryTopFeatures operations; sending those here makes the server reject
    any analytic that takes an input field ("Unable to complete operation")
    and silently ignore the requested output name for those that don't.
    """
    analytics = []
    for a in out_analytics:
        analytic_type = a["type"]
        entry: dict[str, Any] = {
            "analyticType": analytic_type,
            "outAnalyticFieldName": a["out_name"],
        }
        if "field" in a and str(analytic_type).upper() not in _FIELDLESS_ANALYTICS:
            entry["onAnalyticField"] = a["field"]

        analytic_params: dict[str, Any] = dict(a.get("params", {}))
        if "order_by" in a:
            analytic_params["orderBy"] = a["order_by"]
        if analytic_params:
            entry["analyticParameters"] = analytic_params

        analytics.append(entry)
    return json.dumps(analytics)


def queryAnalytic(
    url_or_result: str | dict,
    out_analytics: list[dict[str, Any]],
    partition_by: str | list[str] | None = None,
    order_by_fields: str | None = None,
    where: str = "1=1",
    analytic_where: str | None = None,
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    out_fields: str = "*",
    out_sr: int = 4326,
    return_geometry: bool = True,
    in_sr: int = 4326,
    token: str | None = None,
    timeout: int | None = None,
) -> dict:
    """Run SQL window-function analytics (rank, running total, LAG/LEAD, ...)
    on a layer, with the ArcGIS ``queryAnalytic`` operation.

    Rows are not collapsed: each analytic value is appended as a new field on
    its source feature. Use *analytic_where* to keep only some computed rows
    (e.g. ``"rank_val = 1"`` for the top-ranked feature per partition). The
    layer must support it: ``getLayerInfo(url)["advancedQueryCapabilities"]``
    reports ``supportsQueryAnalytic``.

    Args:
        url_or_result (str or dict): Layer URL, or a
            :func:`portal.searchPortal` result dict.
        out_analytics: List of analytic definitions, each a dict with:

            - type (str): Esri analytic type, e.g. "RANK", "SUM", "LAG",
              "ROW_NUMBER", "PERCENTILE_CONT".
            - field (str, optional): Input field the analytic operates on
              (omit for COUNT/ROW_NUMBER and the ranking types).
            - out_name (str): Output field name for the computed value.
            - order_by (str, optional): Field ordering within each
              partition, e.g. "ACRES DESC" (required by ranking and
              LAG/LEAD functions).
            - params (dict, optional): Extra analyticParameters, e.g.
              {"offset": 1} for LAG/LEAD, {"buckets": 4} for NTILE.
        partition_by: Field name(s) to group rows into separate windows.
            Omit to treat the whole result set as one partition.
        order_by_fields: Sort order applied before analytics are computed
            (separate from an analytic's own ``order_by``).
        where: SQL WHERE clause filtering the source rows.
        analytic_where: SQL filter applied *after* analytics are computed,
            evaluated against output field names (e.g. "acres_rank = 1").
        geometry, geometry_type, spatial_rel: Spatial filter, as for
            :func:`queryFeatureService`.
        out_fields: Comma-separated field names or "*" for all.
        out_sr: Output spatial reference WKID. Default: 4326 (WGS84).
        return_geometry: If False, omit geometry from results (cheaper when
            only the computed values are needed).
        in_sr: Spatial reference WKID of *geometry*. Default: 4326 (WGS84).
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds.

    Returns:
        GeoJSON FeatureCollection dict with the analytic fields appended to
        each feature's properties.

    Raises:
        EsriServiceError: If the service answers with an Esri error object.
    """
    url = _layer_url(url_or_result)
    if partition_by:
        # queryAnalytic has no top-level partitionBy request parameter - the
        # partition must be nested in each analytic's own analyticParameters,
        # or the server silently computes the analytic over the whole result
        # set instead of per partition (verified against the live EDW server).
        partition_str = ",".join(partition_by) if isinstance(partition_by, list) else partition_by
        out_analytics = [
            {**a, "params": {**a.get("params", {}), "partitionBy": partition_str}}
            for a in out_analytics
        ]

    params: dict[str, str] = {
        "where": where,
        "outFields": out_fields,
        "outSR": str(out_sr),
        "f": "json",  # queryAnalytic does not support f=geojson (returns 400)
        "returnGeometry": "true" if return_geometry else "false",
        "outAnalytics": _build_out_analytics(out_analytics),
    }
    if order_by_fields:
        params["orderByFields"] = order_by_fields
    if analytic_where:
        params["analyticWhere"] = analytic_where
    _spatial_params(params, geometry, geometry_type, spatial_rel, in_sr)

    data = _post_query(f"{url}/queryAnalytic", params, token, timeout,
                       what="queryAnalytic returned an error")
    return _sanitize_geojson(_esri_json_to_geojson(data))


def topNPerGroup(
    url_or_result: str | dict,
    field: str,
    group_by: str | list[str],
    n: int = 1,
    descending: bool = True,
    where: str = "1=1",
    geometry: dict | str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    spatial_rel: str = "esriSpatialRelIntersects",
    out_fields: str = "*",
    out_sr: int = 4326,
    in_sr: int = 4326,
    token: str | None = None,
    timeout: int | None = None,
) -> dict:
    """The top *n* features by *field* within each *group_by* group.

    :func:`queryAnalytic` with RANK() and an *analytic_where*, e.g. the
    largest timber harvest per ranger district.

    Args:
        url_or_result (str or dict): Layer URL, or a
            :func:`portal.searchPortal` result dict.
        field: Field to rank by, e.g. "gis_acres".
        group_by: Field name(s) defining each group.
        n: How many top features to keep per group. Default: 1.
        descending: If True (default), rank largest-first.
        where: SQL WHERE clause filtering the source rows.
        geometry, geometry_type, spatial_rel: Spatial filter, as for
            :func:`queryFeatureService`.
        out_fields, out_sr, in_sr, token, timeout: As for
            :func:`queryAnalytic`.

    Returns:
        GeoJSON FeatureCollection dict with only the top *n* features per
        group, each with a ``rank_val`` field. RANK gives tied values the
        same rank, so a group with ties at the cutoff returns more than *n*
        rows.
    """
    direction = "DESC" if descending else "ASC"
    return queryAnalytic(
        url_or_result,
        out_analytics=[{
            # RANK takes no input field - `field` reaches the server via the
            # window's own orderBy.
            "type": "RANK",
            "order_by": f"{field} {direction}",
            "out_name": "rank_val",
        }],
        partition_by=group_by,
        analytic_where=f"rank_val <= {int(n)}",
        where=where,
        geometry=geometry,
        geometry_type=geometry_type,
        spatial_rel=spatial_rel,
        out_fields=out_fields,
        out_sr=out_sr,
        in_sr=in_sr,
        token=token,
        timeout=timeout,
    )


def _convert_geometry(geometry: dict | str, geometry_type: str) -> str:
    """Convert query geometry to the Esri JSON string the REST API takes.

    Kept under this name for callers and tests; the conversion (GeoJSON
    -> Esri, with rings wound the way Esri reads them) lives in
    ``_geojson._convert_query_geometry``.
    ``geometry_type`` is unused here -- pair the result with
    ``_geometry_type_for`` to label it.
    """
    return _convert_query_geometry(geometry)


def getLayerInfo(url_or_result: str | dict, token: str | None = None, timeout: int | None = None) -> dict[str, Any]:
    """Get detailed metadata for a single service layer: fields, geometry
    type, and capabilities.

    Works for any ArcGIS layer URL - a FeatureServer/MapServer sub-layer,
    or an Image Service mosaic layer (e.g. ``.../MapServer/160``).

    Args:
        url_or_result (str or dict): Layer URL (e.g. ``".../MapServer/160"``,
            ``".../FeatureServer/0"``), or a :func:`portal.searchPortal`
            result dict.
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds. Defaults to 60.

    Returns:
        dict: Keys: ``name``, ``geometryType``, ``description``, ``fields``
        (list of ``{name, type, alias}``), ``extent``, ``maxRecordCount``,
        ``capabilities`` (list of supported operations, e.g.
        ``["Map", "Query"]``), ``advancedQueryCapabilities``,
        ``supportedQueryFormats``.

        An ImageServer *root* URL is a valid target and returns the mosaic
        catalog's own fields; ``geometryType`` is empty for it, since the
        service root has no single geometry type of its own.

    Raises:
        ValueError: If *url* points at a container rather than a queryable
            layer - either a group layer or a MapServer/FeatureServer
            service root - in which case the message names the sub-layers to
            use instead. Also raised if the service answers with an Esri
            error object, or with a body that isn't JSON.
        RuntimeError: If the URL is unreachable or answers with an HTTP
            error status (see :func:`queryFeatureServiceCount`).

    Example::

        import georest
        from georest.esri import services

        quakes = "https://sampleserver6.arcgisonline.com/arcgis/rest/services/Earthquakes_Since1970/FeatureServer/0"
        info = services.getLayerInfo(quakes)
        georest.to_frame(info["fields"])   # name, type and alias of each field
    """
    url = _resolve_url(url_or_result)
    params = build_params({"f": "pjson"}, token)
    try:
        data = fetch_json(url, params, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not reach layer at {url!r}: {exc}") from exc

    if "error" in data:
        raise esri_error("Layer returned an error", data['error'])

    # Two distinct shapes of "you pointed at a container, not a layer", which
    # ArcGIS reports with different keys:
    #   - a group *layer* carries `subLayers`
    #   - a MapServer/FeatureServer *service root* carries `layers` and no
    #     `fields` at all (verified against live services)
    # Without the second check, a service-root URL - an easy and common
    # mistake - returned a hollow result (empty name/geometryType, `fields`
    # []) that reads as "this layer has no fields" rather than as an error.
    # An ImageServer root is deliberately excluded: it carries real `fields`
    # (its mosaic catalog's) and no `layers`, and is a supported target.
    container_layers = data.get("subLayers") or data.get("layers") or []
    if container_layers and not (data.get("fields") or []):
        options = ", ".join(f"{s.get('name')} ({s.get('id')})" for s in container_layers)
        kind = data.get("type") or "service root"
        raise ValueError(
            f"{url} is a {kind} ({data.get('name') or 'unnamed'!r}) - "
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


def _bbox_param(bbox: str | dict | list | tuple) -> str:
    """Convert a bbox input to Esri's "xmin,ymin,xmax,ymax" query param form."""
    if isinstance(bbox, str):
        return bbox
    if isinstance(bbox, dict):
        try:
            return f"{bbox['xmin']},{bbox['ymin']},{bbox['xmax']},{bbox['ymax']}"
        except KeyError as exc:
            raise ValueError(f"bbox dict must have xmin/ymin/xmax/ymax keys, got {bbox!r}") from exc
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return ",".join(str(v) for v in bbox)
    raise ValueError(
        f"bbox must be a 'xmin,ymin,xmax,ymax' string, a 4-item list/tuple, "
        f"or a dict with xmin/ymin/xmax/ymax keys; got {bbox!r}"
    )


def exportImage(
    url_or_result: str | dict,
    bbox: str | dict | list | tuple,
    bbox_sr: int = 4326,
    size: tuple[int, int] = (512, 512),
    out_sr: int | None = None,
    image_format: str = "tiff",
    mosaic_rule: dict | None = None,
    rendering_rule: dict | None = None,
    pixel_type: str | None = None,
    no_data: float | list[float] | None = None,
    interpolation: str = "RSP_BilinearInterpolation",
    out_path: str | None = None,
    token: str | None = None,
    timeout: int | None = None,
) -> bytes | str:
    """Export a rendered image from an ArcGIS Image Service.

    Args:
        url_or_result (str or dict): ImageServer URL (e.g.
            ``".../ImageServer"``), or a :func:`portal.searchPortal` result
            dict.
        bbox: Export extent, as a ``"xmin,ymin,xmax,ymax"`` string, a
            4-item list/tuple, or a dict with xmin/ymin/xmax/ymax keys.
        bbox_sr (int, optional): Spatial reference WKID of *bbox*. Defaults
            to 4326 (WGS84).
        size (tuple of int, optional): Output image size in pixels as
            ``(width, height)``. Defaults to ``(512, 512)`` - pass an
            explicit size for large bboxes rather than relying on the
            server's own default, which can be very large.
        out_sr (int, optional): Output spatial reference WKID. Defaults to
            *bbox_sr* if not given.
        image_format (str, optional): Output image format, e.g. ``"tiff"``,
            ``"png"``, ``"png32"``, ``"jpg"``, ``"lerc"``. Defaults to
            ``"tiff"``.
        mosaic_rule (dict, optional): Esri mosaic rule JSON, to select which
            raster(s) in a mosaic dataset contribute to the export (e.g.
            by attribute or lock a specific raster).
        rendering_rule (dict, optional): Esri raster function JSON, applied
            server-side before export (e.g. a colormap or band math
            function).
        pixel_type (str, optional): Output pixel type, e.g. ``"U8"``,
            ``"F32"``.
        no_data (float or list of float, optional): NoData value(s), one
            per band.
        interpolation (str, optional): Resampling method, e.g.
            ``"RSP_BilinearInterpolation"``, ``"RSP_NearestNeighbor"``,
            ``"RSP_CubicConvolution"``. Defaults to
            ``"RSP_BilinearInterpolation"``.
        out_path (str, optional): If given, write the image bytes to this
            path and return the path instead of the raw bytes.
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds. Defaults to 60.

    Returns:
        bytes: The raw image bytes, or the value of *out_path* if given.

    Raises:
        ValueError: If the service returns an error instead of an image -
            including the 200-with-a-JSON-error-body case described below -
            or if *bbox* is malformed.
        RuntimeError: If the service is unreachable or answers with an HTTP
            error status (see :func:`queryFeatureServiceCount`). Note an
            over-large *size* is reported this way, as a server-side 500,
            rather than as a clean Esri error object.

    Example::

        from georest.esri import services

        lcms = "https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer"
        png = services.exportImage(lcms, bbox=(-105.95, 40.5, -105.55, 40.75),
                                   size=(600, 400), image_format="png")
        # In a notebook: from IPython.display import Image; Image(png)
    """
    url = _resolve_url(url_or_result)

    params: dict[str, str] = {
        "bbox": _bbox_param(bbox),
        "bboxSR": str(bbox_sr),
        "size": f"{size[0]},{size[1]}",
        "imageSR": str(out_sr if out_sr is not None else bbox_sr),
        "format": image_format,
        "interpolation": interpolation,
        "f": "image",
    }
    if mosaic_rule is not None:
        params["mosaicRule"] = json.dumps(mosaic_rule)
    if rendering_rule is not None:
        params["renderingRule"] = json.dumps(rendering_rule)
    if pixel_type is not None:
        params["pixelType"] = pixel_type
    if no_data is not None:
        params["noData"] = ",".join(str(v) for v in no_data) if isinstance(no_data, (list, tuple)) else str(no_data)
    if token is not None:
        params["token"] = token

    export_url = f"{url}/exportImage"
    try:
        raw, _content_type = fetch_bytes(export_url, params, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not reach ImageServer at {export_url!r}: {exc}") from exc

    # ArcGIS Server sometimes reports a 200 with a JSON error body instead of
    # raising an HTTP error status (e.g. for an invalid renderingRule) - and
    # the declared Content-Type on that error body is unreliable (observed
    # both "text/plain" and "image/tiff" for the identical JSON error string
    # against the live service, depending on the requested `format`). Sniff
    # the actual bytes instead of trusting the header: no real image format
    # starts with '{', so this reliably catches the error case either way.
    if raw.lstrip()[:1] == b"{":
        try:
            err = json.loads(raw.decode("utf-8", errors="replace")).get("error", {})
            raise esri_error("exportImage error", err)
        except json.JSONDecodeError:
            raise ValueError(f"exportImage returned unexpected content: {raw[:300]!r}") from None

    if out_path is not None:
        with open(out_path, "wb") as f:
            f.write(raw)
        return out_path

    return raw


def getSupportedOperations(
    url_or_result: str | dict, token: str | None = None, timeout: int | None = None
) -> list[dict[str, str]]:
    """List every REST operation actually exposed by a service or layer.

    ArcGIS Server does not expose the full operations list as structured
    JSON - the ``?f=json`` representation only carries a coarse
    ``capabilities`` string (e.g. ``"Image,Metadata,Catalog,Mensuration"``),
    which is not a reliable stand-in: a service can expose operations
    (``computeStatisticsHistograms``, ``getSamples``, etc.) with no
    corresponding capability flag present at all. The authoritative list is
    only rendered on the plain HTML browse page (``?f=html``), as a
    "Supported Operations" section of links - this function fetches that
    page and parses those links out.

    Args:
        url_or_result (str or dict): Service or layer URL, or a
            :func:`portal.searchPortal` result dict.
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds. Defaults to 60.

    Returns:
        list of dict: Each entry has ``name`` (human-readable, e.g.
        ``"Export Image"``), ``operation`` (URL segment, e.g.
        ``"exportImage"``), and ``url`` (full operation URL).

    Raises:
        RuntimeError: If the URL is unreachable or answers with an HTTP
            error status - including the 404 served for a service that does
            not exist (see :func:`queryFeatureServiceCount`).

    Example::

        from georest.esri import services

        lcms = "https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer"
        [op["name"] for op in services.getSupportedOperations(lcms)]
    """
    url = _resolve_url(url_or_result)
    params = build_params({"f": "html"}, token)
    try:
        page = fetch_text(url, params, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not reach service at {url!r}: {exc}") from exc

    section = re.search(r"Supported Operations</b>:(.*?)(?:<b>|\Z)", page, re.DOTALL | re.IGNORECASE)
    if not section:
        return []

    # `url` is absolute (scheme + host + path) but the HTML's <a href> values
    # are server-relative (just the path) - compare path components only,
    # via urlparse, rather than the raw strings (which would never match).
    base_url_path = urllib.parse.urlparse(url).path.rstrip("/")
    operations = []
    for href, name in re.findall(r'<a href="([^"]+)">([^<]+)</a>', section.group(1)):
        clean_name = html.unescape(name).strip()
        path, _, query = href.partition("?")
        path = urllib.parse.urlparse(path).path.rstrip("/")

        if path == base_url_path or path == "":
            # Not a distinct sub-resource - a query-string flag on the
            # resource itself, e.g. "...?f=pjson&returnUpdates=true&"
            # ("Return Updates" on a MapServer layer). Pull the real flag
            # name out of the query string rather than guessing from a URL
            # path segment (which would otherwise grab the layer ID).
            flag_keys = [k for k in urllib.parse.parse_qs(query) if k != "f"]
            operation = flag_keys[0] if flag_keys else clean_name
            op_url = f"{url}?{query.rstrip('&')}" if query else url
        else:
            operation = path.rsplit("/", 1)[-1]
            op_url = f"{url}/{operation}"

        operations.append({"name": clean_name, "operation": operation, "url": op_url})
    return operations


def computeStatisticsHistograms(
    url_or_result: str | dict,
    geometry: dict | str,
    geometry_type: str = "esriGeometryPolygon",
    in_sr: int = 4326,
    mosaic_rule: dict | None = None,
    rendering_rule: dict | None = None,
    pixel_size: dict | None = None,
    token: str | None = None,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    """Compute per-band pixel statistics and a value histogram over an area
    of an ArcGIS Image Service.

    Uses the ``computeStatisticsHistograms`` operation rather than the
    narrower ``computeHistograms`` - it returns both statistics and a
    histogram together for the same single request, so there's no reason
    to call the histogram-only operation separately.

    Args:
        url_or_result (str or dict): ImageServer URL (e.g.
            ``".../ImageServer"``), or a :func:`portal.searchPortal` result
            dict.
        geometry: Area to compute over. Can be a GeoJSON geometry dict, an
            Esri JSON geometry dict/string, a bbox string
            ``"xmin,ymin,xmax,ymax"``, or an Esri JSON polygon/envelope.
        geometry_type (str, optional): Esri geometry type, e.g.
            ``esriGeometryPolygon``, ``esriGeometryEnvelope``. Defaults to
            ``esriGeometryPolygon``.
        in_sr (int, optional): Spatial reference WKID of *geometry*.
            Defaults to 4326 (WGS84).
        mosaic_rule (dict, optional): Esri mosaic rule JSON, to select which
            raster(s) in a mosaic dataset contribute (e.g. by attribute or
            lock a specific raster).
        rendering_rule (dict, optional): Esri raster function JSON, applied
            server-side before computing statistics.
        pixel_size (dict, optional): ``{"x", "y", "spatialReference"}`` to
            compute at a coarser/finer resolution than native. Omit to use
            the service default.
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds. Defaults to
            60. At the native resolution of a high-resolution service (e.g.
            sub-meter imagery), this operation over any non-trivial area can
            exceed a server-side pixel-count limit outright - pass a
            coarser `pixel_size` to avoid that. Even with a reasonable
            `pixel_size`, a large area or a service with a big/complex
            mosaic catalog can legitimately take 30-45+ seconds to respond
            (verified against live services) - raise this rather than
            assuming a hang.

    Returns:
        list of dict: One entry per band, each with ``band`` (int index),
        ``min``, ``max``, ``mean``, ``stddev``, ``sum``, ``median``,
        ``mode``, ``count``, and ``histogram`` (a dict with ``min``,
        ``max``, ``size``, ``counts``).

        Returns an **empty list** - not an error - when the service computed
        nothing for the request. The most common cause is a *pixel_size*
        that is absurdly coarse for its own spatial reference: it is
        interpreted in the units of the ``spatialReference`` inside
        *pixel_size*, not the raster's native units, so
        ``{"x": 90, "y": 90, "spatialReference": {"wkid": 4326}}`` asks for
        90-*degree* pixels rather than 90-metre ones, and the service
        silently returns nothing (verified against a live service). Check
        for an empty result rather than assuming at least one band.

    Raises:
        ValueError: If *geometry* cannot be converted to Esri JSON, or the
            service answers with an Esri error object or a non-JSON body.
        RuntimeError: If the service is unreachable or answers with an HTTP
            error status (see :func:`queryFeatureServiceCount`).

    Example::

        from georest.esri import services

        lcms = "https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer"
        box = {"rings": [[[-105.9, 40.55], [-105.6, 40.55], [-105.6, 40.7],
                           [-105.9, 40.7], [-105.9, 40.55]]],
               "spatialReference": {"wkid": 4326}}
        stats = services.computeStatisticsHistograms(lcms, box)
        stats[0]["histogram"]["counts"]   # pixel count per class value
    """
    url = _resolve_url(url_or_result)

    # Unlike /query, computeStatisticsHistograms silently ignores a separate
    # inSR param (returns empty statistics, no error) - verified against the
    # live service. The spatial reference must be embedded in the geometry
    # JSON itself.
    converted = _convert_geometry(geometry, geometry_type)
    try:
        geom_json = json.loads(converted)
    except json.JSONDecodeError as exc:
        raise ValueError(f"geometry could not be converted to Esri JSON: {geometry!r}") from exc
    geom_json.setdefault("spatialReference", {"wkid": in_sr})

    params: dict[str, str] = {
        "geometry": json.dumps(geom_json),
        "geometryType": _geometry_type_for(converted, geometry_type),
        "f": "json",
    }
    if mosaic_rule is not None:
        params["mosaicRule"] = json.dumps(mosaic_rule)
    if rendering_rule is not None:
        params["renderingRule"] = json.dumps(rendering_rule)
    if pixel_size is not None:
        params["pixelSize"] = json.dumps(pixel_size)
    if token is not None:
        params["token"] = token

    stats_url = f"{url}/computeStatisticsHistograms"
    try:
        data = fetch_json(stats_url, params, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not reach ImageServer at {stats_url!r}: {exc}") from exc

    if "error" in data:
        raise esri_error("computeStatisticsHistograms error", data['error'])

    statistics = data.get("statistics") or []
    histograms = data.get("histograms") or []

    return [
        {
            "band": i,
            "min": stat.get("min"),
            "max": stat.get("max"),
            "mean": stat.get("mean"),
            "stddev": stat.get("standardDeviation"),
            "sum": stat.get("sum"),
            "median": stat.get("median"),
            "mode": stat.get("mode"),
            "count": stat.get("count"),
            "histogram": {
                "min": hist.get("min"),
                "max": hist.get("max"),
                "size": hist.get("size"),
                "counts": hist.get("counts", []),
            },
        }
        for i, (stat, hist) in enumerate(zip(statistics, histograms))
    ]


def identifyPixelValue(
    url_or_result: str | dict,
    x: float,
    y: float,
    in_sr: int = 4326,
    mosaic_rule: dict | None = None,
    rendering_rule: dict | None = None,
    return_catalog_items: bool = False,
    token: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Identify the pixel value at a single point on an ArcGIS Image Service.

    This is inherently a point operation: the underlying ``identify``
    operation accepts other geometry types (e.g. polygon), but silently
    collapses them to their centroid rather than sampling the area
    (verified against a live service), so this function only takes a
    single x/y rather than pretending to support area input. For area-wide
    pixel-value distribution, use :func:`computeStatisticsHistograms`
    instead.

    Args:
        url_or_result (str or dict): ImageServer URL (e.g.
            ``".../ImageServer"``), or a :func:`portal.searchPortal` result
            dict.
        x (float): X coordinate (longitude, if *in_sr* is 4326).
        y (float): Y coordinate (latitude, if *in_sr* is 4326).
        in_sr (int, optional): Spatial reference WKID of *x*/*y*. Defaults
            to 4326 (WGS84).
        mosaic_rule (dict, optional): Esri mosaic rule JSON, to select which
            raster(s) in a mosaic dataset contribute (e.g. by attribute or
            lock a specific raster).
        rendering_rule (dict, optional): Esri raster function JSON, applied
            server-side before identifying the value.
        return_catalog_items (bool, optional): If True, also return the
            footprint attributes of the raster(s) covering this point
            (e.g. which mosaic source/year contributed) in the ``catalog_items``
            result key. Defaults to False. Note: this does *not* control
            whether ``band_values`` is populated - see below.
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds. Defaults to
            60 - a service with a large/complex mosaic catalog (many
            overlapping contributing rasters at a given point) can
            genuinely take 30-45+ seconds to respond (verified against a
            live service); pass a larger value rather than assuming a hang.

    Returns:
        dict: ``value`` (str, the composited pixel value, or ``"NoData"``),
        ``band_values`` (list of str, one per band/raster contributing at
        this point - **empty** where the point is NoData), ``location``, and
        ``catalog_items`` (list of raster footprint
        ``{"attributes", "geometry"}`` dicts if *return_catalog_items* is
        True, else ``None``).

        ``location`` is ``{"x", "y", "spatialReference"}`` as returned by the
        service, reprojected into the service's **native** spatial reference
        - *not* echoed back in *in_sr*. Querying a WGS84 point on a Web
        Mercator service returns Web Mercator metres with
        ``{"wkid": 102100}`` (verified against a live service), so read the
        ``spatialReference`` rather than assuming the coordinates come back
        in the units you sent.

    Raises:
        ValueError: If the service answers with an Esri error object or a
            non-JSON body.
        RuntimeError: If the service is unreachable or answers with an HTTP
            error status (see :func:`queryFeatureServiceCount`).

    Example::

        from georest.esri import services

        lcms = "https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer"
        services.identifyPixelValue(lcms, x=-105.75, y=40.62)["value"]   # the class at one point
    """
    url = _resolve_url(url_or_result)

    params: dict[str, str] = {
        # Like computeStatisticsHistograms, identify silently ignores a
        # separate inSR param - it echoes the raw x/y straight back
        # mislabeled as already being in the target SR, instead of
        # reprojecting or erroring (verified against a live service). The
        # spatial reference must be embedded directly in the geometry JSON.
        "geometry": json.dumps({"x": x, "y": y, "spatialReference": {"wkid": in_sr}}),
        "geometryType": "esriGeometryPoint",
        # Always request catalog items on the wire, regardless of
        # `return_catalog_items` - Esri only populates `properties.Values`
        # (this function's `band_values`) when `returnCatalogItems=true` is
        # sent (verified against a live service: with it false, `properties`
        # comes back null even at a point with real, non-NoData data). What
        # `return_catalog_items` actually controls is only whether we
        # include the (often large) footprint geometries in our own return
        # value below.
        "returnCatalogItems": "true",
        "f": "json",
    }
    if mosaic_rule is not None:
        params["mosaicRule"] = json.dumps(mosaic_rule)
    if rendering_rule is not None:
        params["renderingRule"] = json.dumps(rendering_rule)
    if token is not None:
        params["token"] = token

    identify_url = f"{url}/identify"
    try:
        data = fetch_json(identify_url, params, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not reach ImageServer at {identify_url!r}: {exc}") from exc

    if "error" in data:
        raise esri_error("identify error", data['error'])

    props = data.get("properties") or {}
    catalog = data.get("catalogItems") or {}

    return {
        "value": data.get("value"),
        "band_values": props.get("Values", []),
        "location": data.get("location", {}),
        "catalog_items": catalog.get("features") if return_catalog_items else None,
    }


def getSamples(
    url_or_result: str | dict,
    points: list[tuple[float, float]],
    in_sr: int = 4326,
    mosaic_rule: dict | None = None,
    pixel_size: dict | None = None,
    token: str | None = None,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    """Sample pixel values at multiple points on an ArcGIS Image Service.

    Tries the batch ``getSamples`` operation first (one request for every
    point). If that fails, falls back to calling :func:`identifyPixelValue`
    once per point instead.

    This fallback isn't defensive boilerplate - verified against a live
    service, ``getSamples`` can fail outright (HTTP 400, sometimes even
    500) for a perfectly valid, correctly-formatted request at specific
    locations (e.g. a point landing on a NoData pixel), while ``identify``
    handles the identical location cleanly. Since a single bad point would
    otherwise cost every other point's result too, the fallback keeps
    results for whichever points can succeed.

    Args:
        url_or_result (str or dict): ImageServer URL (e.g.
            ``".../ImageServer"``), or a :func:`portal.searchPortal` result
            dict.
        points (list of tuple): ``(x, y)`` coordinate pairs to sample.
        in_sr (int, optional): Spatial reference WKID of *points*. Defaults
            to 4326 (WGS84).
        mosaic_rule (dict, optional): Esri mosaic rule JSON, to select which
            raster(s) in a mosaic dataset contribute.
        pixel_size (dict, optional): ``{"x", "y", "spatialReference"}`` to
            sample at a coarser/finer resolution than native.
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds, applied to
            both the batch call and any per-point identify fallback.
            Defaults to 60.

    Returns:
        list of dict: One entry per input point, in the same order as
        *points*, each with ``x``, ``y``, ``value`` (str, or ``None`` if
        both the batch call and the fallback failed for this point), and
        ``source`` (``"getSamples"``, ``"identify"``, or an ``"error: ..."``
        string if this specific point could not be sampled at all).

        This function does not raise for a point it cannot sample - a
        failing point is reported in-band via ``source`` so the remaining
        points still return their values.

        ``x``/``y`` are in *in_sr* on both paths, but they do not come from
        the same place: on the ``getSamples`` path they are echoed back by
        the service, and on the ``identify`` fallback they are the input
        coordinates, copied through unchanged. (The fallback deliberately
        does not surface :func:`identifyPixelValue`'s own ``location``,
        which the service returns in its *native* spatial reference rather
        than in *in_sr* - mixing the two would put different coordinate
        systems in one result list.)

    Note:
        *pixel_size* applies only to the batch ``getSamples`` call; the
        ``identify`` fallback has no equivalent parameter, so a point
        resolved by the fallback is sampled at the service's native
        resolution regardless.

    Example::

        import georest
        from georest.esri import services

        lcms = "https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer"
        points = [(-105.75, 40.62), (-105.70, 40.60), (-105.65, 40.58)]
        georest.to_frame(services.getSamples(lcms, points))
    """
    url = _resolve_url(url_or_result)

    params: dict[str, str] = {
        # Same embedded-spatialReference requirement as identify/
        # computeStatisticsHistograms - a separate inSR param is not
        # reliable here either.
        "geometry": json.dumps({"points": [[x, y] for x, y in points], "spatialReference": {"wkid": in_sr}}),
        "geometryType": "esriGeometryMultipoint",
        "f": "json",
    }
    if mosaic_rule is not None:
        params["mosaicRule"] = json.dumps(mosaic_rule)
    if pixel_size is not None:
        params["pixelSize"] = json.dumps(pixel_size)
    if token is not None:
        params["token"] = token

    # The batch call doesn't reliably behave all-or-nothing: it can also
    # return a `samples` array shorter than `points` (silently dropping
    # ones it couldn't resolve, verified against a live service) rather
    # than erroring outright. Match results back to input points by
    # `locationId` and only fall back to identify for whichever specific
    # points didn't come back - not the whole batch.
    samples_url = f"{url}/getSamples"
    by_location_id: dict[int, dict[str, Any]] = {}
    try:
        data = fetch_json(samples_url, params, timeout=timeout)
        if "error" not in data:
            for s in data.get("samples", []):
                loc_id = s.get("locationId")
                if loc_id is not None:
                    by_location_id[loc_id] = {
                        "x": s.get("location", {}).get("x"),
                        "y": s.get("location", {}).get("y"),
                        "value": s.get("value"),
                        "source": "getSamples",
                    }
    except (RuntimeError, ValueError):
        # RuntimeError is every HTTP/transport failure (_http converts
        # HTTPError and URLError alike); ValueError is a body that won't
        # parse as JSON, which is what a degraded HTML error page served
        # with status 200 looks like. Either way the batch produced nothing,
        # and every point falls through to the per-point identify below.
        pass

    results = []
    for i, (x, y) in enumerate(points):
        if i in by_location_id:
            results.append(by_location_id[i])
            continue
        try:
            r = identifyPixelValue(url, x, y, in_sr=in_sr, mosaic_rule=mosaic_rule, token=token, timeout=timeout)
            results.append({"x": x, "y": y, "value": r.get("value"), "source": "identify"})
        except (RuntimeError, ValueError, ConnectionError) as exc:
            # RuntimeError matters most here and was previously missing: it
            # is what identifyPixelValue actually raises for an unreachable
            # host or an HTTP error status, so without it a single transport
            # failure escaped and destroyed the results for every other
            # point - exactly the all-or-nothing behaviour this per-point
            # fallback exists to prevent.
            results.append({"x": x, "y": y, "value": None, "source": f"error: {exc}"})
    return results


def queryBoundary(
    url_or_result: str | dict,
    mosaic_rule: dict | None = None,
    out_sr: int = 4326,
    token: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Get the true coverage boundary of an ArcGIS Image Service.

    Unlike the rectangular ``extent`` returned by :func:`getLayerInfo` /
    :func:`portal.getServiceMetadata`, this is the actual coverage shape -
    useful as a pre-flight check for whether a service actually covers an
    AOI before spending an :func:`exportImage` /
    :func:`computeStatisticsHistograms` call on it.

    Unlike ``identify``/``computeStatisticsHistograms``/``getSamples``,
    this operation *does* respect a plain ``outSR`` param directly
    (verified against a live service) - there's no geometry input here to
    embed a spatial reference into in the first place.

    Args:
        url_or_result (str or dict): ImageServer URL (e.g.
            ``".../ImageServer"``), or a :func:`portal.searchPortal` result
            dict.
        mosaic_rule (dict, optional): Esri mosaic rule JSON, to scope the
            boundary to a subset of the mosaic (e.g. one raster/year). Note:
            not every mosaic rule type actually changes this operation's
            result - verified against a live service, a ``where``-clause
            rule that should lock to a single raster returned an identical
            boundary to no rule at all, and the operation doesn't validate
            the rule (a garbage rule is silently ignored rather than
            erroring).
        out_sr (int, optional): Output spatial reference WKID. Defaults to
            4326 (WGS84).
        token (str, optional): ArcGIS token for secured services.
        timeout (int, optional): Request timeout in seconds. Defaults to
            60 - a service with a large/complex mosaic catalog can
            genuinely take 30-45+ seconds to respond even for this
            operation's simple result (verified against a live service);
            pass a larger value rather than assuming a hang.

    Returns:
        dict: GeoJSON-shaped ``{"type": "Polygon", "coordinates": [...],
        "area": <float>}``. Note: unlike the coordinates, ``area`` does
        *not* vary with *out_sr* - verified against a live service, it's
        always reported in the service's native storage spatial reference
        (e.g. square meters in Web Mercator), regardless of what SR the
        geometry itself was requested in.

    Raises:
        ValueError: If the service answers with an Esri error object or a
            non-JSON body.
        RuntimeError: If the service is unreachable or answers with an HTTP
            error status (see :func:`queryFeatureServiceCount`).

    Example::

        from georest.esri import services

        lcms = "https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer"
        boundary = services.queryBoundary(lcms)   # the area the imagery actually covers
    """
    url = _resolve_url(url_or_result)

    params: dict[str, str] = {
        "outSR": str(out_sr),
        "f": "json",
    }
    if mosaic_rule is not None:
        params["mosaicRule"] = json.dumps(mosaic_rule)
    if token is not None:
        params["token"] = token

    boundary_url = f"{url}/queryBoundary"
    try:
        data = fetch_json(boundary_url, params, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not reach ImageServer at {boundary_url!r}: {exc}") from exc

    if "error" in data:
        raise esri_error("queryBoundary error", data['error'])

    geom = _esri_geometry_to_geojson(data.get("shape")) or {}
    return {**geom, "area": data.get("area")}
