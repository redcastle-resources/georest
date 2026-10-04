"""Sign in to ArcGIS Online or an ArcGIS Enterprise portal for georest.

Opens a browser for an OAuth 2.0 sign-in (enterprise SSO works) and saves
the refresh token, so later processes - an MCP server, a notebook - pick
the session up without a browser:

    georest-login --portal https://myorg.maps.arcgis.com --client-id abc123
    georest-login --status
    georest-login --logout

--portal and --client-id fall back to GEOREST_ESRI_PORTAL and
GEOREST_ESRI_CLIENT_ID. The app must list http://127.0.0.1:<port>/callback
as a redirect URL. Exit status is 0 on success, 1 otherwise.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
from __future__ import annotations

import argparse
import sys

from .. import auth

#: `--help` text, without the license header (see check_themes._HELP).
_HELP = __doc__.split("\nCopyright ")[0].rstrip()


def _print_status(info: dict | None) -> None:
    if info is None:
        print("not signed in")
        return
    for key in ("portal", "username", "source", "expires", "signin_expires", "refreshable"):
        print(f"  {key:15} {info.get(key)}")
    print(f"  {'hosts':15} {', '.join(info.get('hosts') or [])}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--portal", help="portal URL or short name (e.g. agol)")
    parser.add_argument("--client-id", help="the registered app's client id")
    parser.add_argument("--port", type=int, default=auth.DEFAULT_PORT,
                        help=f"local redirect port (default {auth.DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="print the sign-in URL instead of opening it")
    parser.add_argument("--no-save", action="store_true",
                        help="do not save the session to disk")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="show the saved session")
    group.add_argument("--logout", action="store_true",
                       help="forget the session and delete saved credentials")
    args = parser.parse_args()

    if args.logout:
        auth.logout()
        print("signed out")
        return 0
    if args.status:
        info = auth.status()
        _print_status(info)
        return 0 if info else 1

    try:
        info = auth.login(args.portal, args.client_id, port=args.port,
                          open_browser=not args.no_browser, persist=not args.no_save)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"sign-in failed: {exc}", file=sys.stderr)
        return 1
    print("signed in")
    _print_status(info)
    if not args.no_save:
        print(f"  {'saved to':15} {auth.CREDENTIALS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
