"""Client for STAC APIs: which catalogs and collections exist, and what is in them.

STAC (the SpatioTemporal Asset Catalog standard) is how cloud-hosted imagery
and gridded data are listed: a catalog has collections, a collection has items
(scenes), an item has assets (files). This module finds things and returns
links; reading the files is for rasterio, odc-stac, zarr and the rest of the
cloud-native ecosystem::

    from georest.cloud import stac

    stac.catalogs()                                  # the catalogs georest knows
    stac.collections("planetary-computer", "fire")   # collections matching a word
    stac.search("earth-search", "sentinel-2-l2a",    # scenes over a place and dates
                bbox=(-114.2, 46.8, -113.8, 47.1), datetime="2024-06-01/2024-09-30",
                max_cloud_cover=20)

What this module absorbs, so the caller does not meet it:

* **Catalogs differ in how their files are read.** Some are open links, some
  must be signed, some need a NASA login, some are requester-pays buckets.
  Every row says which (the ``assets`` column).
* **A list can be long and paged.** NASA's has thousands of collections, 20
  to a page by default, and a search can match thousands of scenes. Paging
  is followed up to ``max_items``, and ``df.attrs["truncated"]`` says when
  that stopped it.
* **Searches differ in how they page and what they accept.** Next links come
  as GET or POST; cloud cover is filtered by the server and checked again
  here; dates may be given as plain days.
* **A fire perimeter is not a STAC geometry.** ``intersects`` takes a GeoJSON
  geometry, Feature or FeatureCollection, such as what
  ``georest.esri.edw.query_features`` returns, and checks it before sending.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import urllib.parse
from typing import Any

from . import catalogs as _registry
from ._frames import check_bbox, frame
from ._http import CloudError, fetch_json, post_json_body

_CATALOG_COLUMNS = ("name", "title", "url", "assets", "notes")
_COLLECTION_COLUMNS = ("catalog", "id", "title", "summary", "license", "start", "end",
                       "assets", "featured", "note")
_SCENE_COLUMNS = ("catalog", "collection", "id", "datetime", "cloud_cover", "platform",
                  "assets", "thumbnail", "bbox", "geometry")
_ASSET_COLUMNS = ("catalog", "collection", "item", "key", "title", "type", "roles",
                  "access", "href")

#: Collections asked for per page; servers may ignore it and send fewer.
_PAGE_SIZE = 100
_SUMMARY_CHARS = 240

_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

#: The most scenes one search may be asked for. A bound, so a call inside an
#: agent cannot be asked to return a whole archive.
_MAX_SEARCH_ITEMS = 5000
#: Scenes asked for per page of a search. NASA's CMR-STAC answers 502 to a
#: page of 100 or more (checked 2026-11-04) and serves 50 reliably; the other
#: catalogs take 50 without trouble.
_SEARCH_PAGE_SIZE = 50
#: Pages followed in one search (enough for the largest ``max_items`` at the
#: page size above); a server that never stops sending next links cannot hold
#: the call forever.
_MAX_PAGES = 110
#: Vertices accepted in ``intersects``. A full-resolution perimeter can run
#: to hundreds of thousands; servers reject or time out well before that.
_MAX_VERTICES = 10_000

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")

#: Where Planetary Computer issues its per-collection read tokens.
_SIGN_TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/"
_SIGNED_HOST_SUFFIX = ".blob.core.windows.net"


def catalogs() -> Any:
    """The STAC catalogs georest knows, and how each one's files are read.

    Returns:
        A ``pandas.DataFrame``, one row per catalog: ``name`` (pass it as the
        ``catalog`` argument elsewhere), ``title``, ``url``, ``assets`` (how
        files are read: ``open``, ``sign``, ``earthdata`` or
        ``requester-pays``; see ``georest.cloud.catalogs.ASSET_ACCESS``) and
        ``notes``. Any other STAC API can be used by passing its root URL.

    Example::

        from georest.cloud import stac

        stac.catalogs()[["name", "assets"]]
    """
    rows = []
    for name in _registry.names():
        entry = _registry.CATALOGS[name]
        rows.append({"name": name, "title": entry["title"], "url": entry["url"],
                     "assets": entry["assets"], "notes": entry["notes"]})
    return frame(rows, _CATALOG_COLUMNS)


def _summary(description: Any) -> str | None:
    """The first paragraph of a collection's description, as plain text.

    Descriptions are often pages of Markdown; a table of collections wants the
    opening sentence or two, with links reduced to their text.
    """
    if not description:
        return None
    first = str(description).strip().split("\n\n", 1)[0]
    text = " ".join(_MARKDOWN_LINK.sub(r"\1", first).split())
    if len(text) <= _SUMMARY_CHARS:
        return text or None
    cut = text[:_SUMMARY_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "..."


def _interval(collection: dict) -> tuple[str | None, str | None]:
    """The (start, end) of a collection's first temporal interval; None if open or absent."""
    try:
        start, end = collection["extent"]["temporal"]["interval"][0][:2]
    except (KeyError, IndexError, TypeError, ValueError):
        return None, None
    return start or None, end or None


def _next_url(page: dict, current: str) -> str | None:
    """The URL of the next page of a list, if the server sent a GET ``next`` link."""
    for link in page.get("links") or []:
        if link.get("rel") == "next" and str(link.get("method", "GET")).upper() == "GET" \
                and link.get("href"):
            return urllib.parse.urljoin(current, link["href"])
    return None


def _all_collections(root: str, max_items: int) -> tuple[list[dict], bool]:
    """Collections of the catalog at ``root`` (up to ``max_items``) and whether more existed."""
    url, params = root.rstrip("/") + "/collections", {"limit": min(_PAGE_SIZE, max_items)}
    found: list[dict] = []
    seen: set[str] = set()
    while url and url not in seen:
        seen.add(url)
        page = fetch_json(url, params)
        if not isinstance(page, dict) or "collections" not in page:
            raise ValueError(f"{url} did not return a list of collections; "
                             f"is {root!r} the root URL of a STAC API?")
        found.extend(c for c in page["collections"] if isinstance(c, dict))
        url, params = _next_url(page, url), None
        if len(found) >= max_items:
            return found[:max_items], bool(url) or len(found) > max_items
    return found, False


def collections(catalog: str, query: str = "", *, max_items: int = 500) -> Any:
    """The collections in a STAC catalog, optionally narrowed by words.

    Collections georest knows are useful for forest and land work are listed
    first (``featured``), with a note on what each holds. A search word must
    appear in a collection's id, title, description or keywords; several words
    must all appear.

    Args:
        catalog: A name from :func:`catalogs` ("planetary-computer"), or the
            root URL of any STAC API.
        query: Words to look for ("fire", "sentinel", "elevation"). Empty
            lists every collection.
        max_items: The most collections to fetch from the server (the
            filter is applied to those, so on a very large catalog a rare word
            can miss; check ``df.attrs["truncated"]``).

    Returns:
        A ``pandas.DataFrame``, one row per collection: ``catalog``, ``id``
        (pass it as ``collections=`` to a search), ``title``, ``summary`` (the
        description's first paragraph, plain text), ``license``, ``start``
        and ``end`` (None while ongoing or unknown), ``assets`` (how files are
        read; see :func:`catalogs`), ``featured`` and ``note``.
        ``df.attrs["truncated"]`` is True when ``max_items`` stopped the fetch.

    Raises:
        ValueError: If ``catalog`` is not a known name or an http(s) URL, or
            the URL is not a STAC API.
        CloudError: If the server cannot be reached or refuses the request.

    Example::

        from georest.cloud import stac

        stac.collections("planetary-computer", "fire")[["id", "title", "start", "end"]]
    """
    entry = _registry.resolve(catalog)
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    raw, truncated = _all_collections(entry["url"], int(max_items))

    terms = str(query or "").lower().split()
    featured = entry["collections"]
    rows = []
    for item in raw:
        cid = str(item.get("id", ""))
        title = item.get("title") or None
        head = f"{cid} {title or ''}".lower()
        haystack = " ".join([head, str(item.get("description") or "").lower(),
                             " ".join(map(str, item.get("keywords") or [])).lower()])
        if not all(term in haystack for term in terms):
            continue
        meta = featured.get(cid)
        start, end = _interval(item)
        rows.append((sum(term in head for term in terms), {
            "catalog": entry["name"], "id": cid, "title": title,
            "summary": _summary(item.get("description")), "license": item.get("license"),
            "start": start, "end": end,
            "assets": (meta or {}).get("assets", entry["assets"]),
            "featured": meta is not None, "note": (meta or {}).get("note"),
        }))
    # Registered collections first, then the best word matches; the sort is
    # stable, so the server's own order breaks ties.
    rows.sort(key=lambda r: (not r[1]["featured"], -r[0]))
    df = frame((r[1] for r in rows), _COLLECTION_COLUMNS)
    df.attrs["truncated"] = truncated
    df.attrs["catalog"] = entry["name"]
    return df


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _bound(value: Any, *, end: bool) -> str:
    """One end of a datetime range as an RFC 3339 timestamp, or ``..`` if open."""
    if value is None or str(value).strip() in ("", ".."):
        return ".."
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, _dt.date):
        value = value.isoformat()
    text = str(value).strip()
    try:
        if _DATE.match(text):
            _dt.date.fromisoformat(text)
            return text + ("T23:59:59Z" if end else "T00:00:00Z")
        if _TIMESTAMP.match(text):
            _dt.date.fromisoformat(text[:10])
            if int(text[11:13]) < 24 and int(text[14:16]) < 60 and int(text[17:19]) < 61:
                return text
    except ValueError:
        pass
    raise ValueError(f"datetime {value!r} is not a date (2024-06-01) or timestamp "
                     f"(2024-06-01T10:30:00Z)")


def _datetime_range(value: Any) -> str | None:
    """A ``datetime`` argument as the ``start/end`` string a STAC search takes."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError("datetime as a pair must be (start, end); use None or '..' "
                             "for an open end")
        start, end = value
    elif isinstance(value, str) and "/" in value:
        parts = value.split("/")
        if len(parts) != 2:
            raise ValueError(f"datetime {value!r} must be 'start/end'")
        start, end = parts
    else:
        start = end = value  # a single day or instant
    first, last = _bound(start, end=False), _bound(end, end=True)
    if first == ".." and last == "..":
        raise ValueError("datetime is open at both ends; give a start or an end")
    if ".." not in (first, last):
        try:
            a = _dt.datetime.fromisoformat(first.replace("Z", "+00:00").replace("z", "+00:00"))
            b = _dt.datetime.fromisoformat(last.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            a = b = None  # unusual fractional digits: let the server judge
        if a is not None and a > b:
            raise ValueError(f"datetime starts ({first}) after it ends ({last})")
    return f"{first}/{last}"


def _walk_positions(coords: Any):
    """Every [x, y, ...] position in a GeoJSON coordinate array."""
    if isinstance(coords, (list, tuple)) and coords:
        if not isinstance(coords[0], (list, tuple)):
            yield coords
        else:
            for part in coords:
                yield from _walk_positions(part)


def _geometry_of(value: Any) -> dict:
    """A single GeoJSON geometry from a geometry, Feature or FeatureCollection."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("intersects must be GeoJSON; the text given is not JSON") from None
    if not isinstance(value, dict):
        raise ValueError("intersects must be a GeoJSON geometry, Feature or "
                         "FeatureCollection (for example the result of "
                         "georest.esri.edw.query_features)")
    kind = value.get("type")
    if kind == "FeatureCollection":
        geometries = [f["geometry"] for f in value.get("features") or []
                      if isinstance(f, dict) and f.get("geometry")]
        if not geometries:
            raise ValueError("intersects has no geometry: the FeatureCollection has no "
                             "features with a shape (did the query match nothing?)")
        parts = [_geometry_of(g) for g in geometries]
        if len(parts) == 1:
            return parts[0]
        if all(g["type"] in ("Polygon", "MultiPolygon") for g in parts):
            polygons = []
            for g in parts:
                polygons.extend([g["coordinates"]] if g["type"] == "Polygon"
                                else g["coordinates"])
            return {"type": "MultiPolygon", "coordinates": polygons}
        return {"type": "GeometryCollection", "geometries": parts}
    if kind == "Feature":
        if not value.get("geometry"):
            raise ValueError("intersects is a Feature with no geometry")
        return _geometry_of(value["geometry"])
    if kind == "GeometryCollection":
        return {"type": kind, "geometries": [_geometry_of(g)
                                             for g in value.get("geometries") or []]}
    if kind in ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                "MultiPolygon") and "coordinates" in value:
        return {"type": kind, "coordinates": value["coordinates"]}
    if "rings" in value or "paths" in value or "x" in value:
        raise ValueError("intersects looks like Esri JSON; pass GeoJSON (the EDW query "
                         "functions return it by default)")
    raise ValueError(f"intersects has type {kind!r}; expected a GeoJSON geometry, "
                     f"Feature or FeatureCollection")


def _tidy_ring(ring: Any) -> list | None:
    """``ring`` without repeated vertices, or None if fewer than three places remain."""
    if not isinstance(ring, (list, tuple)):
        return ring
    kept: list = []
    for pos in ring:
        if not kept or list(pos[:2]) != list(kept[-1][:2]):
            kept.append(pos)
    return kept if len({tuple(p[:2]) for p in kept}) >= 3 else None


def _tidy_polygons(geometry: dict) -> dict:
    """Drop what simplification leaves that servers reject as invalid shapes.

    A small polygon simplified by ``max_allowable_offset`` can collapse to one
    repeated point (Earth Search answers 400 "invalid_shape_exception"). Repeated
    vertices are removed, and a ring left with fewer than three places is
    dropped, with its polygon if it was the outer ring.
    """
    kind = geometry.get("type")
    if kind == "GeometryCollection":
        parts = [_tidy_polygons(g) for g in geometry.get("geometries") or []]
        return {"type": kind, "geometries": [g for g in parts if g is not None]}
    if kind not in ("Polygon", "MultiPolygon"):
        return geometry
    polygons = [geometry["coordinates"]] if kind == "Polygon" else list(geometry["coordinates"])
    kept = []
    for rings in polygons:
        if not isinstance(rings, (list, tuple)) or not rings:
            continue
        outer = _tidy_ring(rings[0])
        if outer is None:
            continue
        kept.append([outer] + [r for r in (_tidy_ring(h) for h in rings[1:]) if r is not None])
    if not kept and polygons and any(polygons):
        raise ValueError("intersects collapsed to nothing: every polygon is a point or a line "
                         "at this precision. Request it with a smaller max_allowable_offset "
                         "on the EDW queries, or search with its bbox")
    if kind == "Polygon":
        return {"type": kind, "coordinates": kept[0]} if kept else geometry
    return {"type": kind, "coordinates": kept} if kept else geometry


def _check_geometry(geometry: dict) -> dict:
    """Refuse a geometry a STAC server would reject or choke on, saying why."""
    geometry = _tidy_polygons(geometry)
    count = 0
    stack = [geometry]
    while stack:
        g = stack.pop()
        if g.get("type") == "GeometryCollection":
            stack.extend(g.get("geometries") or [])
            continue
        for pos in _walk_positions(g.get("coordinates")):
            count += 1
            try:
                x, y = float(pos[0]), float(pos[1])
            except (TypeError, ValueError, IndexError):
                raise ValueError("intersects has a coordinate that is not a number") from None
            if not (-180 <= x <= 180 and -90 <= y <= 90):
                raise ValueError(f"intersects has a coordinate ({x}, {y}) outside longitude/"
                                 f"latitude range; it looks projected. Request it in "
                                 f"EPSG:4326 (out_sr=4326 on the EDW queries)")
    if count == 0:
        raise ValueError("intersects has no coordinates")
    if count > _MAX_VERTICES:
        raise ValueError(f"intersects has {count:,} vertices; servers accept about "
                         f"{_MAX_VERTICES:,}. Simplify it (max_allowable_offset=0.001 on "
                         f"the EDW queries) or search with its bbox")
    return geometry


def _next_request(page: dict, current: str, body: dict) -> tuple[str, str, dict | None] | None:
    """(method, url, body) of the next page of a search, if the server sent one.

    Servers differ: Earth Search and Planetary Computer send a POST link with a
    body to merge into the original, NASA sends a plain GET link.
    """
    for link in page.get("links") or []:
        if link.get("rel") != "next" or not link.get("href"):
            continue
        url = urllib.parse.urljoin(current, link["href"])
        if str(link.get("method", "GET")).upper() == "POST":
            nxt = link.get("body")
            if not isinstance(nxt, dict):
                nxt = body
            elif link.get("merge"):
                nxt = {**body, **nxt}
            return "POST", url, nxt
        return "GET", url, None
    return None


def _first_href(item: dict, *keys: str) -> str | None:
    """The https link of the first of these assets that has one."""
    assets = item.get("assets") or {}
    for key in keys:
        href = (assets.get(key) or {}).get("href")
        if isinstance(href, str) and href.startswith(("http://", "https://")):
            return href
    return None


def _scene_row(entry: dict, item: dict) -> dict:
    """One flat row for a STAC item."""
    props = item.get("properties") or {}
    collection = item.get("collection")
    meta = entry["collections"].get(collection) or {}
    cloud = props.get("eo:cloud_cover")
    bbox = item.get("bbox")
    return {
        "catalog": entry["name"],
        "collection": collection,
        "id": item.get("id"),
        "datetime": props.get("datetime") or props.get("start_datetime"),
        "cloud_cover": float(cloud) if isinstance(cloud, (int, float)) else None,
        "platform": props.get("platform") or props.get("constellation"),
        "assets": meta.get("assets", entry["assets"]),
        "thumbnail": _first_href(item, "thumbnail", "rendered_preview", "browse"),
        "bbox": list(bbox[:4]) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else None,
        "geometry": item.get("geometry"),
    }


def search(catalog: str, collections: Any, *, bbox: Any = None, intersects: Any = None,
           datetime: Any = None, max_cloud_cover: float | None = None,
           query: dict | None = None, max_items: int = 100, newest_first: bool = True) -> Any:
    """Scenes in a STAC catalog over a place and a time, one row per scene.

    The result is small on purpose: ids, dates, cloud cover and where each
    scene is, not the scene's files. Use :func:`assets` for those links.

    Args:
        catalog: A name from :func:`catalogs` ("earth-search"), or the root URL
            of any STAC API.
        collections: A collection id from :func:`collections` ("sentinel-2-l2a"),
            or a list of ids. A search always names at least one; an
            unscoped search of a whole catalog is rarely what is wanted.
        bbox: ``(west, south, east, north)`` in decimal degrees. Give this or
            ``intersects``, not both.
        intersects: A GeoJSON geometry, Feature or FeatureCollection, such as
            a fire perimeter from ``georest.esri.edw.query_features``. Several
            polygons are searched together. Up to 10,000 vertices.
        datetime: When the scene was captured: ``"2024-06-01/2024-09-30"``, a
            ``(start, end)`` pair, a single day ``"2024-07-15"``, or a
            ``date``/``datetime``. Use ``".."`` or None for an open end.
        max_cloud_cover: Most cloud cover to accept, percent (0 to 100). Scenes
            from collections with no cloud cover value are left out by the
            server.
        query: Extra property filters in the STAC query form, merged with
            ``max_cloud_cover``, for example ``{"platform": {"eq": "sentinel-2a"}}``.
        max_items: The most scenes to return (1 to 5000).
        newest_first: Order by capture date, newest first. When ``max_items``
            stops the fetch, the scenes dropped are the oldest.

    Returns:
        A ``pandas.DataFrame``, one row per scene: ``catalog``, ``collection``,
        ``id``, ``datetime`` (capture time, UTC), ``cloud_cover`` (percent, NaN
        when the scene has none), ``platform``, ``assets`` (how files are read;
        see :func:`catalogs`), ``thumbnail`` (a preview link, or None),
        ``bbox`` and ``geometry`` (GeoJSON, for :func:`footprints`).
        ``df.attrs["truncated"]`` is True when ``max_items`` stopped the
        fetch, and ``df.attrs["matched"]`` is the number of scenes the server
        says matched, or None. A collection id the server does not know gives
        an empty table, not an error, so an empty result is worth checking
        against :func:`collections`.

    Raises:
        ValueError: If an argument is unusable (bad bbox, a geometry that looks
            projected or is too detailed, an unreadable date, ``max_items``
            out of range). The message says what to change.
        CloudError: If the server cannot be reached or rejects the search; the
            message carries the server's explanation.

    Example::

        from georest.cloud import stac

        scenes = stac.search("earth-search", "sentinel-2-l2a",
                             bbox=(-114.2, 46.8, -113.8, 47.1),
                             datetime="2024-06-01/2024-09-30",
                             max_cloud_cover=20, max_items=10)
        scenes[["id", "datetime", "cloud_cover"]]
    """
    entry = _registry.resolve(catalog)
    ids = [collections] if isinstance(collections, str) else list(collections or [])
    ids = [str(c).strip() for c in ids if str(c).strip()]
    if not ids:
        raise ValueError("name at least one collection to search; stac.collections() "
                         "lists them")
    if isinstance(max_items, bool) or not isinstance(max_items, int) \
            or not 1 <= max_items <= _MAX_SEARCH_ITEMS:
        raise ValueError(f"max_items must be a whole number from 1 to {_MAX_SEARCH_ITEMS}")
    if bbox is not None and intersects is not None:
        raise ValueError("give bbox or intersects, not both")
    if max_cloud_cover is not None and not 0 <= float(max_cloud_cover) <= 100:
        raise ValueError("max_cloud_cover is a percentage from 0 to 100")

    body: dict[str, Any] = {"collections": ids}
    if bbox is not None:
        body["bbox"] = list(check_bbox(bbox))
    if intersects is not None:
        body["intersects"] = _check_geometry(_geometry_of(intersects))
    when = _datetime_range(datetime)
    if when:
        body["datetime"] = when
    filters = dict(query or {})
    if max_cloud_cover is not None:
        filters["eo:cloud_cover"] = {"lte": float(max_cloud_cover)}
    if filters:
        body["query"] = filters
    if newest_first:
        body["sortby"] = [{"field": "properties.datetime", "direction": "desc"}]
    # One more than asked for, so a full result and a cut one can be told apart.
    body["limit"] = min(max_items + 1, _SEARCH_PAGE_SIZE)

    url, method, payload = entry["url"] + "/search", "POST", body
    rows: list[dict] = []
    matched: int | None = None
    dropped = 0
    seen: set[tuple] = set()
    pages = 0
    while len(rows) <= max_items and pages < _MAX_PAGES:
        key = (method, url, json.dumps(payload, sort_keys=True))
        if key in seen:
            break
        seen.add(key)
        page = post_json_body(url, payload) if method == "POST" else fetch_json(url)
        if not isinstance(page, dict) or not isinstance(page.get("features"), list):
            raise ValueError(f"{url} did not return search results; is {entry['url']!r} "
                             f"the root URL of a STAC API?")
        pages += 1
        if pages == 1:
            matched = page.get("numberMatched")
            if matched is None:
                matched = (page.get("context") or {}).get("matched")
        for item in page["features"]:
            if not isinstance(item, dict):
                continue
            row = _scene_row(entry, item)
            # Some servers ignore the query extension; do not hand back what was excluded.
            if max_cloud_cover is not None and row["cloud_cover"] is not None \
                    and row["cloud_cover"] > float(max_cloud_cover):
                dropped += 1
                continue
            rows.append(row)
        nxt = _next_request(page, url, body)
        if nxt is None:
            break
        method, url, payload = nxt

    truncated = len(rows) > max_items
    df = frame(rows[:max_items], _SCENE_COLUMNS)
    df.attrs["truncated"] = truncated
    df.attrs["matched"] = None if dropped or not isinstance(matched, int) else matched
    df.attrs["catalog"] = entry["name"]
    return df


def footprints(scenes: Any) -> dict:
    """The outlines of the scenes a search found, as a GeoJSON FeatureCollection.

    Hands the footprints to a map or to the Esri tools: ``georest.esri`` works
    in GeoJSON the same way.

    Args:
        scenes: The ``pandas.DataFrame`` returned by :func:`search`.

    Returns:
        A GeoJSON FeatureCollection dict, one Feature per scene that has a
        shape, with ``id``, ``collection``, ``datetime``, ``cloud_cover`` and
        ``platform`` as properties.

    Raises:
        ValueError: If ``scenes`` is not a search result (it has no ``geometry``
            column).

    Example::

        from georest.cloud import stac

        scenes = stac.search("earth-search", "sentinel-2-l2a",
                             bbox=(-114.2, 46.8, -113.8, 47.1), max_items=5)
        stac.footprints(scenes)["features"][0]["properties"]
    """
    columns = getattr(scenes, "columns", None)
    if columns is None or "geometry" not in columns or "id" not in columns:
        raise ValueError("footprints() takes the table that stac.search() returns "
                         "(it needs the id and geometry columns)")

    def clean(value):
        return None if value is None or (isinstance(value, float) and value != value) else value

    features = []
    for _, row in scenes.iterrows():
        geometry = row["geometry"]
        if not isinstance(geometry, dict):
            continue
        features.append({
            "type": "Feature", "id": row["id"], "geometry": geometry,
            "properties": {name: clean(row[name]) for name in
                           ("id", "collection", "datetime", "cloud_cover", "platform")
                           if name in columns},
        })
    return {"type": "FeatureCollection", "features": features}


# --------------------------------------------------------------------------
# Assets and signing
# --------------------------------------------------------------------------

def _needs_token(href: str) -> bool:
    host = urllib.parse.urlsplit(href).hostname or ""
    return host.endswith(_SIGNED_HOST_SUFFIX) and "sig=" not in href


def _sign_hrefs(hrefs: list[str], entry: dict, collection: str) -> list[str]:
    """``hrefs`` with Planetary Computer's read token for ``collection`` added."""
    if entry["assets"] != "sign":
        raise ValueError(f"catalog {entry['name']!r} does not need signed links "
                         f"(its files are {entry['assets']!r}); only Planetary Computer does")
    if not any(_needs_token(h) for h in hrefs):
        return list(hrefs)
    reply = fetch_json(_SIGN_TOKEN_URL + urllib.parse.quote(collection, safe=""))
    token = reply.get("token") if isinstance(reply, dict) else None
    if not token:
        raise CloudError(f"Planetary Computer did not issue a token for {collection!r}; "
                         f"is it a collection there?")
    return [h + ("&" if "?" in h else "?") + token if _needs_token(h) else h for h in hrefs]


def sign(href: Any, collection: str, *, catalog: str = "planetary-computer") -> Any:
    """Make Planetary Computer file links readable, with a short-lived token.

    A Planetary Computer file refuses to download until its link carries a
    token. The token lasts under an hour and grants read access to that
    collection's files, so treat a signed link like a password: use it, do not
    share or store it. Links that are not Planetary Computer storage, or are
    already signed, come back unchanged.

    Args:
        href: One link, or a list of links.
        collection: The collection the files belong to ("sentinel-2-l2a").
        catalog: The catalog; only "planetary-computer" signs.

    Returns:
        The same kind of value as ``href``: a string, or a list of strings.

    Raises:
        ValueError: If ``catalog`` does not use signed links.
        CloudError: If the token cannot be had.

    Example::

        from georest.cloud import stac

        scenes = stac.search("planetary-computer", "sentinel-2-l2a",
                             bbox=(-114.2, 46.8, -113.8, 47.1), max_items=1)
        row = scenes.iloc[0]
        files = stac.assets(row["catalog"], row["collection"], row["id"])
        stac.sign(files.loc[files["key"] == "B04", "href"].iloc[0], "sentinel-2-l2a")
    """
    entry = _registry.resolve(catalog)
    if isinstance(href, str):
        return _sign_hrefs([href], entry, collection)[0]
    return _sign_hrefs([str(h) for h in href], entry, collection)


def assets(catalog: str, collection: str, item: str, *, sign: bool = False) -> Any:
    """The files of one scene, one row per file.

    Args:
        catalog: A name from :func:`catalogs`, or a STAC API root URL.
        collection: The scene's collection id (the ``collection`` column of a
            search).
        item: The scene's id (the ``id`` column of a search).
        sign: Add Planetary Computer's read token to each link so it
            downloads. Has no effect on catalogs whose links need none. See
            :func:`sign` for what a signed link is.

    Returns:
        A ``pandas.DataFrame``, one row per file: ``catalog``, ``collection``,
        ``item``, ``key`` (the file's name in the scene: "B04", "visual"),
        ``title``, ``type`` (media type), ``roles`` (comma-separated: "data",
        "thumbnail"), ``access`` (how the link is read; see :func:`catalogs`)
        and ``href``. ``s3://`` links are returned as given: they are not
        https and need an S3 reader.

    Raises:
        CloudError: If the scene is not found or the server cannot be reached.
        ValueError: If the server's reply is not a STAC item.

    Example::

        from georest.cloud import stac

        scenes = stac.search("earth-search", "sentinel-2-l2a",
                             bbox=(-114.2, 46.8, -113.8, 47.1), max_items=1)
        row = scenes.iloc[0]
        stac.assets(row["catalog"], row["collection"], row["id"])[["key", "href"]]
    """
    entry = _registry.resolve(catalog)
    cid, iid = str(collection).strip(), str(item).strip()
    if not cid or not iid:
        raise ValueError("assets() needs the collection id and the item id of a scene")
    url = (f"{entry['url']}/collections/{urllib.parse.quote(cid, safe='')}"
           f"/items/{urllib.parse.quote(iid, safe='')}")
    found = fetch_json(url)
    if not isinstance(found, dict) or not isinstance(found.get("assets"), dict):
        raise ValueError(f"{url} did not return a STAC item")
    access = (entry["collections"].get(cid) or {}).get("assets", entry["assets"])
    rows = []
    for key, asset in found["assets"].items():
        asset = asset if isinstance(asset, dict) else {}
        roles = asset.get("roles")
        rows.append({"catalog": entry["name"], "collection": cid, "item": iid, "key": key,
                     "title": asset.get("title"), "type": asset.get("type"),
                     "roles": ", ".join(map(str, roles)) if isinstance(roles, list) else None,
                     "access": access, "href": asset.get("href")})
    if sign and access == "sign":
        hrefs = [r["href"] for r in rows if isinstance(r["href"], str)]
        signed = iter(_sign_hrefs(hrefs, entry, cid))
        for r in rows:
            if isinstance(r["href"], str):
                r["href"] = next(signed)
    return frame(rows, _ASSET_COLUMNS)
