"""The georest MCP server: georest's data clients as agent tools.

Run it with ``georest-mcp`` (or ``python -m georest.mcp``); it speaks MCP over
stdio. Register it with an MCP client the usual way, e.g. for Claude Code::

    {"mcpServers": {"georest": {"command": "georest-mcp"}}}

Design rules, which the tests hold it to:

- **Data tools only.** Every tool calls one georest function and returns its
  result; there is no code execution and no file access.
- **Whole results.** A tool returns every row it got, as JSON. Where an API
  could return a great deal, the tool takes the API's own limit as a
  parameter (``max_items``, ``max_features``, ``limit``) and says in the
  result when that limit stopped the fetch early; earthquake searches count
  first and refuse a search larger than ``max_events`` rather than send part
  of it. Nothing is cut off afterwards.
- **Errors are answers.** A failure comes back as ``{"error": ...}`` with
  what to change, never as a traceback, so the model can correct and retry.
- **Public URLs only.** Tools that take a service URL accept only ``https``
  addresses that resolve to public IP addresses, so a hosted server cannot
  be pointed at its own network or a cloud metadata endpoint.
- **No secrets in tool schemas.** The USGS API key is read from
  ``USGS_API_KEY`` in the server's environment; ArcGIS sign-in, when needed,
  is done once with ``georest-login`` in a terminal.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import functools
import ipaddress
import json
import math
import socket
import urllib.parse
from typing import Any, Optional

# Every import the tools need happens here, when the server starts, and none
# inside a tool. A first-time import on the call path (pandas, above all)
# lost that call's reply: the client waited, timed out, and every later call
# worked (reproduced 2026-10-28 with a one-tool FastMCP server importing
# pandas on first use). geeViz's server hit the same import-lock trap.
# tests/test_mcp_server.py holds the tools to it.
import pandas as pd
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import georest
from georest.esri import edw, services
from georest.esri import portal as esri_portal
from georest.fema import nfhl, openfema
from georest.noaa import coops, nhc, nwps, nws
from georest.usfs import fia, lcms
from georest.usgs import earthquakes as usgs_earthquakes
from georest.usgs import nationalmap, nldi, sciencebase, waterdata, wqp

INSTRUCTIONS = """georest: US federal geospatial data, as tools.

Forest Service: FIA forest inventory estimates (search_fia, then fia_estimate)
and LCMS land cover / land use / change summaries (lcms_summary). USGS: water
data (water_sites, water_series, water_values), the river network
(river_sites), elevation, earthquakes, ScienceBase data releases and the Water
Quality Portal (water_quality). ArcGIS: the Forest Service Enterprise Data
Warehouse (edw_search, edw_query), portal search, and any public Feature
Service (feature_service_query). FEMA and NOAA: below.

Rules for FIA (fia_estimate):
- FIA is a probability sample, not a census. Never report an estimate
  without its sampling error (se_pct) and plot count.
- Do not report a row where unreliable is true; say it is too uncertain and
  give unreliable_reason. A cell with 0 plots shows a 0% error: that means
  no information, not perfect precision.
- A total over several rows comes from FIA, not from adding rows: filter
  with str_filter and report the Total row.

LCMS areas are from a classified map; FIA areas are from a sample. Report
them separately and say which is which; do not equate them.

FEMA and NOAA: the flood zone at a point with its map's date (flood_zone),
federal disaster declarations (disaster_declarations), NWS alerts, forecasts
and current conditions (weather_alerts, weather_forecast, weather_now), river
forecast gauges (river_gauges, river_gauge), tides (tide_stations, tides) and
active tropical cyclones (hurricanes).

Rules for hazards:
- A FEMA flood zone is a regulatory map, not a forecast: outside the
  floodplain is not "will not flood". Give the map's effective date, and
  say so when FEMA has new maps pending or preliminary. mapped false means
  FEMA has no map there, not that there is no flood hazard.
- No alerts, no gauge at flood stage, no storm: say the source lists none
  right now, never that there is no hazard.
- Readings and forecasts carry their times; say how old a reading is.
- disaster_declarations by="county" is one row per declared county: count
  disasters with by="disaster".

Tools return every row as JSON. When a result is too large, narrow the query
(a smaller area, a shorter time window, fewer parameters) rather than asking
for less detail afterwards."""

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

mcp = FastMCP("georest", instructions=INSTRUCTIONS)


# ── Plumbing ────────────────────────────────────────────────────────────

def _clean(value: Any) -> Any:
    """JSON-safe: NaN and infinities become null, containers recurse."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    try:  # pandas / numpy missing values (NaT, pd.NA, numpy nan)
        if not isinstance(value, (str, bytes)) and pd.api.types.is_scalar(value) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item") and callable(value.item):  # numpy scalars
        try:
            return _clean(value.item())
        except (TypeError, ValueError):
            pass
    return value


def records(data: Any) -> list[dict]:
    """Every row of a DataFrame, or a list of records, as plain dicts."""
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        return [_clean(r) for r in data.to_dict("records")]
    return [_clean(r) for r in data]


def dumps(payload: dict) -> str:
    return json.dumps(_clean(payload), default=str, ensure_ascii=False)


def answer(rows: list[dict], **meta: Any) -> str:
    """The standard result: how many rows, anything the caller should know, the rows."""
    return dumps({"count": len(rows), **meta, "rows": rows})


def tool(fn):
    """Register a read-only tool whose failures come back as ``{"error": ...}``."""
    @functools.wraps(fn)
    def safe(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError, LookupError) as exc:
            return dumps({"error": f"{type(exc).__name__}: {exc}",
                          "hint": "Check the arguments; the message names the problem."})
        except Exception as exc:  # upstream outages, HTTP errors
            return dumps({"error": f"{type(exc).__name__}: {exc}",
                          "hint": "The upstream service failed or refused the request; "
                                  "retry later or narrow the query."})
    return mcp.tool(annotations=_READ_ONLY)(safe)


def public_https_url(url: str) -> str:
    """``url`` if it is https and every address its host resolves to is public."""
    parsed = urllib.parse.urlparse(str(url).strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("give a public https:// service URL")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve {parsed.hostname}: {exc}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"{parsed.hostname} is not a public address")
    return parsed.geturl()


def _bbox(bbox: Optional[list[float]]) -> Optional[tuple[float, float, float, float]]:
    if bbox is None:
        return None
    if len(bbox) != 4:
        raise ValueError("bbox is [west, south, east, north] in decimal degrees")
    west, south, east, north = (float(v) for v in bbox)
    if west >= east or south >= north:
        raise ValueError("bbox is [west, south, east, north]: west < east and south < north")
    return west, south, east, north


# ── Forest Service: FIA ────────────────────────────────────────────────

@tool
def search_fia(query: str = "", kind: str = "attributes", state: str = "",
               growth_only: bool = False, land_basis: str = "",
               limit: Optional[int] = 25) -> str:
    """Search the FIA vocabulary: what can be estimated, how to break it out, which inventory.

    Call this before fia_estimate, always: an estimate takes an attribute,
    up to two groupings and an evaluation, drawn from about 750, 100 and
    1,100 options, and guessing one gives an opaque server error.
    Answered from catalogs bundled with georest: instant, and works while
    FIADB-API is down.

    Args:
        query: Free text. For attributes, matched against the description
            ("carbon", "net growth volume", "mortality"); for groupings,
            against the label ("species", "county", "ownership").
        kind: "attributes" (what to estimate), "groupings" (how to break it
            out) or "evaluations" (which inventory).
        state: For evaluations: a state name ("Oregon").
        growth_only: For evaluations: keep only those with growth
            accounting, which every growth, removals and mortality attribute
            needs.
        land_basis: For attributes: "Forest land" or "Timberland".
        limit: How many of the best matches to search for (the search's
            own parameter; null for every match).

    Returns:
        JSON rows. Attributes carry snum, description, units, eval_typ;
        groupings carry the exact label fia_estimate needs; evaluations
        carry wc and growth accounting.
    """

    kind = (kind or "attributes").lower()
    if kind.startswith("attr"):
        df = fia.find_attributes(query, land_basis=land_basis, limit=limit)
    elif kind.startswith("group"):
        df = fia.find_groupings(query, limit=limit)
    elif kind.startswith("eval"):
        df = fia.find_evaluations(state, growth_only=growth_only, limit=limit)
    else:
        raise ValueError(f"kind must be 'attributes', 'groupings' or 'evaluations', got {kind!r}")
    return answer(records(df), kind=kind, query=query)


@tool
def describe_fia_grouping(label: str) -> str:
    """FIA's own description of a grouping variable, including what each code means.

    Args:
        label: The exact grouping label from search_fia(kind="groupings").
    """

    return dumps({"label": label, "description": fia.describe_grouping(label)})


@tool
def fia_estimate(wc: list[int], snum: int, rselected: str = "", cselected: str = "",
                 str_filter: str = "", forest_definition: str = "FIADEF") -> str:
    """Run an FIA estimate. Every value comes WITH its sampling error.

    Get wc, snum and the grouping labels from search_fia first: groupings
    are exact display strings, so a near miss fails.

    You must not report any value from this tool without its standard
    error. FIA is a probability sample, not a census. Rows carry se_pct and
    plots, and unreliable is set when a cell is too thin to report (SE over
    30% or fewer than 30 plots). A zero-plot cell reports a ZERO standard
    error, which means no information, not maximum precision.

    Do not report a row where unreliable is true: say it is too uncertain
    and name why from unreliable_reason.

    A total over several rows comes from FIA, not arithmetic: rows of one
    evaluation can share strata, so adding them gives a wrong error. To
    estimate a region made of several rows (units, counties, owners),
    restrict the query to them with str_filter and report the Total row.
    Several evaluations (several states) in one wc list are estimated
    together, which is the only correct way to combine states.

    Args:
        wc: Evaluation group(s) from search_fia(kind="evaluations"), e.g.
            [412023]; several for a combined estimate.
        snum: Attribute number from search_fia(kind="attributes").
        rselected: Row grouping: an exact label from search_fia.
        cselected: Column grouping. Optional; cross-tabulating thins the
            plots per cell fast, so expect more unreliable rows.
        str_filter: SQL-style domain filter, e.g. "plot.unitcd in (0, 1)".
            Qualify columns with the full table name (plot., cond., tree.)
            and filter on stored codes, not row labels.
        forest_definition: "FIADEF" (FIA's definition, the default) or
            "RPADEF" (the Resources Planning Act's), which counts different
            land as forest.

    Returns:
        JSON with every row, reliable_count / unreliable_count, the forest
        definition applied, and any warnings FIADB-API attached.
    """

    wcs = list(wc) if isinstance(wc, (list, tuple)) else [wc]
    df = fia.estimate(wcs if len(wcs) > 1 else wcs[0], snum, rselected=rselected,
                      cselected=cselected, str_filter=str_filter,
                      forest_definition=forest_definition)
    rows = records(df)
    bad = sum(1 for r in rows if r.get("unreliable"))
    return answer(rows, reliable_count=len(rows) - bad, unreliable_count=bad,
                  forest_definition=forest_definition,
                  warnings=list(getattr(df, "attrs", {}).get("warnings") or []),
                  note="Every value carries se_pct and plots. Do not report a row with "
                       "unreliable=true; give unreliable_reason instead.")


# ── Forest Service: LCMS ───────────────────────────────────────────────

@tool
def lcms_summary(product: str = "Land_Cover", state: str = "", county: str = "",
                 region: str = "", forest: str = "", district: str = "",
                 year: Optional[int] = None, start_year: Optional[int] = None,
                 end_year: Optional[int] = None) -> str:
    """LCMS land cover, land use or change, by year, for a named area.

    Wall-to-wall 30 m maps from 1985. Complements FIA: LCMS answers what
    changed and where; FIA answers what is there, plus or minus error. Do
    not equate their areas: one is a classified map, the other a sample.

    Only precomputed areas: every county, every ranger district, national
    forests and regions, and CONUS rollups.

    Args:
        product: "Land_Cover", "Land_Use" or "Change".
        state, county: A county, by state and county name.
        region, forest, district: Forest Service units; district needs forest.
        year: One year; or start_year / end_year for a range; neither for all.

    Returns:
        JSON rows of year, class, acres and hectares.
    """

    df = lcms.lcms_summary(product, state=state, county=county, region=region,
                           forest=forest, district=district, year=year,
                           startyear=start_year, endyear=end_year)
    return answer(records(df), product=product)


# ── USGS: water data ───────────────────────────────────────────────────

@tool
def water_sites(bbox: Optional[list[float]] = None, state_code: str = "",
                county_code: str = "", site_type_code: str = "",
                hydrologic_unit_code: str = "", max_items: int = 1000) -> str:
    """USGS monitoring locations (stream gages, wells, springs, lakes).

    Args:
        bbox: [west, south, east, north] in decimal degrees.
        state_code: Two-digit state FIPS code ("49" Utah, "08" Colorado).
        county_code: Three-digit county FIPS code, with state_code.
        site_type_code: "ST" stream, "LK" lake, "GW" well, "SP" spring.
        hydrologic_unit_code: A HUC; prefix-matched, so "1407" finds every
            site below that HUC4.
        max_items: Most sites to fetch (the API's limit); the result says
            truncated=true when there were more.

    Returns:
        JSON rows: id ("USGS-09380000"), name, site type, state, county,
        HUC, drainage area, lon, lat.
    """

    filters = {k: v for k, v in {"state_code": state_code, "county_code": county_code,
                                 "site_type_code": site_type_code,
                                 "hydrologic_unit_code": hydrologic_unit_code}.items() if v}
    if not filters and bbox is None:
        raise ValueError("give a bbox, state_code, or hydrologic_unit_code")
    df = waterdata.search_monitoring_locations(bbox=_bbox(bbox), max_items=max_items, **filters)
    return answer(records(df), truncated=bool(df.attrs.get("truncated")))


@tool
def water_series(site: str) -> str:
    """What a USGS site measures: each parameter and statistic, with its dates.

    Check this before water_values, so you ask for a parameter the site
    actually reports.

    Args:
        site: Site id, "USGS-09380000" or "09380000".
    """

    df = waterdata.get_time_series_metadata(monitoring_location_id=site)
    return answer(records(df))


@tool
def water_values(site: str, parameter_code: str = "00060", kind: str = "daily",
                 start: str = "", end: str = "", statistic_id: str = "00003",
                 max_items: int = 10000) -> str:
    """Observations at a USGS site.

    Args:
        site: Site id, "USGS-09380000" or "09380000".
        parameter_code: "00060" discharge (ft3/s), "00065" gage height (ft),
            "00010" water temperature (C), "72019" depth to water (ft).
        kind: "daily" (daily statistics), "continuous" (sensor readings,
            about every 15 minutes), "latest" (the newest daily value),
            "field" (measurements made on site visits) or "peaks" (each
            water year's peak flow).
        start, end: Dates, "YYYY-MM-DD"; for continuous, start may be an
            ISO duration such as "P7D" (the last week).
        statistic_id: For daily: "00003" mean, "00001" max, "00002" min.
        max_items: Most rows to fetch (the API's limit); continuous data is
            about 35,000 rows per year. truncated=true says it bit.

    Returns:
        JSON rows with time, value (a number), unit_of_measure,
        approval_status and qualifier.
    """

    kind = (kind or "daily").lower()
    window = {"start": start or None, "end": end or None}
    if kind == "daily":
        df = waterdata.get_daily_values(site, parameter_code, statistic_id, max_items=max_items, **window)
    elif kind == "continuous":
        df = waterdata.get_continuous_values(site, parameter_code, max_items=max_items, **window)
    elif kind == "latest":
        df = waterdata.get_latest_values(site, parameter_code, max_items=max_items)
    elif kind == "field":
        df = waterdata.get_field_measurements(site, parameter_code, max_items=max_items, **window)
    elif kind == "peaks":
        df = waterdata.get_peaks(site, max_items=max_items, **window)
    else:
        raise ValueError("kind must be daily, continuous, latest, field or peaks")
    return answer(records(df), kind=kind, truncated=bool(df.attrs.get("truncated")))


@tool
def river_sites(lon: float, lat: float, mode: str = "upstream_tributaries",
                distance_km: float = 50, data_source: str = "nwissite") -> str:
    """Sites along the river network from a point: gages upstream or downstream.

    Finds the river reach at the point (the NHDPlus flowline), then walks
    the network with the USGS Hydro Network-Linked Data Index.

    Args:
        lon, lat: A point on or near a river.
        mode: "upstream" (the main stem), "upstream_tributaries" (the whole
            network above), "downstream" or "downstream_diversions".
        distance_km: How far to walk.
        data_source: "nwissite" (USGS gages), "WQP" (water-quality sites),
            "ref_dams", "huc12pp", and other NLDI sources.

    Returns:
        JSON rows of the sites found, with lon and lat, plus the reach's comid.
    """

    reach = nldi.find_flowline(lon, lat)
    comid = reach.get("comid") if isinstance(reach, dict) else None
    if comid is None:
        raise ValueError("no river reach found at that point")
    df = nldi.sites_along("comid", comid, mode, data_source=data_source, distance_km=distance_km)
    return answer(records(df), comid=comid, mode=mode, distance_km=distance_km)


# ── USGS: elevation, earthquakes, ScienceBase, water quality ───────────

@tool
def elevation(points: list[list[float]], units: str = "meters") -> str:
    """Ground elevation from the USGS 3D Elevation Program at points.

    Args:
        points: [[lon, lat], ...] in decimal degrees.
        units: "meters" or "feet".
    """

    pts = [(float(p[0]), float(p[1])) for p in points]
    return answer(records(nationalmap.elevations(pts, units=units)), units=units)


@tool
def earthquakes(start: str = "", end: str = "", bbox: Optional[list[float]] = None,
                min_magnitude: Optional[float] = None, max_magnitude: Optional[float] = None,
                alert_level: str = "", max_events: int = 2000) -> str:
    """Earthquakes from the USGS catalog.

    Counts first: a search matching more than max_events is refused with
    the count, so narrow it (a smaller area, a shorter window, a higher
    min_magnitude) rather than receive part of it.

    Args:
        start, end: "YYYY-MM-DD" (the catalog's default window is the last
            30 days).
        bbox: [west, south, east, north].
        min_magnitude, max_magnitude: Magnitude range.
        alert_level: PAGER alert, "green", "yellow", "orange" or "red".
        max_events: The most events to return.

    Returns:
        JSON rows: time, magnitude, place, depth, lon, lat, alert, tsunami flag, id.
    """

    args = {"start": start or None, "end": end or None, "bbox": _bbox(bbox),
            "min_magnitude": min_magnitude, "max_magnitude": max_magnitude,
            "alert_level": alert_level or None}
    n = usgs_earthquakes.count(**args)
    if n > max_events:
        return dumps({"error": f"{n:,} earthquakes match, more than max_events={max_events:,}",
                      "count": n,
                      "hint": "Narrow the search: a smaller bbox, a shorter window, or a higher "
                              "min_magnitude. Or raise max_events if you need them all."})
    return answer(records(usgs_earthquakes.search(**args)))


@tool
def sciencebase_search(query: str, data_releases_only: bool = True,
                       limit: Optional[int] = 50) -> str:
    """Search ScienceBase, the USGS catalog of data releases.

    Args:
        query: Free text.
        data_releases_only: Keep only official, citable data releases.
        limit: Most items to fetch, best match first (null for every match).
    """

    return answer(records(sciencebase.search(query, data_releases_only=data_releases_only,
                                             limit=limit)), query=query)


@tool
def sciencebase_files(item_id: str) -> str:
    """The files attached to a ScienceBase item, with download links."""

    return answer(records(sciencebase.item_files(item_id)), item_id=item_id)


@tool
def water_quality(kind: str = "results", sites: Optional[list[str]] = None, state: str = "",
                  county: str = "", bbox: Optional[list[float]] = None, huc: str = "",
                  characteristic: Optional[list[str]] = None, start: str = "",
                  end: str = "") -> str:
    """Water-quality sites or results from the Water Quality Portal (USGS, EPA, states, tribes).

    Give at least one place (sites, state, county with state, bbox, huc).
    Results can be large: name a characteristic and a date window.

    Args:
        kind: "sites" (monitoring locations) or "results" (measurements).
        sites: Site ids, e.g. ["USGS-09380000"].
        state: State postal code ("CO") or FIPS code ("08").
        county: Three-digit county FIPS code, with state.
        bbox: [west, south, east, north].
        huc: A hydrologic unit code.
        characteristic: What was measured, e.g. ["Nitrate"],
            ["Temperature, water"], ["pH"].
        start, end: "YYYY-MM-DD", for results.

    Returns:
        JSON rows with every portal column plus lon, lat and, for results,
        date, value (a number) and unit.
    """

    place = {"sites": sites or None, "state": state or None, "county": county or None,
             "bbox": _bbox(bbox), "huc": huc or None, "characteristic": characteristic or None}
    if (kind or "").lower().startswith("site"):
        df = wqp.find_sites(**place)
    elif (kind or "").lower().startswith("result"):
        df = wqp.get_results(**place, start=start or None, end=end or None)
    else:
        raise ValueError("kind must be 'sites' or 'results'")
    return answer(records(df), kind=kind)


# ── ArcGIS: EDW, portals, Feature Services ─────────────────────────────

@tool
def edw_search(query: str = "", theme: str = "") -> str:
    """Find services in the USFS Enterprise Data Warehouse (EDW).

    Args:
        query: Keyword ("fire", "roads", "riparian"); aliases expand it.
        theme: A theme such as "transportation", "boundaries", "inland_waters".

    Returns:
        JSON rows: name (the service_name for edw_query), type, url,
        theme, description.
    """

    if not query and not theme:
        raise ValueError("give a query or a theme")
    return answer(records(edw.search_edw_services(query, theme)))


@tool
def edw_layers(service_name: str) -> str:
    """The layers of an EDW service, with their ids, for edw_query."""

    info = edw.get_service_info(service_name)
    return answer(records(info.get("layers") or []), service=service_name,
                  description=info.get("description"))


@tool
def edw_query(service_name: str, layer_id: int, where: str = "1=1",
              bbox: Optional[list[float]] = None, out_fields: str = "*",
              max_features: int = 1000, count_only: bool = False) -> str:
    """Query an EDW layer: its attribute table, filtered by attributes and/or a box.

    Returns attributes (and lon/lat for point layers), not shapes: geometry
    is for maps, and a polygon layer's shapes can run to megabytes.

    Args:
        service_name: From edw_search, e.g. "EDW_MTBS_01".
        layer_id: From edw_layers.
        where: SQL filter, e.g. "fire_name = 'CAMERON PEAK'" or "year >= 2020".
        bbox: [west, south, east, north] spatial filter.
        out_fields: Comma-separated fields, or "*".
        max_features: Most features to fetch; truncated=true says there were more.
        count_only: Just count the matches.
    """

    geometry = ",".join(str(v) for v in _bbox(bbox)) if bbox is not None else None
    if count_only:
        return dumps(edw.query_features(service_name, layer_id, geometry=geometry,
                                        where=where, return_count_only=True))
    fc = edw.query_features(service_name, layer_id, geometry=geometry, where=where,
                            out_fields=out_fields, max_features=max_features)
    df = georest.to_frame(fc)
    return answer(records(df), truncated=bool(fc.get("exceededTransferLimit")))


@tool
def portal_search(query: str, portal: str = "iipp", limit: int = 20) -> str:
    """Search an ArcGIS portal for services and items.

    Args:
        query: Free text, e.g. "naip 2023", "wildfire perimeter".
        portal: "iipp" (the federal imagery portal, the default), "agol"
            (ArcGIS Online), or a portal's https URL.
        limit: Most items to fetch.
    """

    target = portal if portal in ("iipp", "agol") else public_https_url(portal)
    return answer(records(esri_portal.searchPortal(query, portal=target, limit=limit)), query=query)


@tool
def feature_service_query(url: str, where: str = "1=1", bbox: Optional[list[float]] = None,
                          out_fields: str = "*", max_features: int = 1000) -> str:
    """Query any public ArcGIS Feature Service layer: its attribute table.

    Returns attributes (and lon/lat for points), not shapes. Counts first,
    and refuses a query matching more than max_features rather than send
    part of it.

    Args:
        url: The layer's https URL, ending in /FeatureServer/<n> or /MapServer/<n>.
        where: SQL filter.
        bbox: [west, south, east, north].
        out_fields: Comma-separated fields, or "*".
        max_features: The most features to return.
    """

    geometry = ",".join(str(v) for v in _bbox(bbox)) if bbox is not None else None
    fc = services.queryFeatureService(public_https_url(url), where=where, geometry=geometry,
                                      out_fields=out_fields, max_features=max_features)
    return answer(records(georest.to_frame(fc)))


# ── FEMA: flood maps and disaster declarations ─────────────────────────

@tool
def flood_zone(lon: float, lat: float) -> str:
    """The FEMA flood zone at a point, and when that flood map took effect.

    From FEMA's National Flood Hazard Layer: the effective Flood Insurance
    Rate Map, the one in force for insurance and permitting. A zone is a
    regulatory designation, not a forecast.

    Args:
        lon, lat: The point, in decimal degrees.

    Returns:
        JSON: zone, subtype, sfha (in the 1%-annual-chance floodplain),
        static_bfe and depth where FEMA records them, mapped (false = FEMA
        has no digital map here, not "no hazard"), and map: the panel, the
        date it took effect, any later revision, pending_effective (new
        maps adopted, with their date) and preliminary_issued (new maps
        proposed); a lookup named in map.unchecked could not be checked.
    """

    zone = nfhl.flood_zone(lon, lat)
    dates = nfhl.map_dates(lon, lat)
    zone["map"] = {k: v for k, v in dates.items() if k not in ("lon", "lat")}
    return dumps(zone)


@tool
def disaster_declarations(state: str = "", fips: str = "", incident_type: str = "",
                          declaration_type: str = "", since: str = "", until: str = "",
                          by: str = "disaster", max_rows: int = 2000) -> str:
    """Federal disaster declarations from OpenFEMA, since 1953.

    Give at least one filter. Refuses a search matching more than max_rows,
    with the count, rather than send part of it.

    Args:
        state: Two-letter state code, e.g. "TX".
        fips: 5-digit county FIPS code, e.g. "48201" (Harris County, TX).
        incident_type: OpenFEMA's type, exactly: "Hurricane", "Flood",
            "Severe Storm", "Fire", ...
        declaration_type: "DR" (major disaster), "EM" (emergency) or "FM"
            (fire management).
        since, until: Declaration dates, "YYYY-MM-DD", inclusive.
        by: "disaster" (one row per disaster, with how many declared areas
            matched) or "county" (OpenFEMA's own rows, one per declared
            county or tribal area). One disaster covers many counties:
            count disasters with by="disaster".
        max_rows: The most rows to return.
    """

    filters = {"state": state or None, "fips": fips or None,
               "incident_type": incident_type or None,
               "declaration_type": declaration_type or None,
               "since": since or None, "until": until or None}
    if not any(filters.values()):
        raise ValueError("give at least one filter: state, fips, incident_type, "
                         "declaration_type, since or until")
    if by == "disaster":
        df = openfema.disasters(**filters)
    elif by == "county":
        df = openfema.declarations(**filters)
    else:
        raise ValueError('by must be "disaster" or "county"')
    if len(df) > max_rows:
        return dumps({"error": f"{len(df):,} rows match, more than max_rows={max_rows:,}",
                      "count": len(df),
                      "hint": 'Narrow the search (a state, a county, a date window, an '
                              'incident type), use by="disaster", or raise max_rows.'})
    return answer(records(df), by=by)


# ── NOAA: weather, rivers, tides, hurricanes ───────────────────────────

def _covered(fn, *args, **kwargs):
    """Call an NWS function; a point outside the NWS is an argument problem."""
    try:
        return fn(*args, **kwargs)
    except nws.NotCovered as exc:
        raise LookupError(f"{exc}. The NWS covers the US, its territories and "
                          f"nearby waters.") from None


@tool
def weather_alerts(lon: Optional[float] = None, lat: Optional[float] = None, state: str = "",
                   event: str = "", severity: str = "") -> str:
    """Active National Weather Service watches, warnings and advisories.

    Args:
        lon, lat: A point. Every alert returned is in effect there: the NWS
            matched it to the point, whatever its area text lists.
        state: A state or marine area code ("TX", "GM"). Neither a point
            nor a state means the whole country.
        event: An event name, e.g. "Flood Warning".
        severity: "Extreme", "Severe", "Moderate" or "Minor".

    Returns:
        JSON rows: event, severity, urgency, certainty, headline, area,
        sender, onset, expires, zones and has_polygon (false = issued for
        forecast zones, not drawn as a polygon). No rows means the NWS
        lists no alert there right now, not that there is no hazard.
    """

    point = None
    if lon is not None or lat is not None:
        if lon is None or lat is None:
            raise ValueError("give both lon and lat, or neither")
        point = (lon, lat)
    df = nws.alerts(point=point, area=state or None, event=event or None,
                    severity=severity or None)
    scope = "point" if point else (state.upper() if state else "nationwide")
    return answer(records(df), scope=scope)


@tool
def weather_forecast(lon: float, lat: float, units: str = "us", hourly: bool = False) -> str:
    """The National Weather Service forecast at a point, one row per period.

    Args:
        lon, lat: The point, in decimal degrees.
        units: "us" (degrees F, mph) or "si" (degrees C, km/h); the unit is
            in each column's name.
        hourly: Hourly periods instead of day and night.
    """

    return answer(records(_covered(nws.forecast, lon, lat, units=units, hourly=hourly)),
                  units=units)


@tool
def weather_now(lon: float, lat: float, stations: int = 3, units: str = "us",
                max_age_minutes: float = 90) -> str:
    """Current conditions: the latest readings at the weather stations nearest a point.

    Readings older than max_age_minutes are not current and are left out;
    dropped_stale and failed name the stations left out, so a short answer
    can be told from a quiet one.

    Args:
        lon, lat: The point, in decimal degrees.
        stations: How many stations with a current reading.
        units: "us" or "si", as for weather_forecast.
        max_age_minutes: The oldest reading to count as current.

    Returns:
        JSON rows, nearest station first: station, distance_km,
        observed_at, age_min, conditions, the readings, and qc (readings
        the NWS has not validated).
    """

    df = _covered(nws.observations, lon, lat, n=stations, units=units,
                  max_age_minutes=max_age_minutes)
    return answer(records(df), units=units, dropped_stale=df.attrs.get("dropped_stale", []),
                  failed=df.attrs.get("failed", []))


@tool
def river_gauges(bbox: list[float], flooding_only: bool = False) -> str:
    """NOAA river forecast gauges in a box: the stage now, its flood category, the forecast crest.

    Args:
        bbox: [west, south, east, north].
        flooding_only: Only gauges at a flood category now (action stage or above).

    Returns:
        JSON rows: lid (the gauge id), name, lon, lat, stage and its unit,
        flood_category, observed_at, and forecast_stage, forecast_category
        and forecast_at (null when the forecast is not current - never read
        a forecast crest as the stage now). flooding_now counts gauges at a
        flood category.
    """

    df = nwps.gauges(_bbox(bbox), flooding_only=flooding_only)
    return answer(records(df), flooding_now=df.attrs.get("flooding_now"))


@tool
def river_gauge(lid: str) -> str:
    """One NOAA river gauge in full, with the stages at which it floods.

    Args:
        lid: The gauge id, e.g. "BBST2" (the lid from river_gauges).

    Returns:
        JSON: where it is, the latest stage and forecast crest, and
        flood_stages - the stage for action, minor, moderate and major
        flooding (null where the NWS has not set one).
    """

    return dumps(nwps.gauge(lid))


@tool
def tide_stations(lon: float, lat: float, n: int = 3, max_km: float = 100) -> str:
    """The NOAA tide stations (CO-OPS) nearest a point.

    Args:
        lon, lat: The point, in decimal degrees.
        n: How many stations.
        max_km: Leave out stations farther than this: a gauge far away says
            nothing about the water at the point.
    """

    return answer(records(coops.nearest(lon, lat, n=n, max_km=max_km)))


@tool
def tides(station: str, kind: str = "measured", hours: int = 24, begin: str = "", end: str = "",
          interval: str = "hilo", datum: str = "MLLW", units: str = "english") -> str:
    """Water levels at a NOAA tide station: measured, or the tide predictions.

    Args:
        station: The CO-OPS station id, e.g. "8771450" (from tide_stations).
        kind: "measured" (the last `hours`, oldest first) or "predicted"
            (high and low tides between begin and end).
        hours: For measured: how far back, up to 720.
        begin, end: For predicted: "YYYY-MM-DD".
        interval: For predicted: "hilo" (high and low), "h" (hourly) or minutes.
        datum: The height reference, "MLLW" by default. A level means
            nothing without its datum; say which.
        units: "english" (feet) or "metric" (meters).

    Returns:
        JSON rows with time (UTC) and the level; measured readings carry
        quality ("p" preliminary, "v" verified). Predictions are modelled
        astronomical tides: weather can push the water well above them.
    """

    if kind == "measured":
        df = coops.water_levels(station, hours=hours, datum=datum, units=units)
    elif kind == "predicted":
        if not (begin and end):
            raise ValueError('kind="predicted" needs begin and end ("YYYY-MM-DD")')
        df = coops.predictions(station, begin, end, interval=interval, datum=datum, units=units)
    else:
        raise ValueError('kind must be "measured" or "predicted"')
    return answer(records(df), kind=kind, station=station, name=df.attrs.get("name"),
                  datum=datum, units=units)


@tool
def hurricanes() -> str:
    """The tropical cyclones the National Hurricane Center is advising on now.

    Returns:
        JSON rows: id, name, classification and its name, wind_kt,
        pressure_mb, lon, lat, motion, updated, and links to the public
        advisory and the forecast cone. No rows means the NHC is advising on
        no storm right now.
    """

    return answer(records(nhc.active_storms()))


def main() -> None:
    """Entry point for ``georest-mcp``: serve over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
