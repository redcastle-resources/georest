"""Cached, searchable vocabularies for the FIADB-API.

FIADB-API's ``/fullreport`` takes an estimate attribute, a row grouping,
a column grouping, and an evaluation. Live counts, measured against the
API:

=====================  =====  =========================================
Parameter              Count  What it selects
=====================  =====  =========================================
``snum`` / ``sdenom``    752  Estimate attribute (numerator/denominator)
``rselected`` etc.        96  Grouping variable (row / column / page)
``wc``                  1143  Evaluation (state + year + eval group)
=====================  =====  =========================================

That is a combinatorial space no one memorizes, and the published docs
still say "This page is under construction". Nothing in the official
documentation mentions that the parameter endpoints accept
``outputFormat=JSON``, but they do, and that is what makes discovery
possible at all: the entire vocabulary is machine-readable, about 1.4 MB
in total, and changes per *release* rather than per query.

So it is cached, and search runs locally: a bundled snapshot ships with
the package, a refreshed copy lives in a user cache, and the cache is
revalidated after about 30 days.

**Failure is always backward, never forward.** A vocabulary lookup falls
from user cache, to bundled snapshot, to an empty result. It does not
raise, and it does not block on the network. Discovery is what people
do *before* they know what they want; making it fragile makes the whole
package feel fragile.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from ._http import UpstreamError, UpstreamUnavailable, get_json

logger = logging.getLogger(__name__)

FIA_BASE = "https://apps.fs.usda.gov/fiadb-api"
LCMS_BASE = "https://lcms-dashboard.fs2c.usda.gov/lcms/api"

#: FIA parameter catalogs, by the query-parameter name they populate.
#: ``sdenom`` shares ``snum``'s vocabulary and ``cselected``/``pselected``
#: share ``rselected``'s, so only the distinct ones are fetched.
FIA_CATALOGS = ("snum", "rselected", "wc")

#: Aliases onto the three fetched catalogs.
FIA_CATALOG_ALIASES = {
    "sdenom": "snum",
    "cselected": "rselected",
    "pselected": "rselected",
}

#: Refresh interval. These change when FIA publishes a new evaluation
#: cycle, an annual-ish cadence, so 30 days picks changes up well before
#: anyone notices and keeps the network essentially off the hot path.
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60

_BUNDLED_DIR = Path(__file__).parent / "data"
_MEM_CACHE: dict[str, list[dict]] = {}
#: Lookups by number, built once per loaded catalog: {catalog: {key: row}}.
_INDEX: dict[str, dict[Any, dict]] = {}
_INDEX_KEY = {"snum": "ATTRIBUTE_NBR", "wc": "EVAL_GRP"}


def cache_dir() -> Path:
    """Directory for refreshed catalogs.

    ``~/.georest/restusfs``, beside the ArcGIS sign-in that georest keeps
    in ``~/.georest``. Overridable with ``GEOREST_RESTUSFS_CACHE`` for
    tests and for locked-down machines where the home directory is not
    writable.

    Example::

        from georest.restusfs import vocab

        vocab.cache_dir()   # Path to ~/.georest/restusfs in your home directory
    """
    override = os.environ.get("GEOREST_RESTUSFS_CACHE", "").strip()
    return Path(override) if override else Path.home() / ".georest" / "restusfs"


def _cache_path(name: str) -> Path:
    return cache_dir() / f"{name}.json"


def _bundled_path(name: str) -> Path:
    return _BUNDLED_DIR / f"{name}.json"


def _read_json_file(path: Path) -> list[dict] | None:
    try:
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else None
    except Exception:
        logger.debug("restusfs: unreadable cache file %s", path, exc_info=True)
        return None


def _write_json_file(path: Path, rows: list[dict]) -> None:
    """Write atomically so an interrupted refresh cannot leave a truncated
    catalog that then reads as a valid-but-empty vocabulary."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        tmp.replace(path)
    except Exception:
        logger.debug("restusfs: could not write cache %s", path, exc_info=True)


def _is_stale(path: Path) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) > CACHE_TTL_SECONDS
    except Exception:
        return True


def _fetch_fia_catalog(name: str) -> list[dict]:
    """Pull one FIA parameter catalog as JSON.

    ``outputFormat=JSON`` is the undocumented part. Without it these
    endpoints serve an HTML table intended for a browser.
    """
    payload = get_json(f"{FIA_BASE}/fullreport/parameters/{name}",
                       params={"outputFormat": "JSON"})
    if not isinstance(payload, list):
        raise UpstreamError(
            f"unexpected shape for FIA catalog {name!r}: "
            f"{type(payload).__name__}"
        )
    return payload


def _remember(name: str, rows: list[dict]) -> list[dict]:
    _MEM_CACHE[name] = rows
    _INDEX.pop(name, None)
    return rows


def load_catalog(name: str, *, refresh: bool = False) -> list[dict]:
    """Return one vocabulary, from memory, cache, bundle, or the network.

    Args:
        name: A catalog or alias: any of ``snum``, ``sdenom``,
            ``rselected``, ``cselected``, ``pselected``, ``wc``.
        refresh: Force a network fetch, ignoring the TTL. Still falls
            back to cached data if the fetch fails.

    Returns:
        List of records. Empty only when every source failed, which
        means discovery degrades rather than breaking.

    Example::

        from georest.restusfs import vocab

        rows = vocab.load_catalog("wc")          # every FIA evaluation, as dicts
        len(rows), sorted(rows[0])[:4]
    """
    name = FIA_CATALOG_ALIASES.get(name, name)
    if name not in FIA_CATALOGS:
        raise ValueError(
            f"unknown catalog {name!r}; expected one of "
            f"{sorted(set(FIA_CATALOGS) | set(FIA_CATALOG_ALIASES))}"
        )

    if not refresh and name in _MEM_CACHE:
        return _MEM_CACHE[name]

    cached = _read_json_file(_cache_path(name))
    need_fetch = refresh or cached is None or _is_stale(_cache_path(name))

    if need_fetch:
        try:
            rows = _fetch_fia_catalog(name)
            _write_json_file(_cache_path(name), rows)
            return _remember(name, rows)
        except (UpstreamError, UpstreamUnavailable) as exc:
            # Serve stale rather than fail. A vocabulary that is a month
            # out of date is enormously more useful than an exception,
            # and the caller is usually mid-exploration.
            logger.info(
                "restusfs: catalog %r refresh failed (%s); using "
                "cached/bundled copy", name, exc,
            )

    rows = cached if cached is not None else _read_json_file(_bundled_path(name))
    if rows is None:
        logger.warning(
            "restusfs: no data for catalog %r: network unavailable and "
            "no bundled snapshot found; discovery will return nothing",
            name,
        )
        rows = []
    return _remember(name, rows)


def _lookup(name: str, key: Any) -> dict | None:
    """One row of ``snum`` or ``wc`` by its number, via an index built
    once per loaded catalog instead of a scan per call."""
    rows = load_catalog(name)
    index = _INDEX.get(name)
    if index is None:
        field = _INDEX_KEY[name]
        index = {}
        for r in rows:
            index.setdefault(r.get(field), r)
        _INDEX[name] = index
    row = index.get(key)
    return dict(row) if row is not None else None


def refresh_all(*, quiet: bool = False) -> dict[str, int]:
    """Force-refresh every catalog. Returns ``{name: record_count}``.

    The escape hatch for "a new evaluation just dropped and I do not want
    to wait out the TTL". Normal use should never need it.

    Example::

        from georest.restusfs import vocab

        vocab.refresh_all()   # prints each catalog's record count; returns {name: count}
    """
    out: dict[str, int] = {}
    for name in FIA_CATALOGS:
        rows = load_catalog(name, refresh=True)
        out[name] = len(rows)
        if not quiet:
            print(f"  {name:12s} {len(rows):5d} records")
    return out


# -- Search -------------------------------------------------------------------

def _score(text: str, terms: list[str]) -> int:
    """Count how many terms appear in ``text``, weighting whole words.

    Substring matching alone ranks "carbon" equally against "carbon" and
    "hydrocarbon-adjacent"; the word-boundary bonus keeps the obvious hit
    on top without needing a real index.
    """
    t = text.lower()
    score = 0
    for term in terms:
        if term in t:
            score += 1
            if f" {term} " in f" {t} " or t.startswith(term):
                score += 2
    # The whole query as a phrase outranks the same words scattered.
    # Without this, "area of forest land" ranked four "Area change of
    # forest land ..." attributes above "Area of forest land, in acres",
    # because every word matches all five.
    if score and len(terms) > 1 and " ".join(terms) in t:
        score += 3 * len(terms)
    return score


def find_attributes(query: str = "", *, land_basis: str = "",
                    eval_typ: str = "", limit: int | None = None) -> Any:
    """Search the 752 FIA estimate attributes.

    Args:
        query: Free text matched against description and group, e.g.
            ``"carbon"``, ``"net growth volume"``, ``"mortality"``.
        land_basis: Restrict to ``"Forest land"`` or ``"Timberland"``.
        eval_typ: Restrict to an evaluation type: ``EXPCURR``,
            ``EXPVOL``, ``EXPGROW``, ``EXPMORT``, ``EXPREMV``,
            ``EXPCHNG``, ``EXPDWM``.
        limit: Return at most this many rows. By default every match is
            returned, best first.

    Returns:
        A ``pandas.DataFrame`` with the columns that matter for choosing
        an attribute: number, description, units, and the evaluation
        type it requires. The full record is available via
        :func:`get_attribute`.

    Note:
        **FIA's own catalog contains duplicate descriptions**: 7 of the
        752 appear more than once. ``snum`` 209 and 956, for instance,
        are identical across description, estimate group, evaluation
        type, estimation basis and tree portion. Both are returned
        rather than silently de-duplicated, because they are distinct
        attribute numbers upstream and collapsing them would hide a real
        property of the catalog. Where descriptions match on every
        field, either number should produce the same estimate.

    Example::

        from georest.restusfs import fia

        # Every carbon attribute measured on forest land, best match first
        fia.find_attributes("carbon", land_basis="Forest land")

        # Attributes that need growth accounting (growth, removals, mortality)
        fia.find_attributes("mortality", eval_typ="EXPMORT")
    """
    rows = load_catalog("snum")
    terms = [t for t in query.lower().split() if t]

    hits = []
    for r in rows:
        if land_basis and str(r.get("LAND_BASIS", "")).lower() != land_basis.lower():
            continue
        if eval_typ and str(r.get("EVAL_TYP", "")).upper() != eval_typ.upper():
            continue
        blob = f"{r.get('ATTRIBUTE_DESCR', '')} {r.get('ESTIMATE_GRP_DESCR', '')}"
        s = _score(blob, terms) if terms else 1
        if s:
            hits.append((s, r))

    hits.sort(key=lambda x: (-x[0], x[1].get("ATTRIBUTE_NBR", 0)))
    if limit is not None:
        hits = hits[:limit]
    return _frame([{
        "snum": r.get("ATTRIBUTE_NBR"),
        "description": r.get("ATTRIBUTE_DESCR"),
        "group": r.get("ESTIMATE_GRP_DESCR"),
        "land_basis": r.get("LAND_BASIS"),
        "units": r.get("ESTN_UNITS_DISPLAY"),
        "eval_typ": r.get("EVAL_TYP"),
        "basis": r.get("CONDTREESEED"),
    } for _, r in hits])


def get_attribute(snum: int) -> dict | None:
    """Full record for one attribute number, or None.

    Example::

        from georest.restusfs import fia

        fia.get_attribute(2)["ATTRIBUTE_DESCR"]   # 'Area of forest land, in acres'
    """
    return _lookup("snum", snum)


def find_groupings(query: str = "", *, limit: int | None = None) -> Any:
    """Search the 96 FIA grouping variables.

    These are what ``rselected`` / ``cselected`` / ``pselected`` accept,
    and they are passed as *display strings*, so the exact ``label`` in
    this result is what the query needs, character for character.

    Args:
        query: Free text matched against the label and database column.
        limit: Return at most this many rows. By default every match is
            returned, best first.

    Example::

        from georest.restusfs import fia

        fia.find_groupings("owner")   # labels to pass as rselected= / cselected=
    """
    rows = load_catalog("rselected")
    terms = [t for t in query.lower().split() if t]

    hits = []
    for r in rows:
        blob = f"{r.get('LABEL_VAR', '')} {r.get('DB_VAR', '')}"
        s = _score(blob, terms) if terms else 1
        if s:
            hits.append((s, r))

    hits.sort(key=lambda x: (-x[0], str(x[1].get("LABEL_VAR", ""))))
    if limit is not None:
        hits = hits[:limit]
    return _frame([{
        "label": r.get("LABEL_VAR"),
        "db_column": r.get("DB_VAR"),
        "has_metadata": bool(r.get("PRC_METADATA")),
    } for _, r in hits])


def grouping_labels() -> list[str]:
    """Every grouping label, exactly as ``rselected`` and friends need it.

    Example::

        from georest.restusfs import vocab

        "Forest type group" in vocab.grouping_labels()   # True
    """
    return [str(r.get("LABEL_VAR") or "") for r in load_catalog("rselected")]


def describe_grouping(label: str) -> str:
    """Prose documentation for a grouping variable, codes included.

    FIA ships this as ``PRC_METADATA``, an HTML fragment that usually
    enumerates what each code value means. It is the difference between
    a column of integers and a column you can interpret, so it is worth
    surfacing rather than leaving buried in the catalog.

    Example::

        from georest.restusfs import fia

        # What each code of a grouping means, as plain text
        fia.describe_grouping("Ownership group - Major")
    """
    for r in load_catalog("rselected"):
        if str(r.get("LABEL_VAR", "")).lower() == label.lower():
            html = r.get("PRC_METADATA") or ""
            text = re.sub(r"<[^>]+>", " ", html)
            return re.sub(r"\s+", " ", text).strip() or "(no metadata provided)"
    return f"(no grouping named {label!r}; try find_groupings())"


def find_evaluations(state: str = "", *, most_recent: bool = True,
                     growth_only: bool = False,
                     limit: int | None = None) -> Any:
    """Search the 1,143 FIA evaluations.

    Args:
        state: State name, matched case-insensitively on a prefix.
        most_recent: Keep only each state's current evaluation. Almost
            always what you want; set False to see the back catalog.
        growth_only: Keep only evaluations with growth accounting
            (``GROWTH_ACCT == 'Y'``), which is a prerequisite for every
            growth, removals, and mortality attribute.
        limit: Return at most this many rows. By default every match is
            returned.

    Example::

        from georest.restusfs import fia

        fia.find_evaluations("Oregon")                     # the current inventory
        fia.find_evaluations("Oregon", most_recent=False)  # and every earlier one
        fia.find_evaluations("Oregon", growth_only=True)   # usable for growth and mortality
    """
    rows = load_catalog("wc")
    hits = []
    for r in rows:
        if most_recent and str(r.get("MOST_RECENT", "")).upper() != "Y":
            continue
        if growth_only and str(r.get("GROWTH_ACCT", "")).upper() != "Y":
            continue
        if state and not str(r.get("STATE", "")).lower().startswith(state.lower()):
            continue
        hits.append(r)

    hits.sort(key=lambda r: (str(r.get("STATE", "")), -int(r.get("EVAL_GRP", 0) or 0)))
    if limit is not None:
        hits = hits[:limit]
    return _frame([{
        "wc": r.get("EVAL_GRP"),
        "state": r.get("STATE"),
        "statecd": r.get("STATECD"),
        "years": r.get("REPORT_YEAR_NM"),
        "growth_acct": r.get("GROWTH_ACCT"),
        "most_recent": r.get("MOST_RECENT"),
    } for r in hits])


def get_evaluation(wc: int) -> dict | None:
    """Full record for one evaluation group, or None.

    Example::

        from georest.restusfs import fia

        fia.get_evaluation(412024)["REPORT_YEAR_NM"]   # '2015;2016;...;2024'
    """
    return _lookup("wc", wc)


def _frame(records: list[dict], columns=None):
    """A DataFrame of ``records``, keeping ``columns`` even when empty."""
    import pandas as pd

    if not records and columns:
        return pd.DataFrame(columns=list(columns))
    return pd.DataFrame(records)
