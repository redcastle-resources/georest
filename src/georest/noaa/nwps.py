"""Client for the National Water Prediction Service (api.water.noaa.gov).

NOAA's river forecast gauges: the stage now, its flood category, the
forecast crest, and the stages at which each gauge floods::

    from georest.noaa import nwps

    nwps.gauges((-95.9, 29.4, -95.0, 30.2))            # every gauge in a box
    nwps.gauges((-95.9, 29.4, -95.0, 30.2), flooding_only=True)
    nwps.gauge("BBST2")["flood_stages"]                 # {'action': 17.0, ...}

What the raw API does that this module absorbs:

* **Sentinels that look like readings.** A missing stage is ``-999`` (or
  ``-9999`` for a flood threshold) and a missing time is the year 1. They
  come back as ``None`` here.
* **A forecast that looks like an observation.** The forecast block has the
  same shape as the observed one; a stale forecast is flagged
  ``fcst_not_current``. Forecast columns are named ``forecast_*``, carry
  their own time, and are ``None`` when the forecast is not current - so a
  future crest is never read as the stage now.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from typing import Any

from ._frames import check_bbox, frame, number
from ._http import fetch_json

API_URL = "https://api.water.noaa.gov/nwps/v1"

_GAUGE_COLUMNS = ("lid", "name", "state", "lon", "lat", "stage", "stage_unit",
                  "flow", "flow_unit", "flood_category", "observed_at",
                  "forecast_stage", "forecast_category", "forecast_at")

#: Categories meaning "not flooding" or "no category" - everything else is
#: a flood category (action, minor, moderate, major).
_NOT_FLOODING = frozenset({None, "", "no_flooding", "not_defined", "obs_not_current",
                           "fcst_not_current", "out_of_service"})


def _time(value: Any) -> str | None:
    if not value or str(value).startswith("0001-"):
        return None
    return value


def _reading(block: dict, prefix: str = "") -> dict:
    block = block or {}
    category = block.get("floodCategory") or None
    if prefix and category == "fcst_not_current":
        return {f"{prefix}stage": None, f"{prefix}category": None, f"{prefix}at": None}
    if prefix:
        return {f"{prefix}stage": number(block.get("primary"), missing=-999),
                f"{prefix}category": category,
                f"{prefix}at": _time(block.get("validTime"))}
    return {"stage": number(block.get("primary"), missing=-999),
            "stage_unit": block.get("primaryUnit") or None,
            "flow": number(block.get("secondary"), missing=-999),
            "flow_unit": block.get("secondaryUnit") or None,
            "flood_category": category,
            "observed_at": _time(block.get("validTime"))}


def gauges(bbox: tuple[float, float, float, float], *, flooding_only: bool = False) -> Any:
    """The river forecast gauges in a box, with their latest stage.

    Args:
        bbox: ``(xmin, ymin, xmax, ymax)`` in decimal degrees.
        flooding_only: Only gauges at a flood category now (action stage
            or above).

    Returns:
        A DataFrame with ``lid`` (the gauge id), ``name``, ``state``,
        ``lon``, ``lat``, ``stage`` and ``stage_unit``, ``flow`` and
        ``flow_unit``, ``flood_category`` and ``observed_at``, and the
        ``forecast_stage``, ``forecast_category`` and ``forecast_at`` of the
        forecast crest (``None`` when the forecast is not current).
        ``df.attrs["flooding_now"]`` counts gauges at a flood category.
        No rows means no NWPS gauge in the box, not that nothing is flooding.

    Raises:
        ValueError: For a malformed ``bbox``, before any request.
        NOAAError: If the service refuses the request or cannot be reached.

    Example::

        from georest.noaa import nwps

        nwps.gauges((-95.9, 29.4, -95.0, 30.2))[["name", "stage", "flood_category"]]
    """
    xmin, ymin, xmax, ymax = check_bbox(bbox)
    data = fetch_json(f"{API_URL}/gauges", {
        "bbox.xmin": xmin, "bbox.ymin": ymin, "bbox.xmax": xmax, "bbox.ymax": ymax,
        "srid": "EPSG_4326"})
    rows = []
    for g in data.get("gauges") or []:
        status = g.get("status") or {}
        row = {"lid": g.get("lid"), "name": g.get("name"), "state": (g.get("state") or {}).get("abbreviation")
               if isinstance(g.get("state"), dict) else g.get("state"),
               "lon": number(g.get("longitude")), "lat": number(g.get("latitude")),
               **_reading(status.get("observed")),
               **_reading(status.get("forecast"), "forecast_")}
        if flooding_only and row["flood_category"] in _NOT_FLOODING:
            continue
        rows.append(row)
    df = frame(rows, _GAUGE_COLUMNS)
    df.attrs["flooding_now"] = sum(1 for r in rows if r["flood_category"] not in _NOT_FLOODING)
    return df


def gauge(lid: str) -> dict[str, Any]:
    """One gauge in full: where it is, its latest stage, and its flood stages.

    Args:
        lid: The gauge's NWS location id, e.g. ``"BBST2"`` (the ``lid``
            column of :func:`gauges`).

    Returns:
        A dict with ``lid``, ``usgs_id``, ``name``, ``state``, ``county``,
        ``lon``, ``lat``, ``in_service``, the latest ``stage`` (and its
        unit, flow, category and time), the ``forecast_*`` crest, and
        ``flood_stages``: the stage at which the gauge reaches each category
        (``action``, ``minor``, ``moderate``, ``major``), ``None`` where the
        NWS has not set one.

    Raises:
        NOAAError: If the gauge is unknown or the service cannot be reached.

    Example::

        from georest.noaa import nwps

        g = nwps.gauge("BBST2")
        g["stage"], g["flood_stages"]["minor"]
    """
    g = fetch_json(f"{API_URL}/gauges/{lid}")
    status = g.get("status") or {}
    categories = ((g.get("flood") or {}).get("categories") or {})
    state = g.get("state")
    county = g.get("county")
    return {
        "lid": g.get("lid"), "usgs_id": g.get("usgsId") or None, "name": g.get("name"),
        "state": state.get("abbreviation") if isinstance(state, dict) else state,
        "county": county.get("name") if isinstance(county, dict) else county,
        "lon": number(g.get("longitude")), "lat": number(g.get("latitude")),
        "in_service": g.get("inService", {}).get("enabled")
        if isinstance(g.get("inService"), dict) else g.get("inService"),
        **_reading(status.get("observed")),
        **_reading(status.get("forecast"), "forecast_"),
        "flood_stages": {name: number((categories.get(name) or {}).get("stage"), missing=-999)
                         for name in ("action", "minor", "moderate", "major")},
    }
