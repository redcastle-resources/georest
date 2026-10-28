"""Private helpers that turn API responses into pandas DataFrames.

Shared by the usgs modules so every one of them returns the same shape:
one row per feature, its properties as columns, and point coordinates as
``lon`` and ``lat``. Shapes that are not points (flowlines, basins) are left
to the callers that return GeoJSON.

pandas is imported inside the functions, so ``import georest`` stays cheap.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def frame(records: Iterable[dict], columns: Iterable[str] | None = None) -> Any:
    """A DataFrame of ``records``, keeping ``columns`` even when there are none.

    An empty DataFrame built from ``[]`` has no columns at all, so a caller's
    next line, usually a column selection, would raise a bare KeyError that
    names none of the real problem.
    """
    import pandas as pd

    records = list(records)
    if not records and columns:
        return pd.DataFrame(columns=list(columns))
    return pd.DataFrame(records)


def point_coordinates(geometry: dict | None) -> tuple[float | None, float | None]:
    """``(lon, lat)`` of a GeoJSON Point, or ``(None, None)`` for anything else."""
    if isinstance(geometry, dict) and geometry.get("type") == "Point":
        coords = geometry.get("coordinates") or []
        if len(coords) >= 2:
            return coords[0], coords[1]
    return None, None


def features_frame(fc: dict, columns: Iterable[str] | None = None,
                   id_column: str | None = None) -> Any:
    """One row per feature of a GeoJSON FeatureCollection.

    Properties become columns; a Point geometry becomes ``lon`` and ``lat``.
    When ``id_column`` is given, the feature's top-level ``id`` is kept under
    that name (some APIs carry the identifier only there).
    """
    rows = []
    for feature in (fc or {}).get("features") or []:
        row = dict(feature.get("properties") or {})
        if id_column and "id" in feature and id_column not in row:
            row = {id_column: feature["id"], **row}
        row["lon"], row["lat"] = point_coordinates(feature.get("geometry"))
        rows.append(row)
    return frame(rows, columns)
