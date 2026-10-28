"""Client for the Water Quality Portal (WQP), run by the USGS and EPA.

The largest public store of US water-quality data: samples and field
measurements from the USGS, EPA, states, tribes and hundreds of other
organizations, at more than a million sites:

    https://www.waterqualitydata.us

::

    from georest.usgs import wqp

    sites = wqp.find_sites(state="CO", county="069", site_type="Stream")
    temps = wqp.get_results(sites=["USGS-09380000"], characteristic="Temperature, water",
                            start="2025-01-01", end="2025-12-31")
    temps[["date", "value", "unit", "Result_MeasureStatusIdentifier"]]

This client reads the portal's **WQX 3.0** format. The portal's original
format stopped receiving USGS data in 2024: a 2025 search for USGS results
that returns 130 rows in WQX 3.0 returns none in the original (checked
2026-10-28). WQX 3.0 queries are slower, often most of a minute.

Results are pandas DataFrames with every column the portal sends, plus
``lon`` and ``lat`` (standardized to NAD83) and, for results, ``date``,
``value`` (the measurement as a number) and ``unit``.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import io
import re
import urllib.parse
from collections.abc import Iterable
from typing import Any

from ._http import fetch_text

BASE_URL = "https://www.waterqualitydata.us/wqx3"

#: WQX 3.0 queries routinely take most of a minute; allow five.
TIMEOUT = 300

#: State postal codes onto FIPS codes, which the portal's state and county
#: filters use (``US:08`` for Colorado).
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "AS": "60", "GU": "66", "MP": "69", "PR": "72", "VI": "78",
}


def _state_code(state: str) -> str:
    s = str(state).strip().upper()
    if s.startswith("US:"):
        return s
    if s in STATE_FIPS:
        return f"US:{STATE_FIPS[s]}"
    if s.isdigit() and s.zfill(2) in STATE_FIPS.values():
        return f"US:{s.zfill(2)}"
    raise ValueError(f"state {state!r} is not a state postal code like 'CO' or a FIPS code like '08'")


def _many(value: str | Iterable[str]) -> list[str]:
    """One value or several, always as a list.

    Several values go to the portal as a repeated parameter
    (``siteid=a&siteid=b``). Joining them with semicolons, which the portal
    also accepts, quietly gives wrong answers: two site ids came back as no
    sites at all, and two characteristics as 692 sites instead of the 1,051
    the repeated form returns (checked 2026-10-28).
    """
    return [value] if isinstance(value, str) else [str(v) for v in value]


def _get(path: str, params: dict) -> str:
    """GET from the portal with list values sent as repeated parameters."""
    query = urllib.parse.urlencode(params, doseq=True)
    return fetch_text(f"{BASE_URL}/{path}?{query}", timeout=TIMEOUT)


def _date(value: str, name: str) -> str:
    """ISO ``YYYY-MM-DD`` (or the portal's ``MM-DD-YYYY``) to ``MM-DD-YYYY``."""
    text = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(2)}-{m.group(3)}-{m.group(1)}"
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", text):
        return text
    raise ValueError(f"{name} {value!r} is not a date like 2025-06-30")


def _params(*, sites=None, state=None, county=None, bbox=None, huc=None,
            site_type=None, characteristic=None, providers=None,
            start=None, end=None) -> dict:
    if not any((sites, state, county, bbox, huc)):
        raise ValueError(
            "name where to look: sites=, state=, county= (with state=), bbox= or huc=. "
            "Without one the portal would search the whole country"
        )
    params: dict[str, Any] = {"mimeType": "csv", "zip": "no"}
    if sites:
        params["siteid"] = _many(sites)
    if county:
        if not state:
            raise ValueError("county= needs state= too (county codes repeat across states)")
        county_text = str(county).strip()
        if not county_text.isdigit():
            raise ValueError(f"county {county!r} is a 3-digit FIPS code, e.g. '069'")
        params["countycode"] = f"{_state_code(state)}:{county_text.zfill(3)}"
    elif state:
        params["statecode"] = _state_code(state)
    if bbox:
        west, south, east, north = (float(v) for v in bbox)
        if west >= east or south >= north:
            raise ValueError("bbox is (west, south, east, north), west < east and south < north")
        params["bBox"] = f"{west},{south},{east},{north}"
    if huc:
        params["huc"] = str(huc)
    for name, value in (("siteType", site_type), ("characteristicName", characteristic),
                        ("providers", providers)):
        if value:
            params[name] = _many(value)
    if start:
        params["startDateLo"] = _date(start, "start")
    if end:
        params["startDateHi"] = _date(end, "end")
    return params


def _read(text: str) -> Any:
    import pandas as pd

    if not text.strip():
        return pd.DataFrame()
    # Read everything as text first: identifiers such as HUC codes and
    # county FIPS codes carry leading zeros that a number would drop.
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)


def _with_coordinates(df: Any) -> Any:
    import pandas as pd

    for out, column in (("lon", "Location_LongitudeStandardized"),
                        ("lat", "Location_LatitudeStandardized")):
        df[out] = pd.to_numeric(df[column], errors="coerce") if column in df else pd.Series(dtype=float)
    return df


def find_sites(*, sites: str | Iterable[str] | None = None, state: str | None = None,
               county: str | None = None, bbox: tuple | None = None,
               huc: str | None = None, site_type: str | Iterable[str] | None = None,
               characteristic: str | Iterable[str] | None = None,
               providers: str | Iterable[str] | None = None) -> Any:
    """Monitoring locations in the Water Quality Portal.

    Args:
        sites: Site ids, e.g. ``"USGS-09380000"``.
        state: A state postal code (``"CO"``) or FIPS code (``"08"``).
        county: A county's 3-digit FIPS code, with ``state``.
        bbox: ``(west, south, east, north)`` in decimal degrees.
        huc: A hydrologic unit code (2 to 12 digits).
        site_type: e.g. ``"Stream"``, ``"Lake, Reservoir, Impoundment"``,
            ``"Well"``, ``"Spring"``.
        characteristic: Keep only sites with samples of this, e.g.
            ``"Nitrate"`` or ``"Temperature, water"``.
        providers: ``"NWIS"`` (USGS), ``"STORET"`` (EPA and partners), or both.

    Returns:
        ``pandas.DataFrame``, one row per site, with every WQX 3.0 location
        column (``Location_Identifier``, ``Location_Name``,
        ``Location_Type``, HUC codes, drainage area, ...) plus ``lon`` and
        ``lat``.

    Example::

        from georest.usgs import wqp

        # Stream sites in Larimer County, Colorado, that measured nitrate
        sites = wqp.find_sites(state="CO", county="069", site_type="Stream",
                               characteristic="Nitrate")
        sites[["Location_Identifier", "Location_Name", "Org_FormalName", "lon", "lat"]]
    """
    params = _params(sites=sites, state=state, county=county, bbox=bbox, huc=huc,
                     site_type=site_type, characteristic=characteristic, providers=providers)
    return _with_coordinates(_read(_get("Station/search", params)))


def get_results(*, sites: str | Iterable[str] | None = None, state: str | None = None,
                county: str | None = None, bbox: tuple | None = None,
                huc: str | None = None, site_type: str | Iterable[str] | None = None,
                characteristic: str | Iterable[str] | None = None,
                providers: str | Iterable[str] | None = None,
                start: str | None = None, end: str | None = None,
                profile: str = "basicPhysChem") -> Any:
    """Water-quality results: samples and field measurements.

    Args:
        sites, state, county, bbox, huc, site_type, providers: Where to look,
            as for :func:`find_sites`. At least one place is required.
        characteristic: What was measured, e.g. ``"Temperature, water"``,
            ``"Nitrate"``, ``"pH"``, ``"Escherichia coli"``.
        start, end: Sample dates, ``"YYYY-MM-DD"``.
        profile: The portal's column set: ``"basicPhysChem"`` (the default)
            or ``"fullPhysChem"`` for every lab and method detail.

    Returns:
        ``pandas.DataFrame``, one row per result, with every WQX 3.0 column
        (site, activity, characteristic, measure and unit, status, detection
        limits, method, ...) plus ``lon``, ``lat``, ``date``, ``value`` (the
        measurement as a number, empty where the portal reports none, such as
        a non-detect; see ``Result_ResultDetectionCondition``) and ``unit``.

    Example::

        from georest.usgs import wqp

        temps = wqp.get_results(sites="USGS-09380000", characteristic="Temperature, water",
                                start="2025-01-01", end="2025-12-31")
        temps[["date", "value", "unit", "Result_MeasureStatusIdentifier"]]
    """
    import pandas as pd

    params = _params(sites=sites, state=state, county=county, bbox=bbox, huc=huc,
                     site_type=site_type, characteristic=characteristic, providers=providers,
                     start=start, end=end)
    params["dataProfile"] = profile
    df = _with_coordinates(_read(_get("Result/search", params)))
    df["date"] = df["Activity_StartDate"] if "Activity_StartDate" in df else pd.Series(dtype=str)
    df["value"] = (pd.to_numeric(df["Result_Measure"], errors="coerce")
                   if "Result_Measure" in df else pd.Series(dtype=float))
    df["unit"] = df["Result_MeasureUnit"] if "Result_MeasureUnit" in df else pd.Series(dtype=str)
    return df
