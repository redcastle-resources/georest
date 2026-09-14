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

import re
import threading
import urllib.parse

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

# Keyed on (base_url, token), not base_url alone: portals/self answers with the
# org of whoever the token belongs to, so one base URL can map to many orgs.
_ORG_ID_CACHE: dict[tuple[str, str | None], str] = {}
_ORG_ID_LOCKS: dict[tuple[str, str | None], threading.Lock] = {}
_ORG_ID_LOCKS_GUARD = threading.Lock()

# ArcGIS org ids are short alphanumerics. Validated before being interpolated
# into the search DSL so a malformed portals/self response cannot inject syntax.
_ORG_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


def _is_agol_org_portal(base_url: str) -> bool:
    """True for an ArcGIS Online *organization* URL (``<org>.maps.arcgis.com``).

    ``www.arcgis.com`` is the global AGOL endpoint, not an org, so it is
    excluded - searching it should stay global. Enterprise portals and custom
    vanity domains are also excluded; those need ``org_scoped=True`` explicitly.
    """
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    return host.endswith(".maps.arcgis.com") and not host.startswith("www.")


def _org_lock(key: tuple[str, str | None]) -> threading.Lock:
    with _ORG_ID_LOCKS_GUARD:
        return _ORG_ID_LOCKS.setdefault(key, threading.Lock())


def _resolve_org_id(base_url: str, token: str | None = None) -> str:
    """Return the portal's organization id via ``/sharing/rest/portals/self``.

    Single-flight per (portal, token): concurrent first callers make one
    request, not N. The token is part of the key because portals/self reports
    the token owner's org - keying on the URL alone would hand one caller's
    org id to every other token on that portal. Only *successful* lookups are
    cached - caching a transient failure would permanently disable scoping,
    and an unscoped search silently returns other organizations' data.

    Raises:
        RuntimeError: if the org id cannot be resolved, for any reason
            (georest's one failure type for anything HTTP, see _http).
            Callers wanting a global search on failure must pass
            ``org_scoped=False`` explicitly; this never falls back silently.
    """
    key = (base_url, token or None)
    cached = _ORG_ID_CACHE.get(key)
    if cached:
        return cached

    url = f"{base_url}/sharing/rest/portals/self"
    hint = "Pass org_scoped=False to search all of ArcGIS Online instead."

    with _org_lock(key):
        cached = _ORG_ID_CACHE.get(key)   # another thread may have won
        if cached:
            return cached

        params = {"f": "json"}
        if token:
            params["token"] = token
        try:
            data = fetch_json(url, params)
        except (RuntimeError, OSError, ValueError) as exc:
            # _http folds every transport/HTTP failure into RuntimeError and
            # a non-JSON body into ValueError.
            raise RuntimeError(
                f"Could not resolve the organization id from {url!r}: {exc}. {hint}"
            ) from exc

        # Any shape other than a JSON object is unusable. Checked before any
        # .get() so a list/string body raises RuntimeError, not AttributeError.
        if not isinstance(data, dict):
            raise RuntimeError(
                f"{url!r} returned {type(data).__name__}, expected a JSON object. {hint}"
            )
        if data.get("error"):
            err = data["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            raise RuntimeError(
                f"Portal returned an error resolving the organization id from "
                f"{url!r}: {msg}. {hint}"
            )

        org_id = data.get("id")
        if not isinstance(org_id, str) or not _ORG_ID_RE.match(org_id):
            raise RuntimeError(
                f"{url!r} did not report a usable organization id "
                f"(got {org_id!r}). {hint}"
            )

        _ORG_ID_CACHE[key] = org_id
        return org_id


def _assert_balanced_parens(q: str) -> None:
    """Guard the ``orgid:X AND (<q>)`` wrapper against scope escape.

    Counts only *structural* parentheses - parens inside a quoted DSL phrase are
    literals and must be ignored. A naive counter is bypassable with balanced
    literals, e.g. ``title:"(" x) OR orgid:OTHER OR (x ")"`` counts as balanced
    while structurally closing the wrapper early.
    """
    depth = 0
    in_quote = False
    escaped = False
    for ch in q:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(
                    "Unbalanced parentheses in the portal search query; refusing "
                    "to build an org-scoped query that could escape its scope."
                )
    if escaped:
        # Balanced so far, but the dangling backslash would escape the
        # wrapper's own closing paren: ``orgid:X AND (foo\)``.
        raise ValueError(
            "Trailing backslash in the portal search query; refusing to build "
            "an org-scoped query that could escape its scope."
        )
    if in_quote:
        raise ValueError(
            "Unterminated quote in the portal search query; refusing to build "
            "an org-scoped query that could escape its scope."
        )
    if depth != 0:
        raise ValueError(
            "Unbalanced parentheses in the portal search query; refusing to "
            "build an org-scoped query that could escape its scope."
        )


def _neutralize_free_text(text: str) -> str:
    """Make a free-text ``query`` safe to wrap in ``orgid:X AND (<q>)``.

    ``query`` is a search phrase, not DSL (that is ``raw_q``), so a stray paren
    or quote a user typed - ``evacuation (2023``, ``12" pipeline`` - must not
    fail the search. Well-formed text passes through unchanged, keeping phrases
    and grouping. Text that could break the wrapper has its parentheses and
    quotes replaced with spaces instead, leaving plain search terms.

    Backslashes are always removed. A user typing ``C:\\data`` means no DSL
    escape, and a trailing one would escape the wrapper's own closing paren.
    """
    if "\\" in text:
        text = " ".join(text.replace("\\", " ").split())
    try:
        _assert_balanced_parens(text)
        return text
    except ValueError:
        return " ".join(re.sub(r'[()"]', " ", text).split())


def searchPortal(
    query: str,
    portal: str = "iipp",
    limit: int = 20,
    data_only: bool = True,
    raw_q: str | None = None,
    token: str | None = None,
    org_scoped: bool | None = None,
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
        org_scoped (bool | None, optional): Restrict results to the portal's
            own organization by injecting an ``orgid:`` clause.
            - ``None`` (default) - auto. Scopes only for a canonical
              ``<org>.maps.arcgis.com`` URL, and only when ``raw_q`` is not
              supplied. Global AGOL (``portal="agol"``), Enterprise portals,
              and custom vanity domains are NOT auto-scoped; pass ``True`` for
              those.
            - ``True`` - always scope, wrapping ``raw_q`` if present.
            - ``False`` - never scope (pre-fix behaviour), and the escape hatch
              if the organization id cannot be resolved.
            Scoping costs one extra ``/sharing/rest/portals/self`` request the
            first time a portal is seen; successful lookups are cached for the
            process. If the id cannot be resolved this raises ``RuntimeError``
            rather than silently returning other organizations' data.
            When scoping, a ``query`` with unbalanced parentheses or quotes
            has those characters dropped (it is searched as plain terms),
            while a ``raw_q`` that could escape the ``orgid:`` wrapper raises
            ``ValueError``.
        **filters: Extra ArcGIS search filters forwarded as query params
            (e.g. ``sortField="title"``, ``sortOrder="asc"``,
            ``bbox="-120,35,-110,42"``). The reserved params ``q``, ``num``,
            ``f`` and ``token`` are always set by this function and cannot be
            overridden through ``filters``.

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

    # ``/sharing/rest/search`` on an ArcGIS Online ORG url still searches all of
    # ArcGIS Online unless the query carries an ``orgid:`` clause - so
    # ``searchPortal("evacuation", portal="https://mycity.maps.arcgis.com")``
    # otherwise returns layers from Oregon, California, TxDOT, etc. (measured
    # 2026-09-11 against georest 0.1.0). Scope it.
    #   None  -> auto: scope only for <org>.maps.arcgis.com, and only when the
    #            caller is not driving the query DSL themselves via raw_q.
    #   True  -> always scope (AND-ed onto raw_q if present).
    #   False -> never scope (previous behaviour).
    if org_scoped is None:
        org_scoped = _is_agol_org_portal(base_url) and raw_q is None
    org_id = _resolve_org_id(base_url, token) if org_scoped else None

    # Assemble the query string. The two inputs get different treatment when
    # scoping, because only raw_q is DSL: an unbalanced raw_q is the caller's
    # mistake (refuse), an unbalanced query is just what a user typed (clean).
    # Both are checked BEFORE the data_only exclusions are appended, so the
    # 33 library-generated -type:"..." clauses can't flip the quote parity.
    if raw_q is not None:
        q = raw_q
        if org_id:
            _assert_balanced_parens(q)
    else:
        q = _neutralize_free_text(query) if org_id else query
        if data_only:
            exclusions = " ".join(f'-type:"{t}"' for t in _DATA_ONLY_EXCLUSIONS)
            q = f"{q} {exclusions}".strip()

    if org_id:
        if q.strip():
            q = f"orgid:{org_id} AND ({q})"
        else:
            q = f"orgid:{org_id}"

    # Caller filters merge FIRST so they cannot overwrite the reserved
    # parameters below. Previously ``**filters`` came last, which meant
    # ``filters["q"]`` silently replaced the assembled (and org-scoped) query.
    params: dict[str, Any] = dict(filters)
    params["q"] = q
    params["num"] = min(max(1, limit), 100)
    params["f"] = "json"
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
