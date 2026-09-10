"""Report drift between the live EDW catalog and edw._SERVICE_THEMES.

_SERVICE_THEMES is hand-curated and powers theme filtering plus description
matching in search_edw_services. EDW publishes and retires services without
notice, so the table rots quietly: an untyped service still turns up in
search results, but as theme "uncategorized" with an empty description, so
theme filters and keyword aliases stop reaching it.

    georest-check-themes            # report drift
    georest-check-themes --emit     # + paste-ready entries

Exit status is 0 when the table is in sync, 1 when it has drifted, and 2 if
the catalog could not be reached.
"""
from __future__ import annotations

import argparse
import sys
import time

from .._http import fetch_json
from ..edw import _SERVICE_THEMES, EDW_BASE_URL, search_edw_services, theme_drift

#: Longest a generated description may run before being elided, matching the
#: existing entries in _SERVICE_THEMES.
DESC_LIMIT = 150


def retry(func, tries=6, delay=3.0):
    """EDW answers 404/500/'Layer not found' intermittently; retry through it."""
    last = None
    for attempt in range(tries):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            last = exc
            if attempt < tries - 1:
                print(f"  attempt {attempt + 1} failed ({str(exc)[:60]}); retrying",
                      file=sys.stderr)
                time.sleep(delay)
    raise SystemExit(f"could not reach EDW after {tries} attempts: {last}")


def describe(service_name: str) -> str:
    """Best-effort one-line description from a service's own MapServer doc."""
    try:
        data = fetch_json(f"{EDW_BASE_URL}/{service_name}/MapServer",
                          {"f": "pjson"})
    except Exception:  # noqa: BLE001 - a missing description is not fatal
        return ""
    text = " ".join(
        ((data.get("description") or "").strip()
         or (data.get("serviceDescription") or "").strip()).split()
    )
    if len(text) > DESC_LIMIT:
        text = text[:DESC_LIMIT].rstrip() + "…"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit", action="store_true",
                        help="print paste-ready _SERVICE_THEMES entries for "
                             "services that lack one")
    args = parser.parse_args()

    print(f"fetching the EDW catalog from {EDW_BASE_URL} ...")
    try:
        services = retry(search_edw_services)
    except SystemExit as exc:
        # `retry` gives up by raising SystemExit with a string message. Handle
        # it here rather than under `if __name__ == "__main__"`: the generated
        # console-script wrapper calls `sys.exit(main())` directly and would
        # bypass a module-level handler, so `georest-check-themes` and
        # `python -m` would disagree about the exit code.
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise
    drift = theme_drift(services)
    mapservers = [s for s in services if s.get("type") == "MapServer"]

    print(f"\n  live catalog     {len(services)} services "
          f"({len(mapservers)} MapServer)")
    print(f"  _SERVICE_THEMES  {len(_SERVICE_THEMES)} entries")

    for label, heading in (
        ("untyped", "MapServer services with NO theme entry "
                    "(searchable, but uncategorized and undescribed)"),
        ("orphaned", "theme entries for services no longer in the catalog"),
        ("bad_theme", "entries whose theme is not a known theme"),
        ("missing_desc", "entries with an empty description"),
        ("non_mapserver", "non-MapServer services (informational — these "
                          "carry no layers and need no theme)"),
    ):
        names = drift[label]
        print(f"\n  {heading}: {len(names)}")
        for name in names:
            print(f"    {name}")

    if args.emit and drift["untyped"]:
        print("\n" + "=" * 70)
        print("Paste into _SERVICE_THEMES in src/georest/restesri/edw.py, under")
        print("the right theme heading, replacing THEME with the category:")
        print("=" * 70)
        for name in drift["untyped"]:
            desc = describe(name).replace('"', "'")
            print(f'    "{name}": {{"theme": "THEME", "desc": "{desc}"}},')

    # non_mapserver is informational and deliberately excluded here.
    drifted = any(drift[key] for key in
                  ("untyped", "orphaned", "bad_theme", "missing_desc"))
    if drifted:
        print("\nRESULT: the theme table has drifted from the live catalog.")
        if not args.emit and drift["untyped"]:
            print("Re-run with --emit for paste-ready entries.")
        return 1
    print("\nRESULT: theme table is in sync with the live catalog.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
