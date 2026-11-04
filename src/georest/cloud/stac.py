"""Client for STAC APIs: which catalogs and collections exist, and what is in them.

STAC (the SpatioTemporal Asset Catalog standard) is how cloud-hosted imagery
and gridded data are listed: a catalog has collections, a collection has items
(scenes), an item has assets (files). This module finds things and returns
links; reading the files is for rasterio, odc-stac, zarr and the rest of the
cloud-native ecosystem::

    from georest.cloud import stac

    stac.catalogs()                                  # the catalogs georest knows
    stac.collections("planetary-computer", "fire")   # collections matching a word

What this module absorbs, so the caller does not meet it:

* **Catalogs differ in how their files are read.** Some are open links, some
  must be signed, some need a NASA login, some are requester-pays buckets.
  Every row says which (the ``assets`` column).
* **A collection list can be long and paged.** NASA's has thousands of
  collections, 20 to a page by default. Paging is followed up to
  ``max_items``, and ``df.attrs["truncated"]`` says when that stopped it.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from . import catalogs as _registry
from ._frames import frame
from ._http import fetch_json

_CATALOG_COLUMNS = ("name", "title", "url", "assets", "notes")
_COLLECTION_COLUMNS = ("catalog", "id", "title", "summary", "license", "start", "end",
                       "assets", "featured", "note")

#: Collections asked for per page; servers may ignore it and send fewer.
_PAGE_SIZE = 100
_SUMMARY_CHARS = 240

_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


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
