"""Private helpers for the georest.cloud modules: tables and small checks.

pandas is imported inside the function, so ``import georest`` stays cheap.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def frame(records: Iterable[dict], columns: Iterable[str]) -> Any:
    """A DataFrame of ``records`` with ``columns``, even when there are none.

    An empty DataFrame built from ``[]`` has no columns at all, so a caller's
    next line, usually a column selection, would raise a bare KeyError that
    names none of the real problem.
    """
    import pandas as pd

    records = list(records)
    columns = list(columns)
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records, columns=columns)


def check_bbox(bbox) -> tuple[float, float, float, float]:
    """``bbox`` as four floats, or a ValueError that says what a bbox is."""
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise ValueError(f"bbox {bbox!r} must be four numbers: (west, south, east, north) "
                         f"in decimal degrees") from None
    if not (xmin < xmax and ymin < ymax):
        raise ValueError(f"bbox {bbox!r} must be (west, south, east, north) "
                         f"with west < east and south < north")
    return xmin, ymin, xmax, ymax
