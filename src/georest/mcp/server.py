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
from georest.cloud import stac
from georest.esri import edw, services
from georest.esri import portal as esri_portal
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
Service (feature_service_query).

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

Imagery and gridded data (STAC): stac_collections finds a catalog's
collections (leave catalog blank to list the catalogs), stac_search finds the
scenes over a place and a time, and stac_assets lists one scene's files.
These return links and metadata, never pixels; a worker that can read
rasters opens the links. To find imagery over a fire, give stac_search the
same service, layer and where you used with edw_query (perimeter_service,
perimeter_layer, perimeter_where); it fetches the shape itself, so do not
look for one. Anywhere else, give a bbox. If truncated is true there were
more scenes than max_items. An empty result usually means a wrong collection
id, a too-narrow date range or too strict a max_cloud_cover: check ids with
stac_collections. Each file has an access value: "open" links can be read
as is; "sign" links need georest.cloud.stac.sign in the worker that reads
them; "earthdata" and "requester-pays" links need that worker's own
credentials. Never ask a user for a token.

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


# ── Cloud-native data: STAC discovery ──────────────────────────────────
# Links and metadata only. Reading the pixels is for the caller's own
# rasterio, odc-stac or zarr; signing is not offered, because a signed link
# carries a live read token.

_MAX_SCENES = 500  # a scene row is ~400 bytes; this keeps one reply in the tens of KB
_MAX_PERIMETER_FEATURES = 20


def _stac_catalog(catalog: str) -> str:
    """A registry name as is; a STAC root URL only if it is public https."""
    catalog = (catalog or "").strip()
    if catalog.lower().startswith(("http://", "https://")):
        return public_https_url(catalog)
    return catalog


@tool
def stac_collections(catalog: str = "", query: str = "", max_items: int = 200) -> str:
    """Find STAC catalogs, or the collections (datasets) in one.

    Leave catalog blank to list the catalogs georest knows (Earth Search,
    Planetary Computer, USGS LandsatLook, NASA), with how their files are
    read. Name a catalog to list its collections, those that matter for
    forest and land work first. Use the ids it returns in stac_search.

    Args:
        catalog: A catalog name from the blank-catalog listing, or the root
            https URL of any STAC API. Blank lists the catalogs.
        query: Words to filter collections by ("landsat", "canopy").
        max_items: The most collections to return.

    Returns:
        JSON rows. For collections: catalog, id, title, summary, license,
        start, end and more; truncated=true says there were more.
    """

    if not (catalog or "").strip():
        return answer(records(stac.catalogs()))
    df = stac.collections(_stac_catalog(catalog), query, max_items=int(max_items))
    return answer(records(df), truncated=bool(df.attrs.get("truncated")),
                  catalog=df.attrs.get("catalog", catalog))


@tool
def stac_search(catalog: str, collections: list[str], bbox: Optional[list[float]] = None,
                perimeter_service: str = "", perimeter_layer: Optional[int] = None,
                perimeter_where: str = "1=1", datetime: str = "",
                max_cloud_cover: Optional[float] = None, max_items: int = 50) -> str:
    """Find satellite or aerial scenes (STAC items) over a place and a time.

    Returns links and metadata, never pixels: one row per scene, newest
    first. Use stac_assets for a scene's files.

    Give the place as a bbox, or as a perimeter in an EDW layer (the same
    service, layer and where you would use with edw_query, such as a fire
    from EDW_MTBS_01); the shape is fetched here, simplified, and the
    search covers it. Not both. A where matching no feature, or more than
    20, is refused: narrow it.

    An empty result usually means a wrong collection id, a too-narrow
    datetime or too strict a max_cloud_cover; check ids with
    stac_collections.

    Args:
        catalog: A name from stac_collections ("earth-search",
            "planetary-computer"), or the root https URL of a STAC API.
        collections: Collection ids, e.g. ["sentinel-2-l2a"].
        bbox: [west, south, east, north] in decimal degrees.
        perimeter_service: EDW service_name, e.g. "EDW_MTBS_01".
        perimeter_layer: The layer id from edw_layers.
        perimeter_where: SQL filter selecting the perimeter, e.g.
            "fire_name = 'CAMERON PEAK'".
        datetime: When captured: "2024-06-01/2024-09-30", or one day
            "2024-07-15". Blank for any time.
        max_cloud_cover: Most cloud cover to accept, percent.
        max_items: The most scenes to return (at most 500).

    Returns:
        JSON rows: catalog, collection, id, datetime, cloud_cover, platform,
        assets (how files are read), thumbnail, bbox. truncated=true says
        there were more scenes than max_items; matched is how many the
        server counted, when it says.
    """

    has_shape = bool(perimeter_service) or perimeter_layer is not None
    if bbox is not None and has_shape:
        raise ValueError("give a bbox or a perimeter (perimeter_service, perimeter_layer, "
                         "perimeter_where), not both")
    if bbox is None and not has_shape:
        raise ValueError("give a bbox, or a perimeter (perimeter_service and perimeter_layer)")
    if not isinstance(collections, (list, tuple)) or not collections:
        raise ValueError('collections is a list of collection ids, e.g. ["sentinel-2-l2a"]; '
                         "find ids with stac_collections")
    place: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    if bbox is not None:
        place["bbox"] = _bbox(bbox)
    else:
        if not perimeter_service or perimeter_layer is None:
            raise ValueError("a perimeter needs both perimeter_service and perimeter_layer")
        fc = edw.query_features(perimeter_service, int(perimeter_layer),
                                where=perimeter_where or "1=1", out_fields="objectid",
                                max_features=_MAX_PERIMETER_FEATURES, max_allowable_offset=0.001)
        found = len(fc.get("features") or [])
        if found == 0:
            raise ValueError(f"perimeter_where matched no feature in {perimeter_service} layer "
                             f"{perimeter_layer}; check the filter with edw_query")
        if found >= _MAX_PERIMETER_FEATURES or fc.get("exceededTransferLimit"):
            raise ValueError(f"perimeter_where matched {_MAX_PERIMETER_FEATURES} or more "
                             f"features; use a narrower perimeter_where (one fire, one year)")
        place["intersects"] = fc
        meta["perimeter_features"] = found
    limit = max(1, int(max_items))
    if limit > _MAX_SCENES:
        meta["note"] = f"max_items was lowered from {limit} to {_MAX_SCENES}; narrow the search instead"
        limit = _MAX_SCENES
    df = stac.search(_stac_catalog(catalog), list(collections), datetime=datetime or None,
                     max_cloud_cover=max_cloud_cover, max_items=limit, **place)
    attrs = dict(df.attrs)
    rows = records(df.drop(columns=["geometry"], errors="ignore"))
    if not rows:
        meta["hint"] = ("No scenes. Check the collection ids with stac_collections, widen the "
                        "datetime, or raise max_cloud_cover.")
    return answer(rows, truncated=bool(attrs.get("truncated")), matched=attrs.get("matched"),
                  catalog=attrs.get("catalog", catalog), **meta)


@tool
def stac_assets(catalog: str, collection: str, item: str) -> str:
    """The files of one scene: bands, previews, metadata, with links.

    Links are returned unsigned. Check each file's access: "open" can be
    read as is; "sign" needs georest.cloud.stac.sign in the worker that
    reads it; "earthdata" and "requester-pays" need that worker's own
    credentials.

    Args:
        catalog: The catalog, as given to stac_search.
        collection: The scene's collection (a stac_search row's collection).
        item: The scene's id (a stac_search row's id).

    Returns:
        JSON rows: key (e.g. "B04", "visual"), title, type, roles, access, href.
    """

    return answer(records(stac.assets(_stac_catalog(catalog), collection, item, sign=False)),
                  collection=collection, item=item)


def main() -> None:
    """Entry point for ``georest-mcp``: serve over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
