"""
Private generic OGC API - Features client for georest.restusgs.

Nothing in here knows about USGS: it speaks the OGC API - Features shape
(``/collections``, ``/collections/{id}/items``, ``queryables``) with
cursor-style ``rel="next"`` paging, so another OGC endpoint could reuse it.
``waterdata`` layers the USGS collections, constants and conveniences on top.

Verified against the live USGS API (2026-09-14):

- Responses carry ``numberReturned`` but **no** ``numberMatched``, so the only
  way to know a query is exhausted is to follow ``next`` links until one is
  missing or a page comes back empty.
- ``next`` hrefs preserve every original query parameter, so they are
  followed verbatim.
- Any queryable property name works as a direct equality filter
  (``parameter_code=00060``); CQL2 text goes in ``filter`` +
  ``filter-lang=cql2-text``.
- The API does not sort. Callers that need ordered output sort client-side.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from collections.abc import Iterable, Iterator

from ._http import fetch_json_with_headers

#: The clock `datetime_param` resolves durations against. Module-level so
#: tests can pin it.
_now = lambda: _dt.datetime.now(_dt.timezone.utc)  # noqa: E731

#: ISO 8601 durations with a fixed length: weeks, days, hours, minutes,
#: seconds. Years and months are rejected — they have no fixed length.
_DURATION_RE = re.compile(
    r"^P(?:(?P<weeks>\d+(?:\.\d+)?)W)?(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$",
    re.IGNORECASE,
)

#: Largest `limit` the API accepts per page. The OpenAPI schema says 10 000,
#: but 50 000 was accepted live and the efficiency guide says "up to 50,000
#: records per page"; 1 000 000 answers 400. The live suite guards this.
MAX_LIMIT = 50_000
#: Default page size. Per-request cost dominates for observation rows, so
#: pages are large; callers bound the total with `max_items`, not `limit`.
DEFAULT_PAGE_SIZE = 10_000

#: Query parameters the client sets itself. Passing one through `**filters`
#: raises TypeError rather than silently fighting the paging logic.
_RESERVED_ITEM_PARAMS = frozenset({"f", "limit", "cursor", "offset"})

#: Characters left un-encoded in the query string so `bbox=-1,2,3,4` and
#: `datetime=2020-01-01/2020-01-31` stay readable in logs and error
#: messages. The API accepts both forms (verified).
_URL_SAFE = ",/:"


def _join(base_url: str, *parts: str) -> str:
    return base_url.rstrip("/") + "/" + "/".join(parts)


def _get(url: str, headers: dict[str, str] | None, timeout: int | None) -> dict:
    data, _ = fetch_json_with_headers(url, {"f": "json"}, timeout=timeout, headers=headers)
    return data


def list_collections(
    base_url: str, *, headers: dict[str, str] | None = None, timeout: int | None = None
) -> list[dict]:
    """GET ``{base_url}/collections`` and return its ``collections`` list."""
    return _get(_join(base_url, "collections"), headers, timeout).get("collections", [])


def get_collection(
    base_url: str,
    collection_id: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> dict:
    """GET one collection's description (title, extent, links)."""
    return _get(_join(base_url, "collections", collection_id), headers, timeout)


def get_queryables(
    base_url: str,
    collection_id: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> dict:
    """Return a collection's queryables as ``{property_name: json_schema}``.

    Every key is a valid direct-equality filter for that collection's items.
    """
    return _get(_join(base_url, "collections", collection_id, "queryables"), headers, timeout).get(
        "properties", {}
    )


def get_item(
    base_url: str,
    collection_id: str,
    item_id: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> dict:
    """GET a single Feature by id."""
    return _get(
        _join(base_url, "collections", collection_id, "items", urllib.parse.quote(item_id, safe="")),
        headers,
        timeout,
    )


def bbox_param(bbox: str | dict | list | tuple) -> str:
    """Coerce a bbox to the OGC ``minx,miny,maxx,maxy[,minz,maxz]`` form.

    Accepts a ready string, a 4- or 6-item list/tuple, or a dict with
    ``xmin/ymin/xmax/ymax`` keys (the Esri spelling, so a bbox that came
    from ``restesri`` works unchanged).

    Raises:
        ValueError: For any other shape, or a non-numeric coordinate.
    """
    if isinstance(bbox, str):
        return bbox
    if isinstance(bbox, dict):
        try:
            values = [bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"]]
        except KeyError as exc:
            raise ValueError(f"bbox dict must have xmin/ymin/xmax/ymax keys, got {bbox!r}") from exc
    elif isinstance(bbox, (list, tuple)) and len(bbox) in (4, 6):
        values = list(bbox)
    else:
        raise ValueError(
            "bbox must be a 'minx,miny,maxx,maxy' string, a 4- or 6-item list/tuple, "
            f"or a dict with xmin/ymin/xmax/ymax keys; got {bbox!r}"
        )
    try:
        return ",".join(str(v) if isinstance(v, (int, float)) else str(float(v)) for v in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bbox coordinates must be numeric, got {bbox!r}") from exc


def _stamp(value: str | _dt.date | _dt.datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    # datetime before date: datetime is a date subclass.
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.isoformat() + "Z"  # naive means UTC, by convention here
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    raise TypeError(f"expected a str, date or datetime, got {type(value).__name__}")


def _duration_start(text: str) -> str:
    """``"P7D"`` → the UTC instant seven days before now, as RFC 3339 ``…Z``.

    The USGS server rejects ISO 8601 durations in every form (``P7D``,
    ``PT48H``, ``P7D/..`` all answer 400 "Unknown string format", verified
    2026-09-14) despite its docs listing them, so they are resolved here.

    Raises:
        ValueError: For a malformed duration, or one using years or months.
    """
    match = _DURATION_RE.match(text)
    parts = {k: float(v) for k, v in match.groupdict().items() if v} if match else {}
    if not parts or text.upper().endswith("T"):
        # A Y, or an M before any T, is a year/month component.
        date_part = text.upper().split("T", 1)[0]
        if "Y" in date_part or "M" in date_part:
            raise ValueError(
                f"duration {text!r} uses years or months, which have no fixed length; "
                "use weeks/days/hours (e.g. 'P30D') or explicit start/end dates"
            )
        raise ValueError(f"not a supported ISO 8601 duration: {text!r} (e.g. 'P7D', 'PT6H')")
    stamp = _now() - _dt.timedelta(**parts)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def datetime_param(
    start: str | _dt.date | _dt.datetime | None = None,
    end: str | _dt.date | _dt.datetime | None = None,
) -> str | None:
    """Build the OGC ``datetime`` parameter from a start and/or end.

    - ``(None, None)`` → ``None`` (no filter).
    - A ``start`` string that already is an interval (contains ``/``) passes
      through untouched: ``start="2020-01-01/2020-01-31"``, ``"../2020-12-31"``.
    - A ``start`` that is an ISO 8601 duration — ``"P7D"``, ``"PT6H"``,
      ``"P2W"`` — means "the last …" and is resolved client-side to
      ``<now minus duration>/..``, because the server rejects durations.
    - ``date`` → ``YYYY-MM-DD``; a naive ``datetime`` → RFC 3339 with ``Z``
      (naive is taken to mean UTC); an aware ``datetime`` → its own offset.
    - One-sided ranges use the OGC open-end form: ``start/..`` or ``../end``.

    Raises:
        ValueError: If ``end`` is given alongside a ``start`` that is already
            an interval or duration, or a duration is malformed or uses
            years/months.
        TypeError: For an unsupported ``start``/``end`` type.
    """
    if start is None and end is None:
        return None
    if isinstance(start, str):
        text = start.strip()
        if "/" in text:
            if end is not None:
                raise ValueError(f"start={start!r} is already an interval; do not also pass end")
            return text
        if text[:1] in ("P", "p"):
            if end is not None:
                raise ValueError(f"start={start!r} is a duration; do not also pass end")
            return f"{_duration_start(text)}/.."
    s, e = _stamp(start), _stamp(end)
    if s is None:
        return f"../{e}"
    if e is None:
        return f"{s}/.."
    return f"{s}/{e}"


def api_key_headers(api_key: str | None) -> dict[str, str]:
    """``{"X-Api-Key": key}`` or ``{}``. The key travels only as a header —
    never in the query string, where it would land in every URL that an
    error message or a log line repeats."""
    return {"X-Api-Key": api_key} if api_key else {}


def build_item_params(
    *,
    bbox: str | dict | list | tuple | None = None,
    datetime: str | None = None,
    properties: str | Iterable[str] | None = None,
    skip_geometry: bool = False,
    filter: str | None = None,
    filter_lang: str = "cql2-text",
    limit: int = DEFAULT_PAGE_SIZE,
    **filters: object,
) -> dict[str, str]:
    """Assemble the query parameters for an ``/items`` request.

    Args:
        bbox: See :func:`bbox_param`.
        datetime: A ready ``datetime`` value — see :func:`datetime_param`.
        properties: Property names to include (a comma-separated string or an
            iterable). Verified to trim the response to exactly those keys.
        skip_geometry: Send ``skipGeometry=true``; geometry comes back null.
        filter: A CQL2 text expression, e.g. ``"statistic_id='00003'"``.
        filter_lang: The CQL2 dialect for ``filter``.
        limit: Page size, ``1..MAX_LIMIT``.
        **filters: Any queryable property as a direct equality filter.
            ``None`` values are skipped.

    Raises:
        TypeError: If a reserved parameter (``f``, ``limit``, ``cursor``,
            ``offset``) is passed through ``**filters``.
        ValueError: If ``limit`` is out of range.
    """
    reserved = sorted(_RESERVED_ITEM_PARAMS.intersection(filters))
    if reserved:
        raise TypeError(
            f"{', '.join(reserved)} {'is' if len(reserved) == 1 else 'are'} set by the client; "
            "use limit=/max_items= instead of passing it as a filter"
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an int in 1..{MAX_LIMIT}, got {limit!r}")

    params: dict[str, str] = {}
    if bbox is not None:
        params["bbox"] = bbox_param(bbox)
    if datetime:
        params["datetime"] = datetime
    if properties:
        params["properties"] = (
            properties if isinstance(properties, str) else ",".join(str(p) for p in properties)
        )
    if skip_geometry:
        params["skipGeometry"] = "true"
    if filter:
        params["filter"] = filter
        params["filter-lang"] = filter_lang
    for name, value in filters.items():
        if value is None:
            continue
        params[name] = value if isinstance(value, str) else str(value)
    params["limit"] = str(limit)
    params["f"] = "json"
    return params


def _next_link(page: dict) -> str | None:
    for link in page.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "next" and link.get("href"):
            return link["href"]
    return None


def _pages(
    url: str, *, headers: dict[str, str] | None, timeout: int | None
) -> Iterator[tuple[list[dict], str | None, dict[str, str]]]:
    """Yield ``(features, next_url, response_headers)`` per page, following
    ``next`` links verbatim. Stops on a missing ``next``, an empty page, or a
    ``next`` that points back at a URL already fetched (loop guard)."""
    seen: set[str] = set()
    while url and url not in seen:
        seen.add(url)
        page, resp_headers = fetch_json_with_headers(url, timeout=timeout, headers=headers)
        if not isinstance(page, dict) or "features" not in page:
            raise ValueError(
                f"OGC API response from {url!r} has no 'features' key: {str(page)[:400]}"
            )
        batch = page["features"] or []
        next_url = _next_link(page) if batch else None
        yield batch, next_url, resp_headers
        url = next_url


def _items_url(base_url: str, collection_id: str, params: dict[str, str]) -> str:
    return (
        _join(base_url, "collections", collection_id, "items")
        + "?"
        + urllib.parse.urlencode(params, safe=_URL_SAFE)
    )


def iter_items(
    base_url: str,
    collection_id: str,
    params: dict[str, str],
    *,
    max_items: int | None,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> Iterator[dict]:
    """Yield Features one at a time across pages, stopping after ``max_items``
    (``None`` = unlimited). ``params`` comes from :func:`build_item_params`."""
    count = 0
    for batch, _next, _headers in _pages(
        _items_url(base_url, collection_id, params), headers=headers, timeout=timeout
    ):
        for feature in batch:
            if max_items is not None and count >= max_items:
                return
            yield feature
            count += 1


def rate_limit_from_headers(headers: dict[str, str] | None) -> dict[str, int] | None:
    """``{"limit": n, "remaining": m}`` from ``X-RateLimit-*`` headers, or
    ``None`` when the response carried neither."""
    lookup = {k.lower(): v for k, v in (headers or {}).items()}
    out: dict[str, int] = {}
    for key, header in (("limit", "x-ratelimit-limit"), ("remaining", "x-ratelimit-remaining")):
        value = lookup.get(header)
        if value is None:
            continue
        try:
            out[key] = int(value)
        except ValueError:
            continue
    return out or None


def get_items(
    base_url: str,
    collection_id: str,
    *,
    max_items: int | None,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
    **param_kwargs: object,
) -> dict:
    """Fetch up to ``max_items`` Features as one GeoJSON FeatureCollection.

    Keyword arguments other than ``max_items``/``headers``/``timeout`` go to
    :func:`build_item_params`.

    The result carries two foreign members beyond ``type``/``features``:

    - ``truncated: True`` — only present when the fetch stopped at
      ``max_items`` while the server still had more. The API reports no
      ``numberMatched``, so this is the caller's only signal that the
      collection was cut; a silent cut would be the worst outcome.
    - ``rate_limit: {"limit": n, "remaining": m}`` — from the last page's
      ``X-RateLimit-*`` headers when present. With one shared server key every
      caller draws on the same hourly budget, so it is reported in-band.

    Raises:
        ValueError: If ``max_items`` is not ``None`` or a positive int, or a
            page has no ``features`` key.
        TypeError, ValueError: From :func:`build_item_params`.
        RuntimeError, RateLimitError, ValueError: From the transport.
    """
    if max_items is not None and (isinstance(max_items, bool) or not isinstance(max_items, int)
                                  or max_items < 1):
        raise ValueError(f"max_items must be None or a positive int, got {max_items!r}")
    params = build_item_params(**param_kwargs)  # type: ignore[arg-type]

    features: list[dict] = []
    truncated = False
    resp_headers: dict[str, str] = {}
    for batch, next_url, resp_headers in _pages(
        _items_url(base_url, collection_id, params), headers=headers, timeout=timeout
    ):
        if max_items is not None:
            room = max_items - len(features)
            if len(batch) >= room:
                features.extend(batch[:room])
                truncated = len(batch) > room or next_url is not None
                break
        features.extend(batch)

    fc: dict = {"type": "FeatureCollection", "features": features}
    if truncated:
        fc["truncated"] = True
    rate_limit = rate_limit_from_headers(resp_headers)
    if rate_limit:
        fc["rate_limit"] = rate_limit
    return fc
