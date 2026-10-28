"""Client for the USGS earthquake catalog (the FDSN event web service).

Every earthquake the USGS and its partner networks have located, worldwide,
back to the early 1900s for large events, searchable by time, area,
magnitude and depth:

    https://earthquake.usgs.gov/fdsnws/event/1

::

    from georest.restusgs import earthquakes

    big = earthquakes.search("2025-01-01", "2025-12-31", min_magnitude=6)
    big[["time", "magnitude", "place", "depth_km", "tsunami"]]

    near_la = earthquakes.search("2025-01-01", "2025-12-31",
                                 center=(-118.24, 34.05), radius_km=100,
                                 min_magnitude=3)

Results are complete. The service refuses any search matching more than
20,000 events, so :func:`search` counts first and then fetches the events
20,000 at a time until it has them all. :func:`count` tells you how many a
search will return before you run it.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from typing import Any

from ._frames import frame
from ._http import fetch_json

BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1"

#: The most events the service returns in one request.
PAGE_LIMIT = 20_000

#: How results can be ordered, onto the service's names.
ORDERS = {"newest": "time", "oldest": "time-asc",
          "largest": "magnitude", "smallest": "magnitude-asc"}

_COLUMNS = ("id", "time", "magnitude", "magnitude_type", "place", "depth_km", "lon",
            "lat", "type", "alert", "tsunami", "felt", "cdi", "mmi", "significance",
            "status", "network", "updated", "title", "url")


def _params(start, end, *, bbox=None, center=None, radius_km=None,
            min_magnitude=None, max_magnitude=None, min_depth_km=None,
            max_depth_km=None, event_type=None, alert_level=None) -> dict:
    """The service's query parameters, checked before anything is sent."""
    params: dict[str, Any] = {"format": "geojson"}
    if start:
        params["starttime"] = str(start)
    if end:
        params["endtime"] = str(end)
    if bbox is not None and center is not None:
        raise ValueError("pass either bbox or center and radius_km, not both")
    if bbox is not None:
        west, south, east, north = (float(v) for v in bbox)
        if not (-90 <= south < north <= 90):
            raise ValueError("bbox is (west, south, east, north) in degrees, south < north")
        params.update(minlongitude=west, minlatitude=south,
                      maxlongitude=east, maxlatitude=north)
    if center is not None or radius_km is not None:
        if center is None or radius_km is None:
            raise ValueError("a circle search needs both center=(lon, lat) and radius_km")
        lon, lat = (float(v) for v in center)
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(f"center {center} is not (lon, lat) in degrees")
        if float(radius_km) <= 0 or float(radius_km) > 20001.6:
            raise ValueError("radius_km must be between 0 and 20001.6 (half the Earth's circumference)")
        params.update(longitude=lon, latitude=lat, maxradiuskm=float(radius_km))
    for name, value in (("minmagnitude", min_magnitude), ("maxmagnitude", max_magnitude),
                        ("mindepth", min_depth_km), ("maxdepth", max_depth_km)):
        if value is not None:
            params[name] = float(value)
    if event_type:
        params["eventtype"] = event_type
    if alert_level:
        if alert_level not in ("green", "yellow", "orange", "red"):
            raise ValueError("alert_level is one of 'green', 'yellow', 'orange', 'red'")
        params["alertlevel"] = alert_level
    return params


def count(start=None, end=None, **filters) -> int:
    """How many events a :func:`search` with the same arguments returns.

    Example::

        from georest.restusgs import earthquakes

        earthquakes.count("2025-01-01", "2025-12-31", min_magnitude=2.5)   # e.g. 29167
    """
    reply = fetch_json(f"{BASE_URL}/count", _params(start, end, **filters))
    return int(reply["count"])


def _row(feature: dict) -> dict:
    import pandas as pd

    p = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
    return {
        "id": feature.get("id"),
        "time": pd.to_datetime(p.get("time"), unit="ms", utc=True) if p.get("time") is not None else None,
        "magnitude": p.get("mag"),
        "magnitude_type": p.get("magType"),
        "place": p.get("place"),
        "depth_km": coords[2] if len(coords) > 2 else None,
        "lon": coords[0],
        "lat": coords[1],
        "type": p.get("type"),
        "alert": p.get("alert"),
        "tsunami": bool(p.get("tsunami")),
        "felt": p.get("felt"),
        "cdi": p.get("cdi"),
        "mmi": p.get("mmi"),
        "significance": p.get("sig"),
        "status": p.get("status"),
        "network": p.get("net"),
        "updated": pd.to_datetime(p.get("updated"), unit="ms", utc=True) if p.get("updated") is not None else None,
        "title": p.get("title"),
        "url": p.get("url"),
    }


def search(start=None, end=None, *, bbox=None, center=None, radius_km=None,
           min_magnitude=None, max_magnitude=None, min_depth_km=None,
           max_depth_km=None, event_type=None, alert_level=None,
           order: str = "newest") -> Any:
    """Earthquakes (and other seismic events) matching a search.

    Args:
        start, end: Time range, as ``"YYYY-MM-DD"`` or an ISO 8601 time (UTC).
            Without ``start`` the service searches the last 30 days.
        bbox: ``(west, south, east, north)`` in degrees. A box that crosses
            the antimeridian works with ``west`` > ``east``.
        center, radius_km: A circle instead: ``(lon, lat)`` and a radius in
            kilometers.
        min_magnitude, max_magnitude: Magnitude range.
        min_depth_km, max_depth_km: Depth range, in kilometers below sea level.
        event_type: Keep one kind of event, e.g. ``"earthquake"``. The
            catalog also holds quarry blasts, explosions and ice quakes.
        alert_level: Keep events with this PAGER alert level: ``"green"``,
            ``"yellow"``, ``"orange"`` or ``"red"``.
        order: ``"newest"`` (default), ``"oldest"``, ``"largest"`` or
            ``"smallest"``.

    Returns:
        ``pandas.DataFrame``, one row per event: ``id``, ``time`` (UTC),
        ``magnitude``, ``magnitude_type``, ``place``, ``depth_km``, ``lon``,
        ``lat``, ``type``, ``alert`` (PAGER level), ``tsunami``, ``felt``
        (number of "Did You Feel It?" reports), ``cdi`` and ``mmi``
        (intensities), ``significance``, ``status``, ``network``,
        ``updated``, ``title`` and ``url`` (the event page). Every matching
        event is returned; searches beyond 20,000 events are fetched in
        pages.

    Example::

        from georest.restusgs import earthquakes

        # Magnitude 6 and larger worldwide in 2025, largest first
        big = earthquakes.search("2025-01-01", "2025-12-31", min_magnitude=6,
                                 order="largest")
        big[["time", "magnitude", "place", "depth_km", "alert", "tsunami"]]

        # Within 100 km of Los Angeles
        earthquakes.search("2025-01-01", "2025-12-31", center=(-118.24, 34.05),
                           radius_km=100, min_magnitude=3)
    """
    if order not in ORDERS:
        raise ValueError(f"order is one of {sorted(ORDERS)}, not {order!r}")
    params = _params(start, end, bbox=bbox, center=center, radius_km=radius_km,
                     min_magnitude=min_magnitude, max_magnitude=max_magnitude,
                     min_depth_km=min_depth_km, max_depth_km=max_depth_km,
                     event_type=event_type, alert_level=alert_level)
    params["orderby"] = ORDERS[order]
    total = int(fetch_json(f"{BASE_URL}/count", params)["count"])

    rows: list[dict] = []
    offset = 1   # the service counts from 1
    while offset <= total:
        page = fetch_json(f"{BASE_URL}/query", {**params, "limit": PAGE_LIMIT, "offset": offset})
        features = page.get("features") or []
        if not features:
            break
        rows.extend(_row(f) for f in features)
        offset += len(features)
    return frame(rows, _COLUMNS)


def get_event(event_id: str) -> dict:
    """Everything the catalog holds about one event.

    Args:
        event_id: The event's id, e.g. from the ``id`` column of
            :func:`search`.

    Returns:
        The event's fields as in a :func:`search` row, plus ``products``
        (the kinds of information available for it, such as
        ``"shakemap"``, ``"dyfi"``, ``"losspager"``, ``"moment-tensor"``)
        and ``product_urls`` (each product's main file, by product kind).

    Example::

        from georest.restusgs import earthquakes

        quake = earthquakes.search("2025-01-01", "2025-12-31", min_magnitude=7,
                                   order="largest").iloc[0]
        detail = earthquakes.get_event(quake["id"])
        detail["place"], detail["products"]
    """
    feature = fetch_json(f"{BASE_URL}/query", {"format": "geojson", "eventid": event_id})
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError(f"no event with id {event_id!r}")
    row = _row(feature)
    products = (feature.get("properties") or {}).get("products") or {}
    row["products"] = sorted(products)
    urls = {}
    for kind, versions in products.items():
        contents = (versions[0] if versions else {}).get("contents") or {}
        main = next((c.get("url") for name, c in contents.items()
                     if name.endswith((".xml", ".json", ".html")) and c.get("url")), None)
        if main:
            urls[kind] = main
    row["product_urls"] = urls
    return row
