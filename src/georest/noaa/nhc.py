"""Client for the National Hurricane Center's active storms (www.nhc.noaa.gov).

Every tropical cyclone the NHC is advising on - Atlantic, eastern and
central Pacific - with its latest position, strength and motion, and links
to its advisory and forecast-cone files::

    from georest.noaa import nhc

    nhc.active_storms()[["name", "classification", "wind_kt", "lon", "lat"]]

No rows means the NHC is advising on no storm right now. Positions and
strengths are as of each storm's ``updated`` time; advisories are issued
every 6 hours (3 when a storm threatens land).

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from typing import Any

from ._frames import frame, number
from ._http import fetch_json

STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"

#: The NHC's classification codes.
CLASSIFICATIONS = {
    "TD": "Tropical Depression", "TS": "Tropical Storm", "HU": "Hurricane",
    "STD": "Subtropical Depression", "STS": "Subtropical Storm",
    "PTC": "Post-tropical Cyclone", "PC": "Potential Tropical Cyclone",
    "TY": "Typhoon",
}

_COLUMNS = ("id", "name", "classification", "classification_name", "wind_kt",
            "pressure_mb", "lon", "lat", "moving_toward_deg", "speed_kt", "updated",
            "advisory_url", "cone_url")


def active_storms() -> Any:
    """The tropical cyclones the NHC is advising on now, one row per storm.

    Returns:
        A DataFrame with ``id`` (e.g. ``"al052026"``), ``name``,
        ``classification`` (``"HU"``, ``"TS"``, ...) and its
        ``classification_name``, ``wind_kt`` (maximum sustained wind),
        ``pressure_mb``, ``lon``, ``lat``, ``moving_toward_deg`` and
        ``speed_kt``, ``updated``, and the ``advisory_url`` and
        ``cone_url`` (a zipped shapefile of the forecast cone) where the
        NHC publishes them. No rows means the NHC is advising on no storm
        right now.

    Raises:
        NOAAError: If the NHC's feed cannot be reached.

    Example::

        from georest.noaa import nhc

        nhc.active_storms()[["name", "classification_name", "wind_kt"]]
    """
    data = fetch_json(STORMS_URL)
    rows = []
    for s in data.get("activeStorms") or []:
        cls = s.get("classification")
        rows.append({
            "id": s.get("id"), "name": s.get("name"),
            "classification": cls, "classification_name": CLASSIFICATIONS.get(cls),
            "wind_kt": number(s.get("intensity")), "pressure_mb": number(s.get("pressure")),
            "lon": number(s.get("longitudeNumeric")), "lat": number(s.get("latitudeNumeric")),
            "moving_toward_deg": number(s.get("movementDir")),
            "speed_kt": number(s.get("movementSpeed")),
            "updated": s.get("lastUpdate"),
            "advisory_url": (s.get("publicAdvisory") or {}).get("url"),
            "cone_url": (s.get("trackCone") or {}).get("zipFile"),
        })
    return frame(rows, _COLUMNS)
