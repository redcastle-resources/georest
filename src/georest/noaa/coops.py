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
    """Every CO-OPS station of a kind.

    Args:
        kind: ``"waterlevels"`` (the default: stations that measure water
            level) or another kind the CO-OPS metadata API knows, such as
            ``"tidepredictions"``.

    Returns:
        A DataFrame with ``station``, ``name``, ``state``, ``lon``,
        ``lat``, ``tidal`` and ``great_lakes``. Cached for the life of the
        process.

    Example::

        from georest.noaa import coops

        tx = coops.stations()
        tx[tx["state"] == "TX"][["station", "name"]]
    """
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
    """The CO-OPS stations nearest a point.

    Args:
        lon, lat: The point, in decimal degrees.
        n: How many stations, nearest first.
        kind: As for :func:`stations`.
        max_km: Drop stations farther than this (``None`` keeps all): a tide
            gauge 300 km away says nothing about the water at the point.

    Returns:
        A DataFrame like :func:`stations`, plus ``distance_km``. No rows
        means no station within ``max_km``.

    Example::

        from georest.noaa import coops

        coops.nearest(-94.79, 29.31)[["station", "name", "distance_km"]]
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
    text = str(value).strip().replace("-", "")
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        raise ValueError(f"{value!r} is not a date (use 'YYYY-MM-DD' or 'YYYYMMDD')") from None
    return text


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

    Raises:
        NOAAError: If CO-OPS refuses the request (an unknown station, or no
            data for that datum) or cannot be reached.

    Example::

        from georest.noaa import coops

        df = coops.water_levels("8771450", hours=6)
        df.tail(3), df.attrs["datum"]
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

    Raises:
        ValueError: For a date that is not ``YYYY-MM-DD``-like.
        NOAAError: If CO-OPS refuses the request or cannot be reached.

    Example::

        from datetime import date, timedelta

        from georest.noaa import coops

        today = date.today()
        coops.predictions("8771450", today, today + timedelta(days=2))
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
