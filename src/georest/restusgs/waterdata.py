"""
USGS Water Data OGC API client — the modern replacement for NWIS Water Services.

Base URL ``https://api.waterdata.usgs.gov/ogcapi/v1``. Every function returns a
GeoJSON ``FeatureCollection`` dict (one Feature per *observation* for the
time-series collections, one per site for ``monitoring-locations``), matching
the rest of georest. :func:`to_rows` flattens one into plain dicts for CSV,
pandas, or plotting.

Legacy NWIS endpoint → collection (from the USGS migration guide)::

    /nwis/site                       monitoring-locations
    /nwis/site?seriesCatalogOutput   time-series-metadata
    /nwis/dv                         daily
    /nwis/iv                         continuous / latest-continuous
    /nwis/gwlevels, /measurements    field-measurements
    /nwis/peak                       peaks

Things verified against the live API (2026-09-14) that shape this module:

- The API does not sort. The time-series functions sort their output by
  ``(time_series_id, time)`` client-side.
- ``value`` is a string (precision). The FeatureCollection is returned as
  sent; :func:`to_rows` converts to ``float``.
- There is no ``numberMatched``. ``max_items`` bounds every fetch and a
  ``truncated: True`` member says when it bit; ``max_items=None`` means
  unlimited. Fifteen-minute ``continuous`` data is roughly 35 000 rows per
  year per parameter, so the default of 10 000 covers about 100 days.
- Anonymous use works but is rate limited to "a few queries per hour". Get a
  key at https://api.waterdata.usgs.gov/signup/ and set ``USGS_API_KEY`` (or
  call :func:`set_api_key`). The key is sent only as the ``X-Api-Key`` header.

API-key resolution, per call: an explicit ``api_key=`` argument wins
(``""`` forces anonymous); else the value given to :func:`set_api_key`; else
the first non-empty of the ``USGS_API_KEY`` / ``USGS_WATERDATA_API_KEY``
environment variables; else anonymous. Reading a ``.env`` file is the
application's job, not this library's.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import datetime as _dt
import os
from collections.abc import Iterable

from . import _ogc
from ._http import RateLimitError  # noqa: F401 - re-exported so callers can catch it by name

BASE_URL = "https://api.waterdata.usgs.gov/ogcapi/v1"

#: The data collections, for reference. :func:`list_collections` is the
#: authoritative, live list (it also includes the small code tables such as
#: ``agency-codes`` and ``aquifer-codes``).
COLLECTIONS = (
    "monitoring-locations",
    "daily",
    "latest-daily",
    "continuous",
    "latest-continuous",
    "field-measurements",
    "latest-field-measurements",
    "channel-measurements",
    "peaks",
    "time-series-metadata",
    "field-measurements-metadata",
    "combined-metadata",
    "time-series-revisions",
    "time-series-methods",
)

#: The parameter codes most queries want. Any 5-digit USGS parameter code is
#: valid; these are just the common ones, described for readability.
PARAMETER_CODES = {
    "00060": "Discharge, cubic feet per second",
    "00065": "Gage height, feet",
    "00010": "Temperature, water, degrees Celsius",
    "00045": "Precipitation, total, inches",
    "72019": "Depth to water level, feet below land surface",
}

#: Statistic codes for the ``daily`` / ``latest-daily`` collections.
STATISTIC_CODES = {
    "00001": "Maximum",
    "00002": "Minimum",
    "00003": "Mean",
    "00008": "Median",
}

#: Environment variables checked, in order, when no key is given explicitly.
_KEY_NAMES = ("USGS_API_KEY", "USGS_WATERDATA_API_KEY")
API_KEY_ENV = _KEY_NAMES[0]
_API_KEY: str | None = None

DEFAULT_MAX_ITEMS = 10_000
LOCATION_PAGE_SIZE = 1_000
SERIES_PAGE_SIZE = _ogc.DEFAULT_PAGE_SIZE  # 10 000: per-request cost dominates

#: Properties the time-series helpers always request, even when the caller
#: trims with `properties=`, so sorting and `to_rows` are never starved.
_REQUIRED_SERIES_PROPERTIES = ("time_series_id", "monitoring_location_id", "time", "value")

_ID_PREFIX = "USGS-"
_LATEST_SOURCES = {"daily": "latest-daily", "continuous": "latest-continuous"}


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------


def set_api_key(key: str | None) -> None:
    """Set the process-wide API key, e.g. from a secret store at server start.

    Takes precedence over the environment variables; an explicit ``api_key=``
    argument on a call still wins. ``None`` (or ``""``) clears it, so
    resolution falls back to the environment again.
    """
    global _API_KEY
    _API_KEY = key or None


def _api_key(explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit or None  # "" means: anonymous for this call
    if _API_KEY:
        return _API_KEY
    for name in _KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _headers(api_key: str | None) -> dict[str, str]:
    return _ogc.api_key_headers(_api_key(api_key))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _site_id(value: str | int) -> str:
    """``"09380000"`` → ``"USGS-09380000"``. Anything already carrying an
    agency prefix (``USGS-…``, ``USEPA-…``) passes through untouched."""
    text = str(value).strip()
    return text if "-" in text else _ID_PREFIX + text


def _cql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _eq_or_in(
    name: str, value: object, normalise=str
) -> tuple[str | None, str | None]:
    """A scalar becomes a direct equality param; a list becomes a CQL2 ``IN``.

    Returns ``(param_value, cql_clause)`` — exactly one is non-None, or both
    are None when ``value`` is None.
    """
    if value is None:
        return None, None
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [normalise(v) for v in value]
        if not items:
            raise ValueError(f"{name} list is empty")
        if len(items) == 1:
            return items[0], None
        return None, f"{name} IN ({', '.join(_cql_quote(i) for i in items)})"
    return normalise(value), None


def _time_filter(start=None, end=None) -> str | None:
    """The CQL2 equivalent of a ``datetime`` window, for collections that
    reject the ``datetime`` parameter. Accepts everything
    :func:`georest.restusgs._ogc.datetime_param` does (dates, intervals,
    durations) and translates its result."""
    window = _ogc.datetime_param(start, end)
    if window is None:
        return None
    if "/" not in window:
        return f"time = {_cql_quote(window)}"
    lo, hi = window.split("/", 1)
    return _and(
        f"time >= {_cql_quote(lo)}" if lo not in ("", "..") else None,
        f"time <= {_cql_quote(hi)}" if hi not in ("", "..") else None,
    )


def _and(*clauses: str | None) -> str | None:
    parts = [c for c in clauses if c]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return " AND ".join(f"({p})" for p in parts)


def _time_key(value: object) -> tuple:
    """A sort key for a ``time`` property that never compares unlike types.

    Parsed timestamps sort first (as naive UTC), unparsable strings after
    them in text order, and missing values last.
    """
    if isinstance(value, str) and value:
        text = value.strip()
        if text[-1] in "Zz":
            text = text[:-1] + "+00:00"  # 3.9's fromisoformat does not accept Z
        try:
            stamp = _dt.datetime.fromisoformat(text)
        except ValueError:
            return (1, text)
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return (0, stamp)
    return (2, "")


def _series_key(props: dict) -> tuple:
    series = props.get("time_series_id") or props.get("monitoring_location_id") or ""
    return (str(series), _time_key(props.get("time")))


def _sort_by_time(fc: dict) -> dict:
    """Sort a FeatureCollection's features by ``(time_series_id, time)`` in place.
    The API does not sort, so a date-windowed query comes back in storage
    order (verified: a January window returned the 22nd first)."""
    fc["features"] = sorted(fc.get("features", []), key=lambda f: _series_key(f.get("properties") or {}))
    return fc


def _series_properties(properties: str | Iterable[str] | None) -> list[str] | None:
    if properties is None:
        return None
    given = properties.split(",") if isinstance(properties, str) else list(properties)
    given = [p.strip() for p in given if str(p).strip()]
    for required in _REQUIRED_SERIES_PROPERTIES:
        if required not in given:
            given.append(required)
    return given


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def list_collections(*, api_key: str | None = None, timeout: int | None = None) -> list[dict]:
    """The live collection catalog: ``[{"id", "title", "description", "links", ...}]``.

    Raises:
        RuntimeError: On an HTTP or connection failure (``RateLimitError`` on
            429). ``ValueError`` if the body isn't JSON.
    """
    return _ogc.list_collections(BASE_URL, headers=_headers(api_key), timeout=timeout)


def get_queryables(
    collection: str, *, api_key: str | None = None, timeout: int | None = None
) -> dict:
    """``{property_name: json_schema}`` for a collection. Every key is a valid
    direct filter for :func:`get_items` and the helpers built on it —
    ``daily`` alone has ~55, including every site attribute."""
    return _ogc.get_queryables(BASE_URL, collection, headers=_headers(api_key), timeout=timeout)


# ---------------------------------------------------------------------------
# Generic fetch
# ---------------------------------------------------------------------------


def get_items(
    collection: str,
    *,
    bbox: str | dict | list | tuple | None = None,
    start: str | _dt.date | _dt.datetime | None = None,
    end: str | _dt.date | _dt.datetime | None = None,
    properties: str | Iterable[str] | None = None,
    skip_geometry: bool = False,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = LOCATION_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """Fetch Features from any collection as a GeoJSON FeatureCollection.

    The escape hatch under every helper below; use it directly for a
    collection without one (``channel-measurements``, ``time-series-methods``,
    the code tables) or for a filter the helpers don't spell out.

    Args:
        collection: A collection id — see :data:`COLLECTIONS` / :func:`list_collections`.
        bbox: ``minx,miny,maxx,maxy`` as a string, 4-tuple, or Esri-style dict.
        start, end: The ``datetime`` window — strings, ``date`` or
            ``datetime`` objects; either side may be open. ``start`` may also
            be a ready interval (``"2020-01-01/2020-01-31"``, ``"../2020-12-31"``)
            or an ISO 8601 duration such as ``"P7D"`` or ``"PT6H"`` (the last 7
            days / 6 hours). The server rejects durations, so they are
            resolved client-side to ``<now minus duration>/..``; years and
            months (``"P1Y"``, ``"P3M"``) raise ``ValueError``.
        properties: Property names to keep; the response is trimmed to exactly
            these (verified). Note this generic function does *not* add the
            time-series essentials back — the helpers do.
        skip_geometry: Return ``geometry: null`` on every Feature. Large
            observation pulls don't need a point repeated per row.
        filter: A CQL2 text expression (``"statistic_id IN ('00001','00003')"``).
        max_items: Stop after this many Features; ``None`` for unlimited. When
            the server had more, the result carries ``truncated: True``.
        page_size: Rows per request, ``1..50_000``.
        api_key: Overrides the process/environment key for this call; ``""``
            forces anonymous.
        timeout: Seconds per request (default 60).
        **filters: Any queryable property as an equality filter, e.g.
            ``parameter_code="00060"``, ``state_code="49"``.

    Returns:
        A ``FeatureCollection`` dict, plus ``truncated`` and ``rate_limit``
        members when applicable (see :func:`georest.restusgs._ogc.get_items`).

    Raises:
        TypeError: If ``f``, ``limit``, ``cursor`` or ``offset`` is passed in
            ``**filters``.
        ValueError: For a bad ``bbox``/``page_size``/``max_items``, an
            interval ``start`` combined with ``end``, a non-JSON body, or a
            page without ``features``.
        RuntimeError: On an HTTP error status or connection failure; the
            message includes the API's own ``description`` when it sent one.
            A 429 the server won't let us wait out raises the
            :class:`RateLimitError` subclass, whose ``retry_after`` says how
            long it asked for.
    """
    return _ogc.get_items(
        BASE_URL,
        collection,
        max_items=max_items,
        headers=_headers(api_key),
        timeout=timeout,
        bbox=bbox,
        datetime=_ogc.datetime_param(start, end),
        properties=properties,
        skip_geometry=skip_geometry,
        filter=filter,
        limit=page_size,
        **filters,
    )


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------


def search_monitoring_locations(
    *,
    bbox: str | dict | list | tuple | None = None,
    state_code: str | None = None,
    county_code: str | None = None,
    site_type_code: str | list[str] | None = None,
    hydrologic_unit_code: str | None = None,
    monitoring_location_id: str | int | list | None = None,
    agency_code: str | None = None,
    properties: str | Iterable[str] | None = None,
    filter: str | None = None,
    max_items: int | None = 1_000,
    page_size: int = LOCATION_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """Find sites in the ``monitoring-locations`` collection.

    Every filter is optional and they combine with AND. Common ones:

    Args:
        bbox: Spatial window, ``minx,miny,maxx,maxy`` (WGS84 lon/lat).
        state_code: Two-digit ANSI/FIPS state code, e.g. ``"49"`` for Utah.
        county_code: County FIPS code within the state.
        site_type_code: ``"ST"`` stream, ``"LK"`` lake, ``"GW"`` well,
            ``"SP"`` spring, ``"ES"`` estuary… A list becomes a CQL2 ``IN``.
        hydrologic_unit_code: A HUC; the API prefix-matches, so ``"1407"``
            finds every HUC8 under that HUC4.
        monitoring_location_id: One id or a list; bare numbers get the
            ``USGS-`` prefix.
        agency_code: ``"USGS"``, ``"USEPA"``, …
        properties, filter, max_items, page_size, api_key, timeout, **filters:
            As for :func:`get_items`. ``max_items`` defaults to 1 000 here.

    Returns:
        A FeatureCollection of site Points; each Feature's ``id`` is the
        ``monitoring_location_id`` (``"USGS-09380000"``).
    """
    site, site_clause = _eq_or_in("monitoring_location_id", monitoring_location_id, _site_id)
    kind, kind_clause = _eq_or_in("site_type_code", site_type_code)
    direct = {
        "state_code": state_code,
        "county_code": county_code,
        "site_type_code": kind,
        "hydrologic_unit_code": hydrologic_unit_code,
        "monitoring_location_id": site,
        "agency_code": agency_code,
    }
    return get_items(
        "monitoring-locations",
        bbox=bbox,
        properties=properties,
        filter=_and(filter, site_clause, kind_clause),
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **{k: v for k, v in direct.items() if v is not None},
        **filters,
    )


def get_monitoring_location(
    monitoring_location_id: str | int, *, api_key: str | None = None, timeout: int | None = None
) -> dict:
    """One site as a single GeoJSON Feature (not a collection).

    ``monitoring_location_id`` may be bare (``"09380000"``) or prefixed
    (``"USGS-09380000"``).

    Raises:
        RuntimeError: A 404 for an unknown id surfaces as
            ``Request failed (404): …``.
    """
    return _ogc.get_item(
        BASE_URL,
        "monitoring-locations",
        _site_id(monitoring_location_id),
        headers=_headers(api_key),
        timeout=timeout,
    )


def get_time_series_metadata(
    *,
    monitoring_location_id: str | int | list | None = None,
    parameter_code: str | list[str] | None = None,
    statistic_id: str | None = None,
    bbox: str | dict | list | tuple | None = None,
    properties: str | Iterable[str] | None = None,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = SERIES_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """What time series exist — the ``time-series-metadata`` collection
    (the old ``/nwis/site?seriesCatalogOutput=true``).

    One Feature per (site, parameter, statistic, computation) with its
    ``begin``/``end`` dates, unit and ``time_series_id``. Use it to discover
    which ``parameter_code`` values a site actually reports before pulling
    values.
    """
    site, site_clause = _eq_or_in("monitoring_location_id", monitoring_location_id, _site_id)
    param, param_clause = _eq_or_in("parameter_code", parameter_code)
    direct = {
        "monitoring_location_id": site,
        "parameter_code": param,
        "statistic_id": statistic_id,
    }
    return get_items(
        "time-series-metadata",
        bbox=bbox,
        properties=properties,
        skip_geometry=True,
        filter=_and(filter, site_clause, param_clause),
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **{k: v for k, v in direct.items() if v is not None},
        **filters,
    )


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def _series(
    collection: str,
    *,
    monitoring_location_id: str | int | list | None,
    parameter_code: str | list[str] | None,
    statistic_id: str | list[str] | None = None,
    start=None,
    end=None,
    bbox=None,
    properties=None,
    skip_geometry: bool = True,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = SERIES_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    site, site_clause = _eq_or_in("monitoring_location_id", monitoring_location_id, _site_id)
    param, param_clause = _eq_or_in("parameter_code", parameter_code)
    stat, stat_clause = _eq_or_in("statistic_id", statistic_id)
    direct = {"monitoring_location_id": site, "parameter_code": param, "statistic_id": stat}
    fc = get_items(
        collection,
        bbox=bbox,
        start=start,
        end=end,
        properties=_series_properties(properties),
        skip_geometry=skip_geometry,
        filter=_and(filter, site_clause, param_clause, stat_clause),
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **{k: v for k, v in direct.items() if v is not None},
        **filters,
    )
    return _sort_by_time(fc)


def get_daily_values(
    monitoring_location_id: str | int | list,
    parameter_code: str | list[str] = "00060",
    statistic_id: str | list[str] = "00003",
    start: str | _dt.date | _dt.datetime | None = None,
    end: str | _dt.date | _dt.datetime | None = None,
    *,
    properties: str | Iterable[str] | None = None,
    skip_geometry: bool = True,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = SERIES_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """Daily statistics for a site — the ``daily`` collection (old ``/nwis/dv``).

    One Feature per day, sorted by ``(time_series_id, time)``. ``time`` is a
    ``YYYY-MM-DD`` string; ``value`` is a string — see :func:`to_rows`.

    Args:
        monitoring_location_id: ``"09380000"`` or ``"USGS-09380000"``; a list
            of ids is allowed and becomes a CQL2 ``IN``.
        parameter_code: Default ``"00060"`` (discharge). See :data:`PARAMETER_CODES`.
        statistic_id: Default ``"00003"`` (daily mean). See :data:`STATISTIC_CODES`.
        start, end: Date window (inclusive), open on either side.
        skip_geometry: Default ``True`` — the site's point is the same on
            every row; use :func:`get_monitoring_location` for it.
        properties: Trim the response; ``time_series_id``, ``monitoring_location_id``,
            ``time`` and ``value`` are always kept.
        max_items: Default 10 000 (~27 years of one daily series). ``None``
            for unlimited; ``truncated: True`` in the result when it bit.
        filter, page_size, api_key, timeout, **filters: As for :func:`get_items`.

    Example::

        fc = get_daily_values("USGS-09380000", "00060", start="2024-01-01", end="2024-01-31")
        rows = to_rows(fc)   # [{"time": "2024-01-01", "value": 9870.0, ...}, ...]
    """
    return _series(
        "daily",
        monitoring_location_id=monitoring_location_id,
        parameter_code=parameter_code,
        statistic_id=statistic_id,
        start=start,
        end=end,
        properties=properties,
        skip_geometry=skip_geometry,
        filter=filter,
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **filters,
    )


def get_continuous_values(
    monitoring_location_id: str | int | list,
    parameter_code: str | list[str] = "00060",
    start: str | _dt.date | _dt.datetime | None = None,
    end: str | _dt.date | _dt.datetime | None = None,
    *,
    properties: str | Iterable[str] | None = None,
    skip_geometry: bool = True,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = SERIES_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """Sensor readings, typically every 15 minutes — the ``continuous``
    collection (old ``/nwis/iv``, "instantaneous values").

    ``time`` is an RFC 3339 timestamp with offset. **Volume warning:** one
    parameter at one site is ~35 000 rows per year, so the default
    ``max_items`` of 10 000 covers roughly 100 days; a long pull needs a
    narrow ``start``/``end`` window or ``max_items=None`` plus an API key.
    ``start="P7D"`` asks for the last seven days.

    Sorted by ``(time_series_id, time)``. Other arguments as for
    :func:`get_daily_values` (there is no ``statistic_id`` here).
    """
    return _series(
        "continuous",
        monitoring_location_id=monitoring_location_id,
        parameter_code=parameter_code,
        start=start,
        end=end,
        properties=properties,
        skip_geometry=skip_geometry,
        filter=filter,
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **filters,
    )


def get_latest_values(
    monitoring_location_id: str | int | list | None = None,
    parameter_code: str | list[str] | None = None,
    *,
    source: str = "daily",
    statistic_id: str | list[str] | None = None,
    bbox: str | dict | list | tuple | None = None,
    properties: str | Iterable[str] | None = None,
    skip_geometry: bool = False,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = SERIES_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """The most recent observation per time series — ``latest-daily`` or
    ``latest-continuous`` (``source="daily"`` / ``"continuous"``).

    Unlike the history functions, no site is required: a ``bbox`` with
    ``parameter_code="00060"`` gives the current discharge at every gage in
    an area, which is why ``skip_geometry`` defaults to ``False`` here.

    Raises:
        ValueError: For a ``source`` other than ``"daily"`` or ``"continuous"``.
    """
    try:
        collection = _LATEST_SOURCES[source]
    except KeyError:
        raise ValueError(
            f"source must be one of {sorted(_LATEST_SOURCES)}, got {source!r}"
        ) from None
    return _series(
        collection,
        monitoring_location_id=monitoring_location_id,
        parameter_code=parameter_code,
        statistic_id=statistic_id,
        bbox=bbox,
        properties=properties,
        skip_geometry=skip_geometry,
        filter=filter,
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **filters,
    )


def get_field_measurements(
    monitoring_location_id: str | int | list | None = None,
    parameter_code: str | list[str] | None = None,
    start: str | _dt.date | _dt.datetime | None = None,
    end: str | _dt.date | _dt.datetime | None = None,
    *,
    bbox: str | dict | list | tuple | None = None,
    properties: str | Iterable[str] | None = None,
    skip_geometry: bool = True,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = SERIES_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """Manually collected site-visit readings — the ``field-measurements``
    collection (old ``/nwis/gwlevels`` and ``/measurements``): groundwater
    levels, gage height and discharge measurements used to calibrate the
    sensors.

    ``time`` is the visit date (``YYYY-MM-DD``); ``time_of_day`` carries the
    clock time when known. Sorted by ``(monitoring_location_id, time)``
    (these rows have no ``time_series_id``).
    """
    return _series(
        "field-measurements",
        monitoring_location_id=monitoring_location_id,
        parameter_code=parameter_code,
        start=start,
        end=end,
        bbox=bbox,
        properties=properties,
        skip_geometry=skip_geometry,
        filter=filter,
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **filters,
    )


def get_peaks(
    monitoring_location_id: str | int | list,
    start: str | _dt.date | _dt.datetime | None = None,
    end: str | _dt.date | _dt.datetime | None = None,
    *,
    properties: str | Iterable[str] | None = None,
    skip_geometry: bool = True,
    filter: str | None = None,
    max_items: int | None = DEFAULT_MAX_ITEMS,
    page_size: int = SERIES_PAGE_SIZE,
    api_key: str | None = None,
    timeout: int | None = None,
    **filters: object,
) -> dict:
    """Annual peak streamflow — the ``peaks`` collection (old ``/nwis/peak``).

    One Feature per water year with the peak ``value`` (ft³/s), its date in
    ``time``, ``water_year``, and a ``qualifier`` list (e.g. ``["REGULATED"]``).
    Sorted by time.

    ``start``/``end`` work as everywhere else, but ``peaks`` rejects the OGC
    ``datetime`` parameter ("datetime query not supported", verified
    2026-09-14), so they are sent as a CQL2 ``time`` filter instead.
    """
    return _series(
        "peaks",
        monitoring_location_id=monitoring_location_id,
        parameter_code=None,
        properties=properties,
        skip_geometry=skip_geometry,
        filter=_and(filter, _time_filter(start, end)),
        max_items=max_items,
        page_size=page_size,
        api_key=api_key,
        timeout=timeout,
        **filters,
    )


# ---------------------------------------------------------------------------
# Tabular output
# ---------------------------------------------------------------------------


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def to_rows(fc: dict, numeric: Iterable[str] = ("value",)) -> list[dict]:
    """Flatten a FeatureCollection into one plain dict per Feature.

    Each row is the Feature's ``properties`` (copied), with the Feature's
    ``id`` added under ``"id"`` when the properties don't already carry one,
    and every key named in ``numeric`` converted with ``float()`` — ``None``
    when the value is missing or non-numeric (the API sends ``value`` as a
    string, and a qualifier such as ``"Ice"`` can stand in for a number).
    Rows are sorted by ``(time_series_id, time)`` like the series functions.

    A flat list of dicts feeds ``csv.DictWriter``, ``pandas.DataFrame(rows)``
    and plotting directly, and keeps ``qualifier`` / ``approval_status``
    alongside the value. Group by ``time_series_id`` with
    ``itertools.groupby`` if a per-series shape is wanted.
    """
    numeric = tuple(numeric)
    rows: list[dict] = []
    for feature in fc.get("features", []):
        props = dict(feature.get("properties") or {})
        if "id" not in props and feature.get("id") is not None:
            props["id"] = feature["id"]
        for key in numeric:
            if key in props:
                props[key] = _to_float(props[key])
        rows.append(props)
    rows.sort(key=_series_key)
    return rows
