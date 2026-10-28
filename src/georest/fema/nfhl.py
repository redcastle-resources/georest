"""Client for FEMA's National Flood Hazard Layer (NFHL).

The NFHL is FEMA's database of the *effective* Flood Insurance Rate Maps -
the ones legally in force for flood insurance and floodplain permitting -
served live from FEMA's own map service:

    https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer

::

    from georest.fema import nfhl

    nfhl.flood_zone(-95.3698, 29.7604)
    # {'zone': 'X', 'subtype': 'AREA OF MINIMAL FLOOD HAZARD', 'sfha': False,
    #  'mapped': True, ...}

    nfhl.map_dates(-80.1300, 25.7907)
    # {'panel': '12086C0317L', 'effective': date(2009, 9, 11), 'revised': None,
    #  'pending_effective': date(2027, 2, 19), ...}

Two things the zone alone does not tell you, and :func:`map_dates` does:

* **Effective is not recent.** A map is in force from its effective date
  until FEMA replaces it, and many are decades old. Downtown Houston's
  panels took effect in 2007-2017; Miami Beach's in 2009.
* **New maps may be coming.** FEMA publishes *preliminary* maps (proposed)
  and *pending* maps (adopted, with an effective date) on separate
  services. Miami-Dade's new maps take effect on 19 February 2027; a zone
  read today is the old map's.

A flood zone is a regulatory designation, not a forecast: being outside
the mapped floodplain is not a statement that a place will not flood.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ._http import fetch_json

NFHL_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
PRELIMINARY_URL = ("https://hazards.fema.gov/arcgis/rest/services/PrelimPending/"
                   "Prelim_NFHL/MapServer")
PENDING_URL = ("https://hazards.fema.gov/arcgis/rest/services/PrelimPending/"
               "Pending_NFHL/MapServer")

#: NFHL layer ids, as published by the service.
AVAILABILITY_LAYER = 0
LOMR_LAYER = 1
PANEL_LAYER = 3
ZONE_LAYER = 28

_ZONE_FIELDS = ("FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DEPTH,LEN_UNIT,V_DATUM,"
                "DFIRM_ID")

#: FEMA's "no value" for numeric fields such as STATIC_BFE and DEPTH.
_NO_VALUE = -9999


def _check_point(lon: float, lat: float) -> tuple[float, float]:
    lon, lat = float(lon), float(lat)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(
            f"({lon}, {lat}) is not a longitude, latitude pair in degrees "
            f"(longitude first, -180 to 180; latitude -90 to 90)"
        )
    return lon, lat


def _at_point(url: str, layer: int, lon: float, lat: float, fields: str) -> list[dict]:
    """The attributes of every feature in ``layer`` under the point."""
    data = fetch_json(f"{url}/{layer}/query", {
        "f": "json", "where": "1=1",
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
        "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "outFields": fields, "returnGeometry": "false",
    })
    features = data.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"FEMA's service answered without a feature list: {url}/{layer}")
    return [f.get("attributes") or {} for f in features]


def _day(ms: Any) -> date | None:
    """An ArcGIS date (milliseconds since the epoch, UTC) as a date."""
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value == _NO_VALUE else value


def flood_zone(lon: float, lat: float) -> dict[str, Any]:
    """The FEMA flood zone at a point, from the effective map.

    Args:
        lon, lat: The point, in decimal degrees.

    Returns:
        A dict with:

        * ``zone``: FEMA's zone code (``"AE"``, ``"X"``, ``"VE"``, ...), or
          ``None`` where no zone polygon covers the point.
        * ``subtype``: FEMA's zone subtype, e.g. ``"FLOODWAY"`` or
          ``"0.2 PCT ANNUAL CHANCE FLOOD HAZARD"`` (the shaded X zone).
        * ``sfha``: whether the point is in the Special Flood Hazard Area,
          the 1%-annual-chance floodplain that triggers insurance
          requirements.
        * ``static_bfe``, ``depth``: the base flood elevation or flood
          depth where FEMA records one, in ``units``; ``None`` otherwise.
        * ``units``, ``datum``: as FEMA records them (e.g. ``"Feet"``,
          ``"NAVD88"``).
        * ``dfirm_id``: the flood study the zone comes from.
        * ``mapped``: whether FEMA has digital flood map data at the
          point. ``False`` means FEMA has no digital map there - not that
          there is no flood hazard.
        * ``zones``: every zone polygon under the point, when more than
          one does (a point on a boundary); the first is reported above.

    Raises:
        RuntimeError: If FEMA's service fails or answers with an error.
            A failed query is never reported as "no zone".

    Example::

        from georest.fema import nfhl

        nfhl.flood_zone(-95.3698, 29.7604)["zone"]
    """
    lon, lat = _check_point(lon, lat)
    rows = _at_point(NFHL_URL, ZONE_LAYER, lon, lat, _ZONE_FIELDS)
    rows = [r for r in rows if (r.get("FLD_ZONE") or "") != "OPEN WATER"] or rows
    out: dict[str, Any] = {"lon": lon, "lat": lat, "zone": None, "subtype": None,
                           "sfha": False, "static_bfe": None, "depth": None,
                           "units": None, "datum": None, "dfirm_id": None,
                           "mapped": bool(rows), "zones": []}
    if not rows:
        # No zone polygon: either FEMA has no digital map here, or it does
        # and this point falls in a gap. The availability layer tells which.
        available = _at_point(NFHL_URL, AVAILABILITY_LAYER, lon, lat, "*")
        out["mapped"] = bool(available)
        return out
    first = rows[0]
    out.update({
        "zone": first.get("FLD_ZONE"),
        "subtype": first.get("ZONE_SUBTY"),
        "sfha": str(first.get("SFHA_TF") or "").upper() == "T",
        "static_bfe": _number(first.get("STATIC_BFE")),
        "depth": _number(first.get("DEPTH")),
        "units": first.get("LEN_UNIT"),
        "datum": first.get("V_DATUM"),
        "dfirm_id": first.get("DFIRM_ID"),
    })
    if len(rows) > 1:
        out["zones"] = [{"zone": r.get("FLD_ZONE"), "subtype": r.get("ZONE_SUBTY")}
                        for r in rows]
    return out


def map_dates(lon: float, lat: float) -> dict[str, Any]:
    """When FEMA's flood map at a point took effect, and what is coming.

    Four point queries on FEMA's own services: the effective map panel,
    any Letter of Map Revision (LOMR - an official amendment to the map)
    covering the point, and FEMA's pending and preliminary map services.

    Args:
        lon, lat: The point, in decimal degrees.

    Returns:
        A dict with:

        * ``panel``: the effective map panel's number, e.g.
          ``"12086C0317L"``, or ``None`` where FEMA has no panel.
        * ``effective``: the date that panel took effect.
        * ``revised``: the effective date of the latest LOMR at the point
          made after the panel, or ``None``.
        * ``pending_effective``: the date adopted new maps take effect, or
          ``None``. A date in the past means FEMA still lists them as
          pending, and its effective layer may not show them yet.
        * ``preliminary_issued``: the date FEMA issued preliminary
          (proposed, not yet adopted) maps for the area, or ``None``.
        * ``unchecked``: the names of any of the four lookups that failed.
          A value of ``None`` for a lookup named here means "could not
          check", not "none".

    Example::

        from georest.fema import nfhl

        d = nfhl.map_dates(-80.1300, 25.7907)
        d["effective"], d["pending_effective"]
    """
    lon, lat = _check_point(lon, lat)
    lookups = {
        "panel": (NFHL_URL, PANEL_LAYER, "FIRM_PAN,EFF_DATE"),
        "revised": (NFHL_URL, LOMR_LAYER, "CASE_NO,EFF_DATE,STATUS"),
        "pending_effective": (PENDING_URL, 0, "DFIRM_ID,EFF_DATE"),
        "preliminary_issued": (PRELIMINARY_URL, 0, "DFIRM_ID,PRELM_ISSUE_DATE"),
    }
    got: dict[str, list[dict] | None] = {}
    for name, (url, layer, fields) in lookups.items():
        try:
            got[name] = _at_point(url, layer, lon, lat, fields)
        except (RuntimeError, ValueError):
            got[name] = None

    out: dict[str, Any] = {"lon": lon, "lat": lat, "panel": None, "effective": None,
                           "revised": None, "pending_effective": None,
                           "preliminary_issued": None,
                           "unchecked": [k for k, v in got.items() if v is None]}
    panels = sorted(((r.get("FIRM_PAN"), _day(r.get("EFF_DATE")))
                     for r in got["panel"] or [] if _day(r.get("EFF_DATE"))),
                    key=lambda p: p[1])
    if panels:
        out["panel"], out["effective"] = panels[-1]
    revisions = sorted(
        d for d in (_day(r.get("EFF_DATE")) for r in got["revised"] or []
                    if str(r.get("STATUS") or "Effective").lower().startswith("effective"))
        if d and (out["effective"] is None or d > out["effective"]))
    if revisions:
        out["revised"] = revisions[-1]
    out["pending_effective"] = max(
        (d for d in (_day(r.get("EFF_DATE")) for r in got["pending_effective"] or []) if d),
        default=None)
    out["preliminary_issued"] = max(
        (d for d in (_day(r.get("PRELM_ISSUE_DATE"))
                     for r in got["preliminary_issued"] or []) if d),
        default=None)
    return out
