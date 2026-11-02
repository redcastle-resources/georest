"""Client for OpenFEMA's disaster declarations (fema.gov/api/open).

Every federal disaster declaration since 1953 - major disasters (DR),
emergencies (EM) and fire management assistance (FM) - and the counties
(and tribal areas) each one covers::

    from georest.fema import openfema

    openfema.disasters(state="TX", since="2025-01-01")    # one row per disaster
    openfema.declarations(fips="48201")                    # Harris County, TX
    openfema.declarations(number=4332)                     # Hurricane Harvey, by county

**A row is a declared area, not a disaster.** OpenFEMA publishes one row
per county (or tribal area, or "Statewide") per declaration: Hurricane
Harvey (DR-4332) is 53 rows, and Texas's declarations since 1 January 2025
are 100 rows but 16 disasters. Counting :func:`declarations` rows as
disasters overcounts; :func:`disasters` groups them, one row per disaster
with the number of areas it covers.

Results are complete: OpenFEMA serves at most 10,000 rows a request, so
both functions page until they have every match.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ._http import fetch_json

DECLARATIONS_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"

#: OpenFEMA's declaration types.
DECLARATION_TYPES = {"DR": "Major Disaster", "EM": "Emergency",
                     "FM": "Fire Management Assistance"}

#: Rows per request; OpenFEMA's maximum. Every page is fetched.
_PAGE_SIZE = 10_000

_FIELDS = {
    "disaster_number": "disasterNumber",
    "declaration_string": "femaDeclarationString",
    "declaration_type": "declarationType",
    "declaration_date": "declarationDate",
    "state": "state",
    "designated_area": "designatedArea",
    "fips_state": "fipsStateCode",
    "fips_county": "fipsCountyCode",
    "incident_type": "incidentType",
    "title": "declarationTitle",
    "incident_begin": "incidentBeginDate",
    "incident_end": "incidentEndDate",
    "ih_program": "ihProgramDeclared",
    "ia_program": "iaProgramDeclared",
    "pa_program": "paProgramDeclared",
    "hm_program": "hmProgramDeclared",
    "closeout_date": "disasterCloseoutDate",
    "id": "id",
}
_DATES = ("declaration_date", "incident_begin", "incident_end", "closeout_date")
_DISASTER_COLUMNS = ("disaster_number", "declaration_string", "declaration_type",
                     "declaration_date", "state", "incident_type", "title",
                     "incident_begin", "incident_end", "areas")


def _quoted(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _iso(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        value = value.isoformat()
    text = str(value).strip()
    try:
        date.fromisoformat(text[:10])
    except ValueError:
        raise ValueError(f"{value!r} is not a date (use 'YYYY-MM-DD')") from None
    return f"{text[:10]}T00:00:00.000Z"


def _filter(state, fips, incident_type, declaration_type, since, until, number) -> str:
    parts = []
    if number is not None:
        parts.append(f"disasterNumber eq {int(number)}")
    if state:
        parts.append(f"state eq {_quoted(str(state).upper())}")
    if fips:
        code = str(fips).strip()
        if not (code.isdigit() and len(code) == 5):
            raise ValueError(f"fips must be a 5-digit county code such as '48201', not {fips!r}")
        parts.append(f"fipsStateCode eq {_quoted(code[:2])} and fipsCountyCode eq {_quoted(code[2:])}")
    if incident_type:
        parts.append(f"incidentType eq {_quoted(incident_type)}")
    if declaration_type:
        dtype = str(declaration_type).upper()
        if dtype not in DECLARATION_TYPES:
            raise ValueError(f"declaration_type must be one of {sorted(DECLARATION_TYPES)}, "
                             f"not {declaration_type!r}")
        parts.append(f"declarationType eq {_quoted(dtype)}")
    if since:
        parts.append(f"declarationDate ge {_quoted(_iso(since))}")
    if until:
        parts.append(f"declarationDate le {_quoted(_iso(until))}")
    return " and ".join(parts)


def _rows(where: str) -> list[dict]:
    """Every matching row, paging with a stable order until the count is met."""
    params = {"$select": ",".join(_FIELDS.values()), "$orderby": "declarationDate desc,id",
              "$top": str(_PAGE_SIZE), "$count": "true"}
    if where:
        params["$filter"] = where
    rows: list[dict] = []
    total = None
    while total is None or len(rows) < total:
        page = fetch_json(DECLARATIONS_URL, {**params, "$skip": str(len(rows))})
        if total is None:
            # Only filled in because $count=true was sent: without it,
            # OpenFEMA reports a count of 0 however many rows match.
            total = int((page.get("metadata") or {}).get("count") or 0)
        got = page.get("DisasterDeclarationsSummaries") or []
        if not got:
            break
        rows.extend(got)
    if total is not None and len(rows) < total:
        raise RuntimeError(f"OpenFEMA stopped after {len(rows):,} of {total:,} rows")
    return rows


def _row(raw: dict) -> dict:
    row = {name: raw.get(field) for name, field in _FIELDS.items()}
    for name in _DATES:
        if row[name]:
            row[name] = str(row[name])[:10]
    fs, fc = row.pop("fips_state"), row.pop("fips_county")
    row["fips"] = f"{fs}{fc}" if fs and fc else None
    return row


def declarations(state: str | None = None, *, fips: str | None = None,
                 incident_type: str | None = None, declaration_type: str | None = None,
                 since: str | date | None = None, until: str | date | None = None,
                 number: int | None = None) -> Any:
    """Declared areas, one row per county (or tribal area) per declaration.

    Args:
        state: Two-letter state or territory code, e.g. ``"TX"``.
        fips: A 5-digit county FIPS code, e.g. ``"48201"`` (Harris County).
            Statewide designations carry county code ``"000"`` and so do not
            match a county's code.
        incident_type: OpenFEMA's incident type, exactly - ``"Hurricane"``,
            ``"Flood"``, ``"Severe Storm"``, ``"Fire"``, ``"Biological"``, ...
        declaration_type: ``"DR"``, ``"EM"`` or ``"FM"``.
        since, until: Declaration dates, inclusive (``"2025-01-01"`` or a
            ``date``).
        number: One disaster number, e.g. ``4332``.

    Returns:
        A DataFrame, newest declaration first, with ``disaster_number``,
        ``declaration_string`` (``"DR-4332-TX"``), ``declaration_type``,
        ``declaration_date``, ``state``, ``designated_area``, ``fips``,
        ``incident_type``, ``title``, ``incident_begin``, ``incident_end``,
        the programs declared (``ih_program`` individuals and households,
        ``ia_program``, ``pa_program`` public assistance, ``hm_program``
        hazard mitigation), ``closeout_date`` and OpenFEMA's row ``id``.
        Dates are ``YYYY-MM-DD``. A few areas appear twice under one
        declaration as separate OpenFEMA records (some tribal areas in
        DR-4529, for one); the ``id`` tells them apart.
        No filter at all returns every row OpenFEMA has (tens of thousands).

    Raises:
        ValueError: For a malformed ``fips``, ``declaration_type`` or date,
            before any request.
        RuntimeError: If OpenFEMA refuses the query, cannot be reached, or
            stops short of its own count.

    Example::

        from georest.fema import openfema

        openfema.declarations(fips="48201", since="2017-01-01")[
            ["declaration_string", "declaration_date", "incident_type", "title"]]
    """
    import pandas as pd

    where = _filter(state, fips, incident_type, declaration_type, since, until, number)
    rows = [_row(r) for r in _rows(where)]
    columns = [c for c in _FIELDS if c not in ("fips_state", "fips_county")] + ["fips"]
    return pd.DataFrame(rows, columns=columns)


def disasters(state: str | None = None, *, fips: str | None = None,
              incident_type: str | None = None, declaration_type: str | None = None,
              since: str | date | None = None, until: str | date | None = None) -> Any:
    """Disasters, one row each - the declared areas grouped by disaster number.

    Args:
        state: Two-letter state or territory code, e.g. ``"TX"``.
        fips: A 5-digit county FIPS code, e.g. ``"48201"``: the disasters
            that covered that county.
        incident_type: OpenFEMA's incident type, exactly - ``"Hurricane"``,
            ``"Flood"``, ``"Severe Storm"``, ``"Fire"``, ...
        declaration_type: ``"DR"``, ``"EM"`` or ``"FM"``.
        since, until: Declaration dates, inclusive (``"2025-01-01"`` or a
            ``date``).

    Returns:
        A DataFrame, newest first, with ``disaster_number``,
        ``declaration_string``, ``declaration_type``, ``declaration_date``,
        ``state``, ``incident_type``, ``title``, ``incident_begin``,
        ``incident_end`` and ``areas``: how many distinct declared areas
        matched the filters (all of the disaster's areas when no ``fips`` is
        given). No rows means no declaration matched, not that nothing
        happened: a declaration is a federal decision, not a record of every
        event.

    Raises:
        ValueError: For a malformed ``fips``, ``declaration_type`` or date,
            before any request.
        RuntimeError: If OpenFEMA refuses the query, cannot be reached, or
            stops short of its own count.

    Example::

        from georest.fema import openfema

        openfema.disasters(state="TX", since="2025-01-01")[
            ["declaration_string", "title", "areas"]]
    """
    import pandas as pd

    df = declarations(state, fips=fips, incident_type=incident_type,
                      declaration_type=declaration_type, since=since, until=until)
    if df.empty:
        return pd.DataFrame(columns=list(_DISASTER_COLUMNS))
    grouped = (df.groupby("disaster_number", sort=False)
               .agg(declaration_string=("declaration_string", "first"),
                    declaration_type=("declaration_type", "first"),
                    declaration_date=("declaration_date", "first"),
                    state=("state", "first"),
                    incident_type=("incident_type", "first"),
                    title=("title", "first"),
                    incident_begin=("incident_begin", "min"),
                    incident_end=("incident_end", "max"),
                    areas=("designated_area", "nunique"))
               .reset_index())
    return grouped.sort_values(["declaration_date", "disaster_number"],
                               ascending=False).reset_index(drop=True)[list(_DISASTER_COLUMNS)]
