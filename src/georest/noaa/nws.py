"""Client for the National Weather Service API (api.weather.gov).

Active watches, warnings and advisories; the forecast at a point; and the
latest observations from the stations nearest a point::

    from georest.noaa import nws

    nws.alerts(point=(-95.37, 29.76))          # every alert in effect there
    nws.alerts(area="TX", severity="Severe")
    nws.forecast(-95.37, 29.76)                # one row per forecast period
    nws.observations(-95.37, 29.76)            # nearest stations, latest readings

Three things the raw API does that this module absorbs:

* **Two unit systems in one API.** Observations come back in SI (degrees
  Celsius, km/h, pascals) and forecasts in US units (degrees Fahrenheit,
  "5 to 10 mph" as text). Side by side raw, "34.4 now, high of 100" reads
  as a 66-degree swing that is really 94 F and 100 F. Every call here
  returns one system, chosen by ``units=`` ("us" or "si"), with the unit
  in each column's name.
* **Alerts without polygons.** Many alerts are issued for forecast zones
  and carry no geometry of their own. :func:`alerts` reports
  ``has_polygon`` and the alert's ``zones`` so a map built from polygons
  alone does not silently drop them.
* **Stale "latest" observations.** A station's latest reading can be hours
  old, or stamped in the future by a skewed clock. :func:`observations`
  reports each reading's age and drops readings older than
  ``max_age_minutes``, counting what it dropped.

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

from ._frames import check_point, frame, km_between
from ._http import NOAAError, fetch_json

API_URL = "https://api.weather.gov"
_GEO = "application/geo+json"

_ALERT_COLUMNS = ("id", "event", "severity", "urgency", "certainty", "headline",
                  "area", "sender", "onset", "expires", "ends", "zones",
                  "has_polygon")


class NotCovered(NOAAError):
    """The NWS has no forecast grid at this point (it covers the US, its
    territories and nearby waters)."""


def _units(units: str) -> str:
    u = str(units).lower()
    if u not in ("us", "si"):
        raise ValueError(f"units must be 'us' or 'si', not {units!r}")
    return u


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def _alert_params(point, area, event, severity) -> dict:
    if point is not None and area is not None:
        raise ValueError("give point or area, not both")
    params: dict[str, str] = {}
    if point is not None:
        lon, lat = check_point(*point)
        params["point"] = f"{lat:.4f},{lon:.4f}"
    elif area is not None:
        params["area"] = str(area).upper()
    if event:
        params["event"] = event
    if severity:
        params["severity"] = severity
    return params


def alerts_geojson(point: tuple[float, float] | None = None, area: str | None = None,
                   *, event: str | None = None, severity: str | None = None) -> dict:
    """Active alerts as the NWS's own GeoJSON FeatureCollection, for mapping.

    Args:
        point: ``(lon, lat)``; every alert returned is in effect there.
        area: A state or marine area code (``"TX"``, ``"GM"``). Neither
            ``point`` nor ``area`` means the whole country.
        event: An event name, e.g. ``"Flood Warning"``.
        severity: ``"Extreme"``, ``"Severe"``, ``"Moderate"``, ``"Minor"``.

    Returns:
        The NWS's FeatureCollection, unchanged. A feature's geometry is
        ``None`` when the alert was issued for forecast zones rather than
        drawn as a polygon; its ``affectedZones`` property lists them, so a
        map of the polygons alone is not every alert.

    Raises:
        NOAAError: If the NWS API refuses the request or cannot be reached.

    Example::

        from georest.noaa import nws

        fc = nws.alerts_geojson(area="TX")
        drawn = [f for f in fc["features"] if f["geometry"]]
    """
    return fetch_json(f"{API_URL}/alerts/active",
                      _alert_params(point, area, event, severity), accept=_GEO)


def alerts(point: tuple[float, float] | None = None, area: str | None = None,
           *, event: str | None = None, severity: str | None = None) -> Any:
    """Active watches, warnings and advisories, one row per alert.

    Args:
        point: ``(lon, lat)``. The NWS matches each alert to the point
            itself, so every row is in effect there - whatever its ``area``
            text lists, which can be long and is easy to misread.
        area: A state or marine area code (``"TX"``, ``"GM"``).
            Neither ``point`` nor ``area`` means the whole country.
        event: An event name, e.g. ``"Flood Warning"``.
        severity: ``"Extreme"``, ``"Severe"``, ``"Moderate"``, ``"Minor"``.

    Returns:
        A DataFrame with ``event``, ``severity``, ``urgency``,
        ``certainty``, ``headline``, ``area`` (the NWS's own text),
        ``sender``, ``onset``, ``expires``, ``ends``, ``zones`` (forecast
        zone ids) and ``has_polygon``. No rows means the NWS lists no
        active alert there right now - not that there is no hazard.

    Example::

        from georest.noaa import nws

        nws.alerts(point=(-95.37, 29.76))[["event", "severity", "expires"]]
    """
    fc = alerts_geojson(point, area, event=event, severity=severity)
    rows = []
    for f in fc.get("features") or []:
        p = f.get("properties") or {}
        rows.append({
            "id": p.get("id"),
            "event": p.get("event"),
            "severity": p.get("severity"),
            "urgency": p.get("urgency"),
            "certainty": p.get("certainty"),
            "headline": p.get("headline"),
            "area": p.get("areaDesc"),
            "sender": p.get("senderName"),
            "onset": p.get("onset"),
            "expires": p.get("expires"),
            "ends": p.get("ends"),
            "zones": [z.rstrip("/").rsplit("/", 1)[-1] for z in p.get("affectedZones") or []],
            "has_polygon": bool(f.get("geometry")),
        })
    return frame(rows, _ALERT_COLUMNS)


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

def _point_meta(lon: float, lat: float) -> dict:
    """The NWS grid and links for a point; :class:`NotCovered` outside it."""
    lon, lat = check_point(lon, lat)
    try:
        data = fetch_json(f"{API_URL}/points/{lat:.4f},{lon:.4f}", accept=_GEO)
    except NOAAError as exc:
        if exc.status == 404:
            raise NotCovered(f"the NWS has no forecast grid at ({lon}, {lat})",
                             status=404) from exc
        raise
    return data.get("properties") or {}


def _c_to_f(v):
    return None if v is None else round(v * 9 / 5 + 32, 1)


def _f_to_c(v):
    return None if v is None else round((v - 32) * 5 / 9, 1)


def forecast(lon: float, lat: float, *, units: str = "us", hourly: bool = False) -> Any:
    """The NWS forecast at a point, one row per period.

    Args:
        lon, lat: The point, in decimal degrees.
        units: ``"us"`` (degrees F, mph; the default) or ``"si"``
            (degrees C, km/h). The unit is in the column names.
        hourly: The hourly forecast instead of the day/night periods.

    Returns:
        A DataFrame with ``name``, ``start``, ``end``, ``is_daytime``,
        ``temperature_f`` (or ``_c``), ``precip_pct``, ``wind_max_mph`` (or
        ``_kmh``, the upper end of the NWS's range), ``wind_text`` (as the
        NWS wrote it), ``wind_dir``, ``summary`` and ``detail``.

    Raises:
        NotCovered: Outside the NWS's forecast area.

    Example::

        from georest.noaa import nws

        nws.forecast(-95.37, 29.76)[["name", "temperature_f", "summary"]]
    """
    u = _units(units)
    meta = _point_meta(lon, lat)
    url = meta.get("forecastHourly" if hourly else "forecast")
    if not url:
        raise NotCovered(f"the NWS publishes no forecast at ({lon}, {lat})")
    periods = (fetch_json(url, accept=_GEO).get("properties") or {}).get("periods") or []
    t_col = "temperature_f" if u == "us" else "temperature_c"
    w_col = "wind_max_mph" if u == "us" else "wind_max_kmh"
    rows = []
    for p in periods:
        temp, tunit = p.get("temperature"), p.get("temperatureUnit")
        if isinstance(temp, dict):            # some responses nest the value
            temp = temp.get("value")
        if tunit == "C":
            temp_f, temp_c = _c_to_f(temp), temp
        elif tunit == "F":
            temp_f, temp_c = temp, _f_to_c(temp)
        else:
            temp_f = temp_c = None            # never guess a unit
        nums = [int(n) for n in re.findall(r"\d+", p.get("windSpeed") or "")]
        wind_mph = max(nums) if nums else None
        pop = p.get("probabilityOfPrecipitation") or {}
        rows.append({
            "name": p.get("name") or None,
            "start": p.get("startTime"),
            "end": p.get("endTime"),
            "is_daytime": p.get("isDaytime"),
            t_col: temp_f if u == "us" else temp_c,
            "precip_pct": pop.get("value"),
            w_col: (wind_mph if u == "us" or wind_mph is None
                    else round(wind_mph * 1.609344)),
            "wind_text": p.get("windSpeed"),
            "wind_dir": p.get("windDirection"),
            "summary": p.get("shortForecast"),
            "detail": p.get("detailedForecast") or None,
        })
    return frame(rows, ("name", "start", "end", "is_daytime", t_col, "precip_pct",
                        w_col, "wind_text", "wind_dir", "summary", "detail"))


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

#: NWS unit codes -> (us column suffix, us converter, si suffix, si converter).
_CONVERT = {
    "wmoUnit:degC": ("f", _c_to_f, "c", lambda v: round(v, 1)),
    "wmoUnit:km_h-1": ("mph", lambda v: round(v / 1.609344, 1), "kmh", lambda v: round(v, 1)),
    "wmoUnit:m_s-1": ("mph", lambda v: round(v * 2.236936, 1), "kmh", lambda v: round(v * 3.6, 1)),
    "wmoUnit:Pa": ("inhg", lambda v: round(v / 3386.389, 2), "hpa", lambda v: round(v / 100, 1)),
    "wmoUnit:m": ("mi", lambda v: round(v / 1609.344, 1), "km", lambda v: round(v / 1000, 1)),
    "wmoUnit:percent": ("pct", lambda v: round(v, 1), "pct", lambda v: round(v, 1)),
    "wmoUnit:degree_(angle)": ("deg", round, "deg", round),
}

#: (output name, NWS field, expected kind) - the kind fixes the column name
#: even when a reading is missing, so every row has the same columns.
_OBS_FIELDS = (
    ("temperature", "temperature", ("f", "c")),
    ("dewpoint", "dewpoint", ("f", "c")),
    ("feels_like", ("heatIndex", "windChill"), ("f", "c")),
    ("humidity", "relativeHumidity", ("pct", "pct")),
    ("wind", "windSpeed", ("mph", "kmh")),
    ("gust", "windGust", ("mph", "kmh")),
    ("wind_dir", "windDirection", ("deg", "deg")),
    ("pressure", "barometricPressure", ("inhg", "hpa")),
    ("visibility", "visibility", ("mi", "km")),
)


def _obs_columns(u: str) -> list[str]:
    i = 0 if u == "us" else 1
    return [f"{name}_{kinds[i]}" for name, _f, kinds in _OBS_FIELDS]


def _age_minutes(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((now - t).total_seconds() / 60, 1)


def _read_observation(props: dict, u: str, now: datetime) -> dict:
    i = 0 if u == "us" else 1
    row: dict[str, Any] = {"observed_at": props.get("timestamp"),
                           "age_min": _age_minutes(props.get("timestamp"), now),
                           "conditions": props.get("textDescription") or None}
    qc: dict[str, str] = {}
    for name, fields, kinds in _OBS_FIELDS:
        col = f"{name}_{kinds[i]}"
        row[col] = None
        for field in (fields if isinstance(fields, tuple) else (fields,)):
            block = props.get(field)
            if not isinstance(block, dict) or block.get("value") is None:
                continue
            conv = _CONVERT.get(block.get("unitCode"))
            if conv is None or conv[i * 2] != kinds[i]:
                qc[col] = f"unrecognized unit {block.get('unitCode')}"
                break                     # never label a number with the wrong unit
            row[col] = conv[i * 2 + 1](block["value"])
            if block.get("qualityControl") not in (None, "V"):
                qc[col] = block["qualityControl"]
            break
    row["qc"] = qc
    return row


def stations(lon: float, lat: float) -> Any:
    """The NWS observation stations for a point's forecast grid, nearest first.

    Args:
        lon, lat: The point, in decimal degrees.

    Returns:
        A DataFrame with ``station``, ``name``, ``lon``, ``lat``,
        ``distance_km`` (from the point) and ``elevation_m``.

    Raises:
        NotCovered: Outside the NWS's forecast area.

    Example::

        from georest.noaa import nws

        nws.stations(-95.37, 29.76).head(3)[["station", "name", "distance_km"]]
    """
    lon, lat = check_point(lon, lat)
    url = _point_meta(lon, lat).get("observationStations")
    if not url:
        raise NotCovered(f"the NWS lists no observation stations for ({lon}, {lat})")
    rows = []
    for f in fetch_json(url, accept=_GEO).get("features") or []:
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        slon, slat = coords[0], coords[1]
        rows.append({
            "station": p.get("stationIdentifier"),
            "name": p.get("name"),
            "lon": slon, "lat": slat,
            "distance_km": (round(km_between(lon, lat, slon, slat), 1)
                            if slon is not None and slat is not None else None),
            "elevation_m": (p.get("elevation") or {}).get("value"),
        })
    rows.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"] or 0))
    return frame(rows, ("station", "name", "lon", "lat", "distance_km", "elevation_m"))


def latest_observation(station: str, *, units: str = "us") -> dict[str, Any]:
    """A station's latest observation, in one unit system.

    Args:
        station: The station's identifier, e.g. ``"KHOU"`` (from
            :func:`stations`).
        units: ``"us"`` (degrees F, mph, inHg, miles; the default) or
            ``"si"`` (degrees C, km/h, hPa, km). The unit is in each key.

    Returns:
        A dict with ``station``, ``observed_at``, ``age_min`` (negative
        means the station's clock runs ahead), ``conditions``, the readings
        (``temperature_f``, ``wind_mph``, ... or their SI forms) and
        ``qc``: the readings the NWS has not validated (``"Z"`` = no
        quality control applied) or whose unit was not recognized - a
        reading with an unrecognized unit is left ``None`` rather than
        mislabelled. Check ``age_min``: "latest" can be hours old.

    Raises:
        NOAAError: If the station is unknown or the NWS cannot be reached.

    Example::

        from georest.noaa import nws

        obs = nws.latest_observation("KHOU")
        obs["temperature_f"], obs["age_min"]
    """
    u = _units(units)
    data = fetch_json(f"{API_URL}/stations/{station}/observations/latest", accept=_GEO)
    row = _read_observation(data.get("properties") or {}, u, datetime.now(timezone.utc))
    return {"station": station, **row}


def observations(lon: float, lat: float, *, n: int = 3, units: str = "us",
                 max_age_minutes: float = 90) -> Any:
    """The latest readings from the stations nearest a point.

    Args:
        lon, lat: The point, in decimal degrees.
        n: How many stations with a current reading to return.
        units: ``"us"`` or ``"si"``, as for :func:`forecast`.
        max_age_minutes: Readings older than this are not current and are
            dropped; so are readings stamped more than 5 minutes in the
            future.

    Returns:
        A DataFrame, nearest station first, with ``station``, ``name``,
        ``distance_km``, ``lon``, ``lat``, ``observed_at``, ``age_min``,
        ``conditions``, the readings and ``qc``. ``df.attrs`` records
        ``dropped_stale`` and ``failed`` (stations that did not answer), so
        a short table can be told apart from a quiet one.

    Raises:
        NotCovered: Outside the NWS's forecast area.

    Example::

        from georest.noaa import nws

        df = nws.observations(-95.37, 29.76)
        df[["station", "distance_km", "age_min", "temperature_f", "wind_mph"]]
    """
    u = _units(units)
    near = stations(lon, lat)
    now = datetime.now(timezone.utc)
    rows, stale, failed = [], [], []
    # Nearest first, until n current readings - but not every station in a
    # grid's list (Houston's has 45): past a few more than asked for, a
    # station is too far away to stand for the point anyway.
    for rec in near.to_dict("records")[: n + 6]:
        if len(rows) >= n:
            break
        try:
            data = fetch_json(f"{API_URL}/stations/{rec['station']}/observations/latest",
                              accept=_GEO)
        except NOAAError:
            failed.append(rec["station"])
            continue
        obs = _read_observation(data.get("properties") or {}, u, now)
        age = obs["age_min"]
        if age is None or age > max_age_minutes or age < -5:
            stale.append(rec["station"])
            continue
        rows.append({"station": rec["station"], "name": rec["name"],
                     "distance_km": rec["distance_km"], "lon": rec["lon"],
                     "lat": rec["lat"], **obs})
    columns = ["station", "name", "distance_km", "lon", "lat", "observed_at",
               "age_min", "conditions", *_obs_columns(u), "qc"]
    df = frame(rows, columns)
    df.attrs["dropped_stale"] = stale
    df.attrs["failed"] = failed
    return df
