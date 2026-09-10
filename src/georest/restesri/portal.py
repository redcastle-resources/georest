"""
ArcGIS / Esri Portal search and service metadata client.

Ported from geeViz's esriLib.py, with the geeViz-viewer rendering functions
(addEsriImageService, addEsriMapService, addEsriFeatureService, addEsriService)
removed — this module only fetches data, it does not render into any viewer.

Quick start::

    from georest.restesri import portal

    # Discover data on any ArcGIS Portal
    results = portal.searchPortal("naip 2023")                  # IIPP (default)
    results = portal.searchPortal("naip 2023", portal="agol")   # ArcGIS Online
    results = portal.searchPortal("naip 2023",
                                  portal="https://myagency.gov/portal")

    # Available portals
    portal.PORTALS.keys()   # iipp, agol, usgs, noaa, usfs, nasa

    # Inspect any service
    meta = portal.getServiceMetadata("https://.../ImageServer")

Token-gated portals::

    # Obtain a token first:
    #   POST <portal>/sharing/rest/generateToken
    #     username=...&password=...&client=requestip&expiration=60&f=json
    token = "..."
    portal.searchPortal("classified data", token=token)

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from typing import Any

from ._http import build_params, fetch_json, format_esri_error

# ---------------------------------------------------------------------------
# Known public portals
# ---------------------------------------------------------------------------

PORTALS: dict[str, str] = {
    "iipp": "https://imagery.geoplatform.gov/iipp",
    "agol": "https://www.arcgis.com",
    "usgs": "https://www.sciencebase.gov/sciencebase",
    "noaa": "https://coastalatlas.noaa.gov",
    "usfs": "https://data.fs.usda.gov/geodata",
    "nasa": "https://nasa.maps.arcgis.com",
}
"""Module-level dict mapping short names to portal base URLs.

Add your own at runtime::

    from portal import PORTALS
    PORTALS["myagency"] = "https://gis.myagency.gov/portal"
"""

# Non-data item types that clutter portal search results.  Applied when
# data_only=True (the default).  This mirrors the exclusion list used by
# the IIPP search UI.
_DATA_ONLY_EXCLUSIONS: list[str] = [
    "Style",
    "Layer",
    "Map Document",
    "Map Package",
    "Basemap",
    "Mobile Basemap Package",
    "Web Scene",
    "CityEngine Web Scene",
    "Pro Map",
    "Project Package",
    "Task File",
    "Operations Dashboard Add In",
    "Application",
    "Web Mapping Application",
    "Mobile Application",
    "Code Sample",
    "Symbol Set",
    "Color Set",
    "Windows Viewer Add In",
    "Windows Viewer Configuration",
    "Map Area",
    "Insights Workbook",
    "Insights Page",
    "Insights Model",
    "Hub Initiative",
    "Hub Site Application",
    "Hub Page",
    "Hub Project",
    "Experience Builder Widget",
    "Dashboard",
    "StoryMap",
    "Survey123 Add In",
    "Compact Tile Package",
]


def _resolve_portal(portal: str) -> str:
    """Resolve a portal argument to a base URL.

    Args:
        portal (str): Either a short name from :data:`PORTALS` (e.g.
            ``"iipp"``, ``"agol"``) or a full URL
            (e.g. ``"https://gis.myagency.gov/portal"``).

    Returns:
        str: Portal base URL with no trailing slash.

    Raises:
        KeyError: If a short name is given but not found in :data:`PORTALS`.
    """
    if portal.startswith("http://") or portal.startswith("https://"):
        return portal.rstrip("/")
    if portal in PORTALS:
        return PORTALS[portal].rstrip("/")
    known = ", ".join(f'"{k}"' for k in PORTALS)
    raise KeyError(
        f"Unknown portal short name {portal!r}.  Known names: {known}.  "
        f"Pass a full URL or add your portal to PORTALS first: "
        f'PORTALS["{portal}"] = "https://..."'
    )


# ---------------------------------------------------------------------------
# Portal search
# ---------------------------------------------------------------------------

def searchPortal(
    query: str,
    portal: str = "iipp",
    limit: int = 20,
    data_only: bool = True,
    raw_q: str | None = None,
    token: str | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """Search any ArcGIS Portal for hosted services. Defaults to IIPP.

    Uses the standard ``/sharing/rest/search`` endpoint present on ArcGIS
    Online, IIPP, and any ArcGIS Enterprise install. This is a raw portal
    search — it has no theme/keyword-alias matching. To search the USFS EDW
    catalog specifically (a separate, non-portal REST endpoint), use
    ``georest.restesri.edw.search_edw_services(query, theme=...)`` instead.

    Args:
        query (str): Free-text search query (e.g. ``"naip 2023"``,
            ``"fire perimeter"``).
        portal (str, optional): Either a short name from :data:`PORTALS`
            (``"iipp"``, ``"agol"``, ``"usgs"``, ``"noaa"``, ``"usfs"``,
            ``"nasa"``) or a full portal base URL.  Defaults to ``"iipp"``.
        limit (int, optional): Maximum results to return (1–100).
            Defaults to 20.
        data_only (bool, optional): When ``True`` (default), appends a
            bundled exclusion list that filters out non-data items (styles,
            web apps, dashboards, etc.) so results are datasets only.
            Set to ``False`` to search without restrictions.
        raw_q (str, optional): If supplied, overrides the assembled query
            string entirely — ignores ``query``, ``data_only``, and
            ``filters``.  Use for portal query DSL power users.
        token (str, optional): ArcGIS token for secured portals.  Omit for
            public services.  Obtain via
            ``POST <portal>/sharing/rest/generateToken``.
        **filters: Extra ArcGIS search filters forwarded verbatim as query
            params (e.g. ``sortField="title"``, ``sortOrder="asc"``,
            ``bbox="-120,35,-110,42"``).

    Returns:
        list of dict: Parsed portal items.  Each dict includes:

        - ``id`` (str): Item ID.
        - ``title`` (str): Item title.
        - ``type`` (str): Esri item type (e.g. ``"Image Service"``,
          ``"Feature Service"``).
        - ``snippet`` (str): Short description.
        - ``tags`` (list of str): Associated tags.
        - ``url`` (str): Service endpoint URL (may be ``""`` if not set).
        - ``owner`` (str): Portal username of the owner.
        - ``created`` (int): Unix timestamp (ms) of item creation.
        - ``modified`` (int): Unix timestamp (ms) of last modification.
        - ``thumbnail`` (str or None): Thumbnail URL, or ``None`` if absent.
        - ``_raw`` (dict): Full raw portal item dict for advanced access.

        An empty list means the search genuinely matched nothing — a
        rejected search raises rather than returning ``[]`` (see below).

    Raises:
        ValueError: If the portal answers 200 with an error object — most
            commonly code 498, an invalid or expired *token* — or with a
            body that isn't JSON. This case used to be swallowed into an
            empty result list, which read as "no matches".
        RuntimeError: If the portal is unreachable or answers with an HTTP
            error status. ``_http`` converts both
            :class:`urllib.error.HTTPError` and
            :class:`urllib.error.URLError` into :class:`RuntimeError`, so a
            dead host and a 500 surface the same way.

    Example::

        import portal

        # Search IIPP for NAIP imagery (default portal)
        results = portal.searchPortal("naip 2023", limit=10)
        for r in results:
            print(r["title"], r["type"], r["url"])

        # ArcGIS Online
        results = portal.searchPortal("wildfire perimeter", portal="agol")

        # Custom Enterprise portal
        results = portal.searchPortal("hydrology",
                                      portal="https://gis.mystate.gov/portal")

        # Raw portal query DSL (bypasses data_only and filters)
        results = portal.searchPortal("", raw_q='type:"Feature Service" owner:USGS')
    """
    base_url = _resolve_portal(portal)
    search_url = f"{base_url}/sharing/rest/search"

    # Assemble the query string
    if raw_q is not None:
        q = raw_q
    else:
        q = query
        if data_only:
            exclusions = " ".join(f'-type:"{t}"' for t in _DATA_ONLY_EXCLUSIONS)
            q = f"{q} {exclusions}".strip()

    params: dict[str, Any] = {
        "q": q,
        "num": min(max(1, limit), 100),
        "f": "json",
        **filters,
    }
    if token:
        params["token"] = token

    try:
        data = fetch_json(search_url, params)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Could not reach portal at {search_url!r}: {exc}"
        ) from exc

    # A portal reports a rejected search with a 200 and an error object, not
    # an HTTP status — an expired or invalid token comes back as code 498
    # (verified against ArcGIS Online). Reading `results` straight off that
    # body yields [], which is indistinguishable from a search that simply
    # matched nothing, so a caller with a bad token is told "no data" rather
    # than "your token is invalid". Surface it instead.
    if "error" in data:
        raise ValueError(
            f"Portal search at {search_url!r} returned an error: "
            f"{format_esri_error(data['error'])}"
        )

    items = data.get("results", [])
    parsed = []
    for item in items:
        thumb = item.get("thumbnail")
        if thumb:
            thumb = f"{base_url}/sharing/rest/content/items/{item.get('id', '')}/info/{thumb}"
        parsed.append({
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "snippet": item.get("snippet", ""),
            "tags": item.get("tags", []),
            "url": item.get("url", ""),
            "owner": item.get("owner", ""),
            "created": item.get("created"),
            "modified": item.get("modified"),
            "thumbnail": thumb,
            "_raw": item,
        })
    return parsed


# ---------------------------------------------------------------------------
# Service metadata
# ---------------------------------------------------------------------------

def getServiceMetadata(url: str, token: str | None = None) -> dict[str, Any]:
    """Fetch and return the JSON metadata for any ArcGIS REST service.

    Appends ``?f=json`` to the URL and returns the parsed response.  Works
    for ImageServer, FeatureServer, MapServer, and any sub-layer URL
    (e.g. ``/FeatureServer/0``).

    Args:
        url (str): ArcGIS service endpoint, e.g.::

            "https://naip.services.arcgis.com/.../ImageServer"
            "https://services.arcgis.com/.../FeatureServer/0"
            "https://server.arcgisonline.com/.../MapServer"

        token (str, optional): ArcGIS token for secured services.

    Returns:
        dict: Parsed service metadata.  Common keys vary by service type:

        - ``name`` (str): Service name.
        - ``type`` (str): Layer geometry type (Feature Services).
        - ``fields`` (list): Schema fields (Feature Services).
        - ``extent`` (dict): Spatial extent.
        - ``spatialReference`` (dict): Spatial reference info.
        - ``minScale``, ``maxScale`` (int): Scale range.
        - ``capabilities`` (str): Comma-separated capabilities string.

    Raises:
        ValueError: If the service answers 200 with an Esri error object —
            which is how ArcGIS reports a service that does not exist,
            rather than with an HTTP 404 — or if the response is not valid
            JSON.
        RuntimeError: If the URL is unreachable or answers with an HTTP
            error status (see :func:`searchPortal`).

    Example::

        import portal

        meta = portal.getServiceMetadata("https://.../ImageServer")
        print(meta["name"])
        print(meta["extent"])

        # FeatureServer layer 0
        meta = portal.getServiceMetadata("https://.../FeatureServer/0")
        print([f["name"] for f in meta.get("fields", [])])
    """
    clean_url = url.rstrip("/")
    params = build_params({"f": "json"}, token)
    try:
        meta = fetch_json(clean_url, params)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Could not reach service at {clean_url!r}: {exc}"
        ) from exc

    # ArcGIS answers a missing service with a 200 and an Esri error object
    # rather than an HTTP 404 (verified against a live server). Returning
    # that dict as if it were metadata makes the failure the caller's to
    # notice, and every caller has to remember to check — so raise instead,
    # matching how `services.py` treats the same response shape.
    if "error" in meta:
        raise ValueError(
            f"Service at {clean_url!r} returned an error: "
            f"{format_esri_error(meta['error'])}"
        )
    return meta


# ---------------------------------------------------------------------------
# Service-type detection
# ---------------------------------------------------------------------------

def _detect_service_type(url: str, meta: dict | None = None) -> str:
    """Return the service type string for *url*.

    Detection order:
    1. URL path segments (fast, no HTTP call needed for clear cases).
    2. ``meta["type"]`` or ``meta["serviceDataType"]`` if caller already
       fetched metadata.
    3. Fetch ``?f=json`` and inspect the response.

    Returns one of: ``"ImageServer"``, ``"FeatureServer"``, ``"MapServer"``,
    or ``"Unknown"``.
    """
    # Normalise
    clean = url.rstrip("/").lower()

    # Canonical spellings: match URL segment (case-insensitive), return
    # the correctly-cased ArcGIS type name.
    _stype_map = {
        "imageserver": "ImageServer",
        "featureserver": "FeatureServer",
        "mapserver": "MapServer",
    }
    for lower, canonical in _stype_map.items():
        if f"/{lower}" in clean or clean.endswith(lower):
            return canonical

    # Fall back to metadata inspection
    if meta is None:
        try:
            meta = getServiceMetadata(url)
        except Exception:
            return "Unknown"

    # ArcGIS REST items carry a "type" key on the item record,
    # but service endpoint JSON uses serviceDataType or serviceType.
    for key in ("serviceDataType", "serviceType", "type"):
        val = meta.get(key, "")
        if isinstance(val, str):
            v = val.lower()
            if "image" in v:
                return "ImageServer"
            if "feature" in v:
                return "FeatureServer"
            if "map" in v:
                return "MapServer"

    # Check for fields[] → likely a FeatureServer layer
    if "fields" in meta:
        return "FeatureServer"
    # Check for bandCount → ImageServer
    if "bandCount" in meta or "pixelType" in meta:
        return "ImageServer"

    return "Unknown"


def _resolve_url(url_or_result: str | dict) -> str:
    """Extract a service URL from either a raw URL string or a
    :func:`searchPortal` result dict."""
    if isinstance(url_or_result, str):
        return url_or_result.rstrip("/")
    if isinstance(url_or_result, dict):
        # searchPortal result has a "url" key; fall back to id-based lookup
        service_url = url_or_result.get("url", "")
        if service_url:
            return service_url.rstrip("/")
        raise ValueError(
            "Portal result dict has no 'url' key.  Either the item is not a "
            "hosted service, or the portal did not return a URL for it.  "
            "Check url_or_result['_raw'] for the full item record."
        )
    raise TypeError(
        f"url_or_result must be a URL string or a searchPortal() result dict, "
        f"got {type(url_or_result).__name__!r}"
    )
