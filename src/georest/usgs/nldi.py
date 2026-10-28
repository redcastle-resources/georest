"""Client for the USGS Hydro Network-Linked Data Index (NLDI).

The NLDI ties things that sit on rivers (stream gages, water-quality sites,
dams, pour points and more) to the NHDPlus river network, and lets you walk
that network: upstream or downstream from any point, along the main stem or
into every tributary, listing the flowlines or the sites you pass. It also
delineates the basin that drains to a point.

    https://api.water.usgs.gov/nldi/linked-data

A typical question takes two calls: find the river reach at a point, then
navigate from it::

    from georest.usgs import nldi

    reach = nldi.find_flowline(-111.596, 36.866)     # the NHDPlus reach (COMID)
    gages = nldi.sites_along("comid", reach["comid"], "upstream_tributaries",
                             distance_km=100)          # a DataFrame of gages

Everything that starts from a feature takes a ``source`` and an
``identifier``: ``("comid", 18267741)`` for a river reach,
``("nwissite", "USGS-09380000")`` for a USGS gage, and so on;
:func:`list_sources` lists them all. Navigation modes accept the friendly
names (``"upstream"``, ``"upstream_tributaries"``, ``"downstream"``,
``"downstream_diversions"``) or NLDI's codes (``UM``, ``UT``, ``DM``,
``DD``).

Tables come back as pandas DataFrames, with point coordinates as ``lon`` and
``lat``. Flowlines and basins are shapes, so they come back as GeoJSON.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ._frames import features_frame, frame
from ._http import fetch_json

BASE_URL = "https://api.water.usgs.gov/nldi/linked-data"

#: Navigation modes, by friendly name, onto NLDI's codes.
MODES = {
    "upstream": "UM",
    "upstream_main": "UM",
    "upstream_tributaries": "UT",
    "downstream": "DM",
    "downstream_main": "DM",
    "downstream_diversions": "DD",
}

#: Columns every sites table carries, even when nothing was found.
_SITE_COLUMNS = ("identifier", "name", "source", "comid", "uri", "lon", "lat")


def _mode(mode: str) -> str:
    code = MODES.get(str(mode).lower(), str(mode).upper())
    if code not in ("UM", "UT", "DM", "DD"):
        raise ValueError(
            f"unknown navigation mode {mode!r}; use one of {sorted(MODES)} "
            f"or UM, UT, DM, DD"
        )
    return code


def _feature_path(source: str, identifier: str | int) -> str:
    if not source or identifier in (None, ""):
        raise ValueError("pass both a source (e.g. 'comid', 'nwissite') and an identifier")
    return f"{BASE_URL}/{urllib.parse.quote(str(source))}/{urllib.parse.quote(str(identifier))}"


def _distance(distance_km: float) -> str:
    if distance_km is None or float(distance_km) <= 0:
        raise ValueError(
            "distance_km must be a positive number of kilometers. NLDI walks "
            "the whole network without one, which can return the entire "
            "Mississippi basin"
        )
    return f"{float(distance_km):g}"


def list_sources() -> Any:
    """Every kind of feature the NLDI links to the river network.

    Returns:
        ``pandas.DataFrame`` with ``source`` (the name to pass as ``source=``
        or ``data_source=``), ``name`` and ``url``.

    Example::

        from georest.usgs import nldi

        nldi.list_sources()   # nwissite (USGS gages), WQP, ref_dams, huc12pp, ...
    """
    rows = fetch_json(BASE_URL, {"f": "json"})
    return frame(({"source": r.get("source"), "name": r.get("sourceName"),
                   "url": r.get("features")} for r in rows or []),
                 ("source", "name", "url"))


def find_flowline(lon: float, lat: float) -> dict:
    """The NHDPlus river reach nearest a point.

    Args:
        lon, lat: The point, in decimal degrees.

    Returns:
        The reach as a GeoJSON Feature: its ``comid`` (pass it on as
        ``("comid", reach["comid"])``) and the other properties at the top
        level of the dict, and the reach's line under ``geometry``.

    Example::

        from georest.usgs import nldi

        reach = nldi.find_flowline(-111.596, 36.866)
        reach["comid"]   # an NHDPlus COMID, e.g. 18267741
    """
    fc = fetch_json(f"{BASE_URL}/comid/position",
                    {"coords": f"POINT({float(lon)} {float(lat)})"})
    features = (fc or {}).get("features") or []
    if not features:
        raise ValueError(f"no NHDPlus flowline found near ({lon}, {lat})")
    feature = features[0]
    props = dict(feature.get("properties") or {})
    props["comid"] = int(props.get("comid") or props.get("identifier"))
    return {**props, "geometry": feature.get("geometry")}


def get_feature(source: str, identifier: str | int) -> dict:
    """One linked feature, such as a USGS gage, with where it joins the network.

    Returns:
        The feature's properties (``name``, ``comid`` of the reach it sits
        on, ``uri``, ...) plus ``lon`` and ``lat``.

    Example::

        from georest.usgs import nldi

        gage = nldi.get_feature("nwissite", "USGS-09380000")
        gage["name"], gage["comid"]   # 'COLORADO RIVER AT LEES FERRY, AZ', 20733845
    """
    fc = fetch_json(_feature_path(source, identifier))
    features = (fc or {}).get("features") or []
    if not features:
        raise ValueError(f"NLDI has no {source} feature {identifier!r}")
    row = features_frame({"features": features[:1]}).iloc[0].to_dict()
    return row


def flowlines(source: str, identifier: str | int, mode: str = "upstream", *,
              distance_km: float = 50) -> dict:
    """The river reaches you pass navigating from a feature.

    Args:
        source, identifier: Where to start, e.g. ``("comid", 18267741)`` or
            ``("nwissite", "USGS-09380000")``.
        mode: ``"upstream"`` (main stem), ``"upstream_tributaries"``,
            ``"downstream"`` or ``"downstream_diversions"``.
        distance_km: How far to go. Required: without it NLDI walks the
            whole network.

    Returns:
        A GeoJSON FeatureCollection of LineStrings, one per NHDPlus reach,
        each with its ``nhdplus_comid``.

    Example::

        from georest.usgs import nldi

        river = nldi.flowlines("nwissite", "USGS-09380000", "upstream", distance_km=25)
        len(river["features"])   # reaches within 25 km upstream of Lees Ferry
    """
    return fetch_json(f"{_feature_path(source, identifier)}/navigation/{_mode(mode)}/flowlines",
                      {"distance": _distance(distance_km)})


def sites_along(source: str, identifier: str | int, mode: str = "upstream", *,
                data_source: str = "nwissite", distance_km: float = 50) -> Any:
    """The linked sites you pass navigating from a feature.

    Args:
        source, identifier: Where to start.
        mode: ``"upstream"``, ``"upstream_tributaries"``, ``"downstream"`` or
            ``"downstream_diversions"``.
        data_source: Which sites to list: ``"nwissite"`` (USGS stream gages,
            the default), ``"WQP"`` (Water Quality Portal sites),
            ``"ref_dams"``, ``"huc12pp"``, ...; see :func:`list_sources`.
        distance_km: How far to go. Required: without it NLDI walks the
            whole network.

    Returns:
        ``pandas.DataFrame``, one row per site: ``identifier``, ``name``,
        ``comid``, ``uri`` and the rest of NLDI's properties, plus ``lon``
        and ``lat``.

    Example::

        from georest.usgs import nldi

        # Every USGS gage on the river and its tributaries within 100 km
        # upstream of Lees Ferry
        gages = nldi.sites_along("nwissite", "USGS-09380000", "upstream_tributaries",
                                 distance_km=100)
        gages[["identifier", "name"]]
    """
    fc = fetch_json(
        f"{_feature_path(source, identifier)}/navigation/{_mode(mode)}/{urllib.parse.quote(data_source)}",
        {"distance": _distance(distance_km)})
    return features_frame(fc, _SITE_COLUMNS)


def basin(source: str, identifier: str | int, *, simplified: bool = True,
          split_catchment: bool = False) -> dict:
    """The drainage basin upstream of a feature.

    Args:
        source, identifier: The outlet, e.g. ``("nwissite", "USGS-09380000")``.
        simplified: Return a lighter, simplified outline (the default). Full
            detail can run to megabytes for a large basin.
        split_catchment: Split the outlet's own catchment at the exact point
            instead of including all of it. Slower; matters for small basins.

    Returns:
        A GeoJSON FeatureCollection holding one (Multi)Polygon.

    Example::

        from georest.usgs import nldi

        drains_to_lees_ferry = nldi.basin("nwissite", "USGS-09380000")
        drains_to_lees_ferry["features"][0]["geometry"]["type"]   # 'MultiPolygon'
    """
    return fetch_json(f"{_feature_path(source, identifier)}/basin",
                      {"simplified": str(bool(simplified)).lower(),
                       "splitCatchment": str(bool(split_catchment)).lower()})
