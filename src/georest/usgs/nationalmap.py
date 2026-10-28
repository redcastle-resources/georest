"""Clients for The National Map: point elevations and product downloads.

Two USGS services behind The National Map:

* the **Elevation Point Query Service (EPQS)**, which returns the 3DEP
  elevation at any point in the US, from the best data available there
  (down to 1 meter lidar-derived models where they exist);
* **TNM Access**, the catalog of downloadable National Map products: 3DEP
  elevation models and lidar, US Topo maps, NAIP imagery, hydrography,
  boundaries, structures and more, each with a direct download link.

::

    from georest.usgs import nationalmap

    nationalmap.elevation(-105.75, 40.62)              # meters, e.g. 3149.6
    tiles = nationalmap.find_products(
        (-105.8, 40.6, -105.7, 40.7), "Digital Elevation Model (DEM) 1 meter")
    tiles[["title", "size_mb", "download_url"]]

Tables come back as pandas DataFrames.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ._frames import frame
from ._http import fetch_json, fetch_text

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"
TNM_URL = "https://tnmaccess.nationalmap.gov/api/v1"

#: Products per request to TNM Access. Every page is fetched; this only
#: sets how many requests that takes.
_PAGE_SIZE = 500

_PRODUCT_COLUMNS = ("title", "dataset", "format", "size_mb", "publication_date",
                    "west", "south", "east", "north", "download_url",
                    "metadata_url", "source_id")


def _check_point(lon: float, lat: float) -> tuple[float, float]:
    lon, lat = float(lon), float(lat)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(
            f"({lon}, {lat}) is not a longitude, latitude pair in degrees "
            f"(longitude first, -180 to 180; latitude -90 to 90)"
        )
    return lon, lat


def elevation(lon: float, lat: float, *, units: str = "meters") -> float | None:
    """The 3DEP elevation at a point.

    Args:
        lon, lat: The point, in decimal degrees.
        units: ``"meters"`` (the default) or ``"feet"``.

    Returns:
        The elevation, or ``None`` where there is no data, such as over the
        open ocean. (EPQS reports that case as "Call failed" rather than as
        an error, so it is not raised.) Coverage reaches past US borders
        into parts of Canada and Mexico.

    Example::

        from georest.usgs import nationalmap

        nationalmap.elevation(-105.75, 40.62)                  # meters
        nationalmap.elevation(-105.75, 40.62, units="feet")
    """
    return _query_point(lon, lat, units)["elevation"]


def _query_point(lon: float, lat: float, units: str) -> dict:
    lon, lat = _check_point(lon, lat)
    unit = {"meters": "Meters", "m": "Meters", "feet": "Feet", "ft": "Feet"}.get(str(units).lower())
    if unit is None:
        raise ValueError(f"units must be 'meters' or 'feet', not {units!r}")
    params = {"x": lon, "y": lat, "wkid": 4326, "units": unit, "includeDate": "true"}
    # EPQS answers in plain text, under HTTP 200, in two cases. "Call
    # failed" means it has no elevation there (open ocean, say). Anything
    # else, such as "Invalid or missing input parameters." for a point
    # that is valid, has been seen to come and go between identical
    # requests, so it is retried before being reported.
    for attempt in range(3):
        text = fetch_text(EPQS_URL, params)
        try:
            data = json.loads(text)
            break
        except ValueError:
            if text.strip().startswith("Call failed"):
                return {"lon": lon, "lat": lat, "elevation": None, "resolution_m": None,
                        "acquired": None}
            if attempt == 2:
                raise RuntimeError(
                    f"the Elevation Point Query Service answered {text.strip()!r} "
                    f"for ({lon}, {lat}) three times running"
                ) from None
    value = data.get("value")
    value = float(value) if value not in (None, "", "-1000000") else None
    if value is not None and value <= -999999:
        value = None
    return {"lon": lon, "lat": lat, "elevation": value,
            "resolution_m": data.get("resolution"),
            "acquired": (data.get("attributes") or {}).get("AcquisitionDate")}


def elevations(points: Iterable[tuple[float, float]], *, units: str = "meters") -> Any:
    """The 3DEP elevation at each of several points.

    EPQS answers one point per request, so this makes one request per point.

    Args:
        points: ``(lon, lat)`` pairs in decimal degrees.
        units: ``"meters"`` (the default) or ``"feet"``.

    Returns:
        ``pandas.DataFrame`` with ``lon``, ``lat``, ``elevation`` (empty where
        there is no data), ``resolution_m`` (the resolution of the source data
        at that point) and ``acquired`` (when that data was collected).

    Example::

        from georest.usgs import nationalmap

        # A transect across the Continental Divide at Rocky Mountain National Park
        transect = [(-105.90 + i * 0.05, 40.40) for i in range(7)]
        nationalmap.elevations(transect, units="feet")
    """
    return frame((_query_point(lon, lat, units) for lon, lat in points),
                 ("lon", "lat", "elevation", "resolution_m", "acquired"))


def list_datasets() -> Any:
    """The National Map products you can search with :func:`find_products`.

    Returns:
        ``pandas.DataFrame``, one row per product type: ``dataset`` (the
        exact name to pass to :func:`find_products`), ``title``, ``category``
        (the group it belongs to), ``extents`` (tile sizes) and ``info_url``.

    Example::

        from georest.usgs import nationalmap

        datasets = nationalmap.list_datasets()
        datasets[datasets["category"].str.contains("Elevation")]
    """
    rows = []
    for group in fetch_json(f"{TNM_URL}/datasets") or []:
        tags = group.get("tags") or []
        for item in tags or [group]:
            rows.append({
                "dataset": item.get("sbDatasetTag"),
                "title": item.get("title"),
                "category": group.get("title"),
                "extents": ", ".join(item.get("extents") or []),
                "info_url": item.get("infoUrl"),
            })
    return frame(rows, ("dataset", "title", "category", "extents", "info_url"))


def find_products(bbox: tuple[float, float, float, float] | str, dataset: str, *,
                  formats: str | Iterable[str] | None = None,
                  start: str | None = None, end: str | None = None) -> Any:
    """Downloadable National Map products covering an area.

    Every matching product is returned; TNM Access pages are followed.

    Args:
        bbox: ``(west, south, east, north)`` in decimal degrees.
        dataset: The product type, exactly as :func:`list_datasets` gives it
            in its ``dataset`` column, e.g. ``"Digital Elevation Model (DEM) 1
            meter"`` or ``"National Elevation Dataset (NED) 1/3 arc-second"``.
        formats: Keep only these formats, e.g. ``"GeoTIFF"`` or ``["LAS",
            "LAZ"]``.
        start, end: Publication date range, ``"YYYY-MM-DD"``.

    Returns:
        ``pandas.DataFrame``, one row per product: ``title``, ``dataset``,
        ``format``, ``size_mb``, ``publication_date``, its bounding box
        (``west``, ``south``, ``east``, ``north``), ``download_url``,
        ``metadata_url`` and ``source_id``.

    Example::

        from georest.usgs import nationalmap

        # 1 meter lidar-derived elevation tiles over part of Rocky Mountain National Park
        tiles = nationalmap.find_products((-105.8, 40.3, -105.6, 40.4),
                                          "Digital Elevation Model (DEM) 1 meter")
        tiles[["title", "publication_date", "size_mb", "download_url"]]
    """
    if isinstance(bbox, str):
        bbox_text = bbox
    else:
        west, south, east, north = (float(v) for v in bbox)
        _check_point(west, south)
        _check_point(east, north)
        if west >= east or south >= north:
            raise ValueError("bbox is (west, south, east, north); west < east and south < north")
        bbox_text = f"{west},{south},{east},{north}"
    params: dict[str, Any] = {"bbox": bbox_text, "datasets": dataset, "max": _PAGE_SIZE}
    if formats:
        params["prodFormats"] = formats if isinstance(formats, str) else ",".join(formats)
    if start or end:
        params["dateType"] = "Publication"
        if start:
            params["start"] = start
        if end:
            params["end"] = end

    rows: list[dict] = []
    offset = 0
    while True:
        page = fetch_json(f"{TNM_URL}/products", {**params, "offset": offset})
        errors = page.get("errors") or []
        if errors:
            raise RuntimeError(f"TNM Access refused the search: {errors}")
        items = page.get("items") or []
        for item in items:
            box = item.get("boundingBox") or {}
            size = item.get("sizeInBytes")
            rows.append({
                "title": item.get("title"),
                "dataset": dataset,
                "format": item.get("format"),
                "size_mb": round(size / 1e6, 1) if isinstance(size, (int, float)) else None,
                "publication_date": item.get("publicationDate"),
                "west": box.get("minX"), "south": box.get("minY"),
                "east": box.get("maxX"), "north": box.get("maxY"),
                "download_url": item.get("downloadURL"),
                "metadata_url": item.get("metaUrl"),
                "source_id": item.get("sourceId"),
            })
        offset += len(items)
        if not items or offset >= int(page.get("total") or 0):
            break
    if not rows and dataset not in set(list_datasets()["dataset"]):
        raise ValueError(
            f"{dataset!r} is not a National Map dataset name; list_datasets() gives "
            f"the exact names (its 'dataset' column)"
        )
    return frame(rows, _PRODUCT_COLUMNS)
