"""Private helpers for the georest.noaa modules: tables and small checks.

pandas is imported inside the function, so ``import georest`` stays cheap.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def frame(records: Iterable[dict], columns: Iterable[str]) -> Any:
    """A DataFrame of ``records`` with ``columns``, even when there are none."""
    import pandas as pd

    records = list(records)
    columns = list(columns)
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records, columns=columns)


def check_point(lon: float, lat: float) -> tuple[float, float]:
    lon, lat = float(lon), float(lat)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(
            f"({lon}, {lat}) is not a longitude, latitude pair in degrees "
            f"(longitude first, -180 to 180; latitude -90 to 90)"
        )
    return lon, lat


def check_bbox(bbox) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    if not (xmin < xmax and ymin < ymax):
        raise ValueError(f"bbox {bbox!r} must be (xmin, ymin, xmax, ymax) "
                         f"with xmin < xmax and ymin < ymax")
    return xmin, ymin, xmax, ymax


def km_between(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in kilometers."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


def number(value: Any, *, missing: float | None = None) -> float | None:
    """A float, or None for blanks, junk and the service's "no value" sentinel."""
    if value is None or value == "":
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value or (missing is not None and value <= missing):
        return None
    return value
