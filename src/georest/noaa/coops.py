"""Client for NOAA CO-OPS Tides and Currents (api.tidesandcurrents.noaa.gov).

Water levels measured at NOAA's tide stations, and the tide predictions
for them::

    from georest.noaa import coops

    coops.nearest(-94.79, 29.31)                       # stations near Galveston
    coops.water_levels("8771450", hours=24)            # measured, oldest first
    coops.predictions("8771450", "2026-11-01", "2026-11-03")   # high and low tides

What the raw API does that this module absorbs:

* **Errors under HTTP 200.** A bad station or product answers 200 with
  ``{"error": {"message": ...}}``; that is raised, not returned as no data.
* **The datum is the meaning.** A water level is a height above a datum
  (mean lower low water by default); the same reading against NAVD88 is a
  different number. The datum asked for is kept in ``df.attrs["datum"]``
  and the unit is in the column name.
* **Measured is not verified.** Each reading carries ``quality``: ``"p"``
  preliminary or ``"v"`` verified. Predictions are modelled, not measured.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ._frames import check_point, frame, km_between, number
from ._http import NOAAError, fetch_json

DATA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATIONS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"

_STATION_COLUMNS = ("station", "name", "state", "lon", "lat", "tidal", "great_lakes")
_STATIONS_CACHE: dict[str, list[dict]] = {}


def stations(kind: str = "waterlevels") -> Any:
    """Every CO-OPS station of a kind (``"waterlevels"``, the default, or
    another kind the metadata API knows, such as ``"tidepredictions"``).
    Cached for the life of the process."""
    if kind not in _STATIONS_CACHE:
        data = fetch_json(STATIONS_URL, {"type": kind})
        _STATIONS_CACHE[kind] = [{
            "station": s.get("id"), "name": s.get("name"), "state": s.get("state") or None,
            "lon": number(s.get("lng")), "lat": number(s.get("lat")),
            "tidal": s.get("tidal"), "great_lakes": s.get("greatlakes"),
        } for s in data.get("stations") or []]
    return frame(_STATIONS_CACHE[kind], _STATION_COLUMNS)


def nearest(lon: float, lat: float, *, n: int = 3, kind: str = "waterlevels",
            max_km: float | None = 100) -> Any:
    """The ``n`` CO-OPS stations nearest a point, with ``distance_km``.

    ``max_km`` drops stations farther than that (``None`` keeps all): a
    tide gauge 300 km away says nothing about the water at the point.
    """
    lon, lat = check_point(lon, lat)
    rows = []
    for s in stations(kind).to_dict("records"):
        if s["lon"] is None or s["lat"] is None:
            continue
        d = km_between(lon, lat, s["lon"], s["lat"])
        if max_km is None or d <= max_km:
            rows.append({**s, "distance_km": round(d, 1)})
    rows.sort(key=lambda r: r["distance_km"])
    return frame(rows[:n], (*_STATION_COLUMNS, "distance_km"))


def _yyyymmdd(value: str | date | datetime) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")


def _get(params: dict) -> dict:
    data = fetch_json(DATA_URL, {**params, "time_zone": "gmt", "format": "json",
                                 "application": "georest"})
    if isinstance(data, dict) and data.get("error"):
        raise NOAAError(f"CO-OPS refused the request: {data['error'].get('message')}")
    return data


def water_levels(station: str, *, hours: int = 24, datum: str = "MLLW",
                 units: str = "english") -> Any:
    """Measured water levels at a station over the last ``hours`` (up to 720).

    Args:
        station: The CO-OPS station id, e.g. ``"8771450"`` (Galveston Pier 21).
        hours: How far back, from now.
        datum: The height reference: ``"MLLW"`` (the default), ``"MHHW"``,
            ``"MSL"``, ``"NAVD"``, ...
        units: ``"english"`` (feet) or ``"metric"`` (meters).

    Returns:
        A DataFrame, oldest first, with ``time`` (UTC), ``water_level_ft``
        (or ``_m``), ``quality`` (``"p"`` preliminary, ``"v"`` verified) and
        ``flags``. ``df.attrs`` holds the station's ``name``, ``lon``,
        ``lat``, the ``datum`` and the ``units``.
    """
    col = "water_level_ft" if units == "english" else "water_level_m"
    data = _get({"station": station, "product": "water_level", "datum": datum,
                 "units": units, "range": max(1, min(int(hours), 720))})
    rows = [{"time": d.get("t"), col: number(d.get("v")), "quality": d.get("q"),
             "flags": d.get("f")} for d in data.get("data") or []]
    df = frame(rows, ("time", col, "quality", "flags"))
    meta = data.get("metadata") or {}
    df.attrs.update({"station": station, "name": meta.get("name"),
                     "lon": number(meta.get("lon")), "lat": number(meta.get("lat")),
                     "datum": datum, "units": units})
    return df


def predictions(station: str, begin: str | date, end: str | date, *,
                interval: str = "hilo", datum: str = "MLLW", units: str = "english") -> Any:
    """Tide predictions for a station between two dates.

    Args:
        station: The CO-OPS station id.
        begin, end: Dates, ``"2026-11-01"``, ``"20261101"`` or a ``date``.
        interval: ``"hilo"`` (high and low tides, the default), ``"h"``
            (hourly) or minutes such as ``"6"``.
        datum, units: As for :func:`water_levels`.

    Returns:
        A DataFrame with ``time`` (UTC), ``prediction_ft`` (or ``_m``) and,
        for ``"hilo"``, ``type``: ``"H"`` or ``"L"``. These are modelled
        astronomical tides; weather can push the water well above them.
    """
    col = "prediction_ft" if units == "english" else "prediction_m"
    data = _get({"station": station, "product": "predictions", "datum": datum,
                 "units": units, "interval": interval,
                 "begin_date": _yyyymmdd(begin), "end_date": _yyyymmdd(end)})
    rows = [{"time": d.get("t"), col: number(d.get("v")), "type": d.get("type")}
            for d in data.get("predictions") or []]
    df = frame(rows, ("time", col, "type"))
    df.attrs.update({"station": station, "datum": datum, "units": units})
    return df
