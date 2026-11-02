"""Turn what georest returns into a pandas DataFrame.

georest.usgs and georest.usfs already return DataFrames. georest.esri
returns GeoJSON, because its features carry real geometry (polygons, lines)
that a plain table cannot hold, and because it uses only the standard
library. :func:`to_frame` is the bridge: the attribute table of any
FeatureCollection, list of Features or list of records, one row each.

pandas is imported inside the function, so ``import georest`` stays cheap.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from typing import Any


def _rows_and_extras(data: Any) -> tuple[list[dict], dict, bool]:
    """(rows, top-level members other than the features, whether the rows are Features)."""
    if isinstance(data, dict):
        if data.get("type") == "Feature":
            return [data], {}, True
        if "features" in data:
            extras = {k: v for k, v in data.items() if k not in ("features", "type")}
            return list(data.get("features") or []), extras, True
        return [data], {}, False
    rows = list(data or [])
    is_features = bool(rows) and all(
        isinstance(r, dict) and (r.get("type") == "Feature" or "properties" in r) for r in rows)
    return rows, {}, is_features


def to_frame(data: Any, *, geometry: bool = False) -> Any:
    """A DataFrame of ``data``: one row per feature or record.

    Args:
        data: A GeoJSON FeatureCollection (any dict with ``features``, such
            as an esri query result), a single Feature, a list of Features,
            or a list of plain records (an EDW service search, a portal
            search).
        geometry: Also keep each feature's GeoJSON geometry, as a dict in a
            ``geometry`` column. Points always get ``lon`` and ``lat``
            columns either way.

    Returns:
        ``pandas.DataFrame``. For features: the properties as columns, the
        feature ``id`` when the properties lack one, ``lon``/``lat`` for
        points. Members of a FeatureCollection other than its features
        (``exceededTransferLimit``, ``truncated``, ...) go to ``df.attrs``.

    Example::

        import georest
        from georest.esri import edw

        fires = edw.query_features("EDW_MTBS_01", 63, where="fire_name = 'CAMERON PEAK'",
                                   out_fields="fire_name,ig_date,acres")
        georest.to_frame(fires)
    """
    import pandas as pd

    rows, extras, is_features = _rows_and_extras(data)
    if not is_features:
        df = pd.DataFrame(rows)
    else:
        any_point = any(isinstance(f.get("geometry"), dict)
                        and f["geometry"].get("type") == "Point" for f in rows)
        records = []
        for feature in rows:
            record = dict(feature.get("properties") or {})
            if "id" not in record and feature.get("id") is not None:
                record = {"id": feature["id"], **record}
            geom = feature.get("geometry")
            if any_point:
                coords = geom.get("coordinates") if isinstance(geom, dict) and geom.get("type") == "Point" else None
                record["lon"], record["lat"] = (coords[0], coords[1]) if coords and len(coords) >= 2 else (None, None)
            if geometry:
                record["geometry"] = geom
            records.append(record)
        df = pd.DataFrame(records)
    for key, value in extras.items():
        df.attrs[key] = value
    return df
