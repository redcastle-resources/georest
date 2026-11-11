"""Wildfire perimeters by fire name, from the National Interagency Fire Center.

Two NIFC services hold the official interagency perimeters:

* **WFIGS Interagency Fire Perimeters** - every incident reported
  through IRWIN since 2016, with its acres and its discovery, containment
  and fire-out dates. A fire usually has several perimeters here (daily
  ones while it burns, then a final one); :func:`find_fires` keeps one per
  incident, preferring the final perimeter.
* **InterAgency Fire Perimeter History** - the long record, back to the
  1800s in places, with a year but no dates. It has gaps (the 2020
  Cameron Peak Fire is not in it), so it is a fallback, not a source of
  truth for recent fires.

Both are searched and merged; WFIGS wins for the same fire.

A fire's name is not unique: "Dixie" names a 963,000-acre California
fire in 2021 and a 44,000-acre Idaho fire the same summer. Give ``year``
and ``state`` when you know them. :func:`fire_perimeter` returns the
largest matching fire and lists the others it passed over in
``candidates``, so a caller can say which one it used.

Example::

    from georest.nifc import perimeters

    perimeters.find_fires("Dixie", year=2021)
    fc = perimeters.fire_perimeter("Monroe Canyon", year=2025, state="UT")
    fc["features"][0]["properties"]["discovered"]   # '2025-07-13'

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..esri._http import fetch_json

_BASE = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services"
#: Every IRWIN-reported incident since 2016, with dates.
WFIGS_URL = f"{_BASE}/WFIGS_Interagency_Perimeters/FeatureServer/0"
#: The long historical record: a year, no dates.
HISTORY_URL = f"{_BASE}/InterAgencyFirePerimeterHistory_All_Years_View/FeatureServer/0"
#: The first year WFIGS holds.
WFIGS_FIRST_YEAR = 2016

_WFIGS_FIELDS = ("OBJECTID,poly_IncidentName,poly_GISAcres,poly_FeatureCategory,"
                 "attr_IncidentSize,attr_FireDiscoveryDateTime,attr_ContainmentDateTime,"
                 "attr_FireOutDateTime,attr_POOState,attr_IrwinID,poly_IRWINID")
_HISTORY_FIELDS = "OBJECTID,INCIDENT,GIS_ACRES,FIRE_YEAR_INT,UNIT_ID,IRWINID,AGENCY,SOURCE"
_FINAL = "Wildfire Final Fire Perimeter"

_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN",
    "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR",
}


def _state_code(state: str | None) -> str | None:
    """``"UT"``, ``"Utah"`` or ``"US-UT"`` as ``"UT"``; None for none."""
    if not state:
        return None
    s = str(state).strip().upper()
    if s.startswith("US-"):
        s = s[3:]
    if len(s) == 2 and s.isalpha():
        return s
    if s in _STATES:
        return _STATES[s]
    raise ValueError(f"unknown state {state!r}: give a two-letter code such as 'UT'")


def _clean_name(name: str) -> str:
    """The incident name as NIFC stores it: no trailing "Fire", no quotes
    that could end the SQL literal early."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    n = re.sub(r"(?i)\s+fire$", "", n)       # "Cameron Peak Fire" is "Cameron Peak"
    if not n:
        raise ValueError("give a fire name, such as 'Cameron Peak'")
    return n.replace("'", "''")


def _date(ms) -> str | None:
    if ms in (None, "", 0):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def _query(url: str, where: str, fields: str, timeout: int | None) -> list[dict]:
    data = fetch_json(f"{url}/query", {"where": where, "outFields": fields,
                                       "returnGeometry": "false", "f": "json"}, timeout=timeout)
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(f"NIFC query failed: {data['error'].get('message') or data['error']}")
    return [f.get("attributes") or {} for f in (data or {}).get("features") or []]


def _wfigs(name: str, year: int | None, timeout: int | None, exact: bool) -> list[dict]:
    cond = (f"UPPER(poly_IncidentName) = UPPER('{name}')" if exact
            else f"UPPER(poly_IncidentName) LIKE UPPER('%{name}%')")
    if year is not None:
        cond += (f" AND attr_FireDiscoveryDateTime >= DATE '{year}-01-01'"
                 f" AND attr_FireDiscoveryDateTime < DATE '{year + 1}-01-01'")
    out = []
    for a in _query(WFIGS_URL, cond, _WFIGS_FIELDS, timeout):
        discovered = _date(a.get("attr_FireDiscoveryDateTime"))
        st = a.get("attr_POOState") or ""
        out.append({
            "name": a.get("poly_IncidentName"),
            "year": int(discovered[:4]) if discovered else None,
            "state": st[3:] if st.startswith("US-") else (st or None),
            "acres": a.get("poly_GISAcres") or a.get("attr_IncidentSize"),
            "discovered": discovered,
            "contained": _date(a.get("attr_ContainmentDateTime")),
            "fire_out": _date(a.get("attr_FireOutDateTime")),
            "final": a.get("poly_FeatureCategory") == _FINAL,
            "category": a.get("poly_FeatureCategory"),
            "irwin_id": (a.get("attr_IrwinID") or a.get("poly_IRWINID") or "").strip("{}").upper() or None,
            "source": "WFIGS Interagency Fire Perimeters (NIFC)",
            "source_url": WFIGS_URL,
            "object_id": a.get("OBJECTID"),
        })
    return out


def _history(name: str, year: int | None, timeout: int | None, exact: bool) -> list[dict]:
    cond = (f"UPPER(INCIDENT) = UPPER('{name}')" if exact
            else f"UPPER(INCIDENT) LIKE UPPER('%{name}%')")
    if year is not None:
        cond += f" AND FIRE_YEAR_INT = {int(year)}"
    out = []
    for a in _query(HISTORY_URL, cond, _HISTORY_FIELDS, timeout):
        unit = (a.get("UNIT_ID") or "").strip()
        out.append({
            "name": a.get("INCIDENT"),
            "year": a.get("FIRE_YEAR_INT"),
            # A unit id starts with its state: "COARF" is Colorado.
            "state": unit[:2].upper() if len(unit) >= 2 and unit[:2].isalpha() else None,
            "acres": a.get("GIS_ACRES"),
            "discovered": None, "contained": None, "fire_out": None,
            "final": True,
            "category": "Fire perimeter history",
            "irwin_id": (a.get("IRWINID") or "").strip("{}").upper() or None,
            "source": "InterAgency Fire Perimeter History (NIFC)",
            "source_url": HISTORY_URL,
            "object_id": a.get("OBJECTID"),
        })
    return out


def _one_per_fire(rows: list[dict]) -> list[dict]:
    """One row per incident: WFIGS over history, a final perimeter over a
    daily one, then the largest."""
    def rank(r):
        return (r["source_url"] == WFIGS_URL, r["final"], r["acres"] or 0)

    best: dict[Any, dict] = {}
    for r in rows:
        key = r["irwin_id"] or (str(r["name"]).upper(), r["year"], r["state"])
        if key not in best or rank(r) > rank(best[key]):
            best[key] = r
    # History rows lack an IRWIN id more often than not: drop one that
    # repeats a WFIGS fire of the same name, year and state.
    wfigs = {(str(r["name"]).upper(), r["year"], r["state"]) for r in best.values()
             if r["source_url"] == WFIGS_URL}
    kept = [r for r in best.values() if r["source_url"] == WFIGS_URL
            or (str(r["name"]).upper(), r["year"], r["state"]) not in wfigs]
    kept.sort(key=lambda r: (r["acres"] or 0), reverse=True)
    # The history service can hold one fire twice (one copy without a
    # unit id, so without a state): same name and year, acres within 5%.
    out: list[dict] = []
    for r in kept:
        same = next((o for o in out if o["year"] == r["year"]
                     and str(o["name"]).upper() == str(r["name"]).upper()
                     and (r["state"] is None or o["state"] is None or o["state"] == r["state"])
                     and abs((o["acres"] or 0) - (r["acres"] or 0))
                     <= 0.05 * max(o["acres"] or 0, 1)), None)
        if same is None:
            out.append(r)
        elif same["state"] is None:
            same["state"] = r["state"]       # keep whichever copy knew it
    return out


def find_fires(name: str, year: int | None = None, state: str | None = None,
               min_acres: float = 0, timeout: int | None = None) -> list[dict]:
    """Fires with this name, one row each, largest first. No geometry.

    Args:
        name (str): The incident name, such as ``"Cameron Peak"``. A
            trailing "Fire" is ignored. An exact (case-insensitive) match
            is tried first; when nothing matches, any name containing it.
        year (int, optional): The year the fire was discovered.
        state (str, optional): Where it started: ``"UT"``, ``"Utah"``.
        min_acres (float, optional): Leave out fires smaller than this.
        timeout (int, optional): Seconds per request.

    Returns:
        list[dict]: Each with ``name``, ``year``, ``state``, ``acres``,
        ``discovered``, ``contained`` and ``fire_out`` (ISO dates, None
        where NIFC has none: always for the history service), ``final``
        (a final perimeter rather than a daily one), ``category``,
        ``irwin_id``, ``source``, ``source_url`` and ``object_id``.
    """
    n = _clean_name(name)
    st = _state_code(state)
    y = int(year) if year is not None else None
    rows: list[dict] = []
    for exact in (True, False):
        if y is None or y >= WFIGS_FIRST_YEAR:
            rows += _wfigs(n, y, timeout, exact)
        rows += _history(n, y, timeout, exact)
        if st:
            rows = [r for r in rows if r["state"] == st]
        rows = [r for r in rows if (r["acres"] or 0) >= min_acres]
        if rows:
            break
    return _one_per_fire(rows)


def fire_perimeter(name: str, year: int | None = None, state: str | None = None,
                   min_acres: float = 0, max_offset_m: float | None = 10,
                   timeout: int | None = None) -> dict:
    """The perimeter of a fire, as a GeoJSON FeatureCollection in WGS84.

    Picks the largest fire :func:`find_fires` returns. The feature's
    properties are that fire's row (name, year, state, acres, discovered,
    contained, fire_out, source...). The collection also carries
    ``candidates``: the other fires that matched, largest first, so you
    can say which one this is when the name is shared.

    Args:
        name, year, state, min_acres, timeout: As :func:`find_fires`.
        max_offset_m (float, optional): Generalize the outline by up to
            this many meters (the service's ``maxAllowableOffset``).
            Default 10: a large fire's exact outline runs to megabytes
            (the 2025 Monroe Canyon Fire's is 1.3 MB), which every
            request that uses it must carry, and 10 m is under one
            Landsat or Sentinel-2 pixel. ``None`` returns it exact.

    Returns:
        dict: ``{"type": "FeatureCollection", "features": [one feature],
        "candidates": [...], "source": ..., "source_url": ...}``.

    Raises:
        LookupError: No fire matches.
    """
    fires = find_fires(name, year=year, state=state, min_acres=min_acres, timeout=timeout)
    if not fires:
        where = ", ".join(x for x in (f"in {year}" if year else "", f"in {state}" if state else "") if x)
        raise LookupError(f"NIFC has no fire named {name!r}{' ' + where if where else ''}")
    fire = fires[0]
    params = {"where": f"OBJECTID = {int(fire['object_id'])}", "outFields": "OBJECTID",
              "returnGeometry": "true", "outSR": "4326", "f": "geojson"}
    if max_offset_m:
        # Degrees, since the output is WGS84: a meter of latitude.
        params["maxAllowableOffset"] = repr(float(max_offset_m) / 111_320)
        params["geometryPrecision"] = "6"
    data = fetch_json(f"{fire['source_url']}/query", params, timeout=timeout)
    feats = (data or {}).get("features") or []
    if not feats or not feats[0].get("geometry"):
        raise LookupError(f"NIFC returned no geometry for {fire['name']!r} ({fire['source']})")
    props = dict(fire, generalized_m=max_offset_m or 0)
    feature = {"type": "Feature", "geometry": feats[0]["geometry"], "properties": props}
    return {"type": "FeatureCollection", "features": [feature], "candidates": fires[1:],
            "source": fire["source"], "source_url": fire["source_url"]}
