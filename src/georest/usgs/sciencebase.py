"""Client for ScienceBase, the USGS catalog of data releases and science.

ScienceBase holds USGS data releases (the official, citable publication of
USGS data), along with project pages, collections and the files attached to
them:

    https://www.sciencebase.gov/catalog

::

    from georest.usgs import sciencebase

    releases = sciencebase.search("burn severity", data_releases_only=True)
    releases[["title", "published", "files", "url"]]

    files = sciencebase.item_files(releases["id"].iloc[0])
    files[["name", "size_mb", "url"]]

Searches return every match unless you pass ``limit``; ScienceBase is
large, so a broad query can match thousands of items. :func:`count` says how
many first.

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

BASE_URL = "https://www.sciencebase.gov/catalog"

#: The most items ScienceBase returns per request; larger requests get this many.
PAGE_SIZE = 1000

_FIELDS = "title,summary,dates,systemTypes,hasChildren,files,link"
_COLUMNS = ("id", "title", "summary", "item_type", "published", "last_updated",
            "has_children", "files", "url")
_FILE_COLUMNS = ("name", "size_mb", "content_type", "url")


def _date(item: dict, *kinds: str) -> str | None:
    for kind in kinds:
        for d in item.get("dates") or []:
            if d.get("type") == kind:
                return d.get("dateString")
    return None


def _row(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "summary": item.get("summary"),
        "item_type": ", ".join(item.get("systemTypes") or []),
        "published": _date(item, "Publication"),
        "last_updated": _date(item, "lastUpdated", "Last Updated"),
        "has_children": bool(item.get("hasChildren")),
        "files": len(item.get("files") or []),
        "url": (item.get("link") or {}).get("url"),
    }


def _filters(data_releases_only: bool, parent_id: str | None) -> list[str]:
    filters = []
    if data_releases_only:
        filters.append("systemType=Data Release")
    if parent_id:
        filters.append(f"parentId={parent_id}")
    return filters


def _params(query: str, data_releases_only: bool, parent_id: str | None) -> dict:
    params: dict[str, Any] = {"format": "json", "q": query or ""}
    filters = _filters(data_releases_only, parent_id)
    if filters:
        params["filter"] = filters if len(filters) > 1 else filters[0]
    return params


def count(query: str = "", *, data_releases_only: bool = False,
          parent_id: str | None = None) -> int:
    """How many items a :func:`search` with the same arguments returns.

    Example::

        from georest.usgs import sciencebase

        sciencebase.count("streamflow", data_releases_only=True)
    """
    reply = fetch_json(f"{BASE_URL}/items",
                       {**_params(query, data_releases_only, parent_id), "max": 0})
    return int(reply.get("total") or 0)


def search(query: str = "", *, data_releases_only: bool = False,
           parent_id: str | None = None, limit: int | None = None) -> Any:
    """ScienceBase items matching a search.

    Args:
        query: Free text, matched against titles, summaries, keywords and
            more.
        data_releases_only: Keep only USGS data releases.
        parent_id: Keep only items inside this one (see :func:`children`).
        limit: Return at most this many, best match first. By default every
            match is returned; ScienceBase serves 1,000 per request, and every
            page is fetched.

    Returns:
        ``pandas.DataFrame``, one row per item: ``id``, ``title``, ``summary``,
        ``item_type`` (e.g. "Data Release", "Folder"), ``published``,
        ``last_updated``, ``has_children``, ``files`` (how many are attached)
        and ``url``.

    Example::

        from georest.usgs import sciencebase

        releases = sciencebase.search("burn severity", data_releases_only=True)
        releases[["title", "published", "files", "url"]]
    """
    if limit is not None and int(limit) < 1:
        raise ValueError("limit must be at least 1, or None for every match")
    params = {**_params(query, data_releases_only, parent_id), "fields": _FIELDS}
    rows: list[dict] = []
    offset = 0
    while True:
        want = PAGE_SIZE if limit is None else min(PAGE_SIZE, int(limit) - len(rows))
        page = fetch_json(f"{BASE_URL}/items", {**params, "max": want, "offset": offset})
        items = page.get("items") or []
        rows.extend(_row(item) for item in items)
        offset += len(items)
        total = int(page.get("total") or 0)
        if not items or offset >= total or (limit is not None and len(rows) >= int(limit)):
            break
    return frame(rows, _COLUMNS)


def children(item_id: str) -> Any:
    """The items inside a ScienceBase item, such as the parts of a data release.

    Example::

        from georest.usgs import sciencebase

        project = sciencebase.search("Monitoring Trends in Burn Severity", limit=1)
        sciencebase.children(project["id"].iloc[0])
    """
    return search(parent_id=item_id)


def get_item(item_id: str) -> dict:
    """Everything ScienceBase holds about one item.

    Returns:
        A dict: the :func:`search` fields, plus ``body`` (the full
        description, HTML), ``dates`` (every dated event, by type),
        ``tags``, ``contacts`` (names and roles), ``web_links``,
        ``bbox`` (west, south, east, north, where the item has an extent)
        and ``parent_id``.

    Example::

        from georest.usgs import sciencebase

        item = sciencebase.get_item("6331e928d34e900e86c52b9e")
        item["title"], item["contacts"]
    """
    item = fetch_json(f"{BASE_URL}/item/{item_id}", {"format": "json"})
    if not isinstance(item, dict) or not item.get("id"):
        raise ValueError(f"no ScienceBase item {item_id!r}")
    box = (item.get("spatial") or {}).get("boundingBox") or {}
    return {
        **_row(item),
        "body": item.get("body"),
        "dates": {d.get("type"): d.get("dateString") for d in item.get("dates") or []},
        "tags": [t.get("name") for t in item.get("tags") or [] if t.get("name")],
        "contacts": [{"name": c.get("name"), "role": c.get("type")} for c in item.get("contacts") or []],
        "web_links": [{"title": w.get("title"), "url": w.get("uri")} for w in item.get("webLinks") or []],
        "bbox": (box.get("minX"), box.get("minY"), box.get("maxX"), box.get("maxY")) if box else None,
        "parent_id": item.get("parentId"),
    }


def item_files(item_id: str) -> Any:
    """The files attached to a ScienceBase item, with download links.

    Returns:
        ``pandas.DataFrame`` with ``name``, ``size_mb``, ``content_type`` and
        ``url``. Many data releases keep their files in child items; see
        :func:`children`.

    Example::

        from georest.usgs import sciencebase

        sciencebase.item_files("6331e928d34e900e86c52b9e")
    """
    item = fetch_json(f"{BASE_URL}/item/{item_id}", {"format": "json", "fields": "files"})
    rows = []
    for f in item.get("files") or []:
        size = f.get("size")
        rows.append({"name": f.get("name"),
                     "size_mb": round(size / 1e6, 3) if isinstance(size, (int, float)) else None,
                     "content_type": f.get("contentType"), "url": f.get("url")})
    return frame(rows, _FILE_COLUMNS)
