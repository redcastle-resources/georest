"""
Sign in to ArcGIS Online or ArcGIS Enterprise (Portal) as a user.

Once a session exists, every ``portal`` and ``services`` call that is not
given an explicit ``token=`` sends the session's token, but only to hosts
the session covers: the portal itself, ``*.arcgis.com`` for ArcGIS Online, the
portal's federated servers, and anything added with :func:`trust_host`. A
public service on another host never receives it.

Quick start::

    from georest.restesri import auth

    # OAuth 2.0 (authorization code + PKCE). Works with enterprise SSO
    # (SAML / eAuth) because the login happens in the browser.
    auth.login("https://myorg.maps.arcgis.com", client_id="abc123")

    # Or hand in a token from somewhere else, e.g. ArcGIS Pro via the
    # arcgis API (run inside Pro's Python environment):
    from arcgis.gis import GIS
    gis = GIS("Pro")
    auth.set_token(gis._con.token, portal=gis.url,
                   refresh=lambda: gis._con.token)

    auth.status()     # portal, user, expiry - never the token
    auth.logout()

For a long-running process such as an MCP server, sign in once from a
terminal with ``georest-login`` (which calls ``login(persist=True)``). The
server then picks the saved session up on its first request; nothing about
credentials ever appears in a tool schema.

One-time setup: register an app in the org (Content > New item > Developer
credentials > OAuth 2.0 credentials), add the redirect URL
``http://127.0.0.1:8765/callback``, and use its client id. PKCE needs no
client secret.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import html
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import uuid
import warnings
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ._http import fetch_json, format_esri_error, post_json

#: Environment variables read when no argument is given.
CLIENT_ID_ENV = "GEOREST_ESRI_CLIENT_ID"
PORTAL_ENV = "GEOREST_ESRI_PORTAL"
#: A raw token, used only when no session exists. Needs PORTAL_ENV too, which
#: decides the hosts it may be sent to.
TOKEN_ENV = "GEOREST_ESRI_TOKEN"

DEFAULT_PORT = 8765
#: Where ``login(persist=True)`` keeps the refresh token.
CREDENTIALS_PATH = Path.home() / ".georest" / "esri_credentials.json"

_REFRESH_MARGIN = 120   # seconds before expiry at which a token is renewed
_LOGIN_TIMEOUT = 300    # seconds to wait for the browser sign-in
_DEFAULT_EXPIRES_IN = 1800

RefreshFn = Callable[[], "str | tuple[str, Any]"]


@dataclass
class _Session:
    portal_url: str
    token: str | None
    expires: float | None          # epoch seconds; None = unknown
    hosts: frozenset[str]          # exact hosts, or ".suffix" entries
    source: str                    # "oauth", "token" or "env"
    referer: str | None = None
    username: str | None = None
    refresh: RefreshFn | None = None
    oauth: dict[str, Any] | None = None   # client_id, refresh_token, refresh_expires
    ident: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def refreshable(self) -> bool:
        return self.oauth is not None or self.refresh is not None


_SESSION: _Session | None = None
_LOCK = threading.RLock()
_RESTORE_TRIED = False
_EXTRA_HOSTS: set[str] = set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def login(
    portal: str | None = None,
    client_id: str | None = None,
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    persist: bool = False,
    timeout: float = _LOGIN_TIMEOUT,
) -> dict[str, Any]:
    """Sign in through the browser with OAuth 2.0 authorization code + PKCE.

    Args:
        portal: Portal URL (an org URL such as ``https://myorg.maps.arcgis.com``
            shows the org's SSO button directly) or a short name from
            ``portal.PORTALS``. Defaults to ``$GEOREST_ESRI_PORTAL``.
        client_id: The registered app's client id. Defaults to
            ``$GEOREST_ESRI_CLIENT_ID``.
        port: Local port for the redirect. The app must list
            ``http://127.0.0.1:<port>/callback`` as a redirect URL.
        open_browser: Open the sign-in page automatically. The URL is always
            printed to stderr as well.
        persist: Save the refresh token to :data:`CREDENTIALS_PATH` so later
            processes sign in without a browser.
        timeout: Seconds to wait for the sign-in to finish.

    Returns:
        :func:`status` for the new session.

    Raises:
        ValueError: No portal or client id.
        RuntimeError: Sign-in failed, was cancelled, or timed out.
    """
    portal_url = _resolve_portal(portal)
    client_id = client_id or os.environ.get(CLIENT_ID_ENV, "").strip()
    if not client_id:
        raise ValueError(f"No client id: pass client_id= or set {CLIENT_ID_ENV}.")

    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    authorize_url = f"{portal_url}/sharing/rest/oauth2/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
    })

    # Bound before the browser opens, so a fast redirect cannot miss it.
    server = _CallbackServer(("127.0.0.1", port), _CallbackHandler)
    try:
        print(f"Sign in to {portal_url} in your browser. If it does not open, visit:\n"
              f"  {authorize_url}", file=sys.stderr)
        if open_browser:
            webbrowser.open(authorize_url)
        query = server.wait(timeout)
    finally:
        server.server_close()

    if query.get("state") != state:
        raise RuntimeError("Sign-in response did not match this request (state mismatch).")
    if not query.get("code"):
        reason = query.get("error_description") or query.get("error") or "no code returned"
        raise RuntimeError(f"Sign-in failed: {reason}")

    data = _token_request(portal_url, {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": query["code"],
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    })
    session = _oauth_session(portal_url, client_id, data)
    _install(session)
    if persist:
        _save(session)
    return status()


def set_token(
    token: str,
    portal: str | None = None,
    *,
    expires: Any = None,
    referer: str | None = None,
    refresh: RefreshFn | None = None,
) -> dict[str, Any]:
    """Use a token obtained elsewhere, e.g. from the arcgis API's ``GIS``.

    Args:
        token: The access token.
        portal: The portal it was issued by (URL or short name). Defaults to
            ``$GEOREST_ESRI_PORTAL``.
        expires: When it expires: epoch seconds, epoch milliseconds, or a
            ``datetime``. Unknown by default.
        referer: Sent as the ``Referer`` header, for tokens bound to one.
        refresh: Optional zero-argument callable returning a fresh token (or
            ``(token, expires)``). Called shortly before ``expires``, and once
            when a request is rejected as an invalid token (Esri code 498).

    Returns:
        :func:`status` for the new session.
    """
    if not token:
        raise ValueError("token must be a non-empty string.")
    portal_url = _resolve_portal(portal)
    session = _Session(
        portal_url=portal_url,
        token=token,
        expires=_as_epoch(expires),
        hosts=_hosts_for(portal_url, token),
        source="token",
        referer=referer,
        refresh=refresh,
    )
    _install(session)
    return status()


def restore(path: str | os.PathLike | None = None) -> dict[str, Any] | None:
    """Load a session saved by ``login(persist=True)``.

    Called automatically on the first request when no session exists, so a
    process started after ``georest-login`` needs no code at all. The access
    token is fetched on first use.

    Returns:
        :func:`status`, or ``None`` if nothing usable is saved.
    """
    global _RESTORE_TRIED
    file = Path(path) if path else CREDENTIALS_PATH
    with _LOCK:
        _RESTORE_TRIED = True
        try:
            saved = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            warnings.warn(f"Ignoring unreadable ArcGIS credentials at {file}: {exc}", stacklevel=2)
            return None
        try:
            refresh_expires = saved.get("refresh_expires")
            if refresh_expires is not None and refresh_expires <= time.time():
                warnings.warn(
                    f"Saved ArcGIS sign-in for {saved['portal']} has expired; "
                    "run georest-login to sign in again.", stacklevel=2)
                return None
            session = _Session(
                portal_url=saved["portal"],
                token=None,
                expires=0,
                hosts=frozenset(saved["hosts"]),
                source="oauth",
                username=saved.get("username"),
                oauth={
                    "client_id": saved["client_id"],
                    "refresh_token": saved["refresh_token"],
                    "refresh_expires": refresh_expires,
                },
            )
        except (KeyError, TypeError, AttributeError) as exc:
            warnings.warn(f"Ignoring malformed ArcGIS credentials at {file}: {exc!r}", stacklevel=2)
            return None
        _install(session)
    return status()


def logout() -> None:
    """End the session and delete any saved credentials.

    ``$GEOREST_ESRI_TOKEN`` is not touched; unset it to go fully anonymous.
    """
    global _SESSION, _RESTORE_TRIED
    with _LOCK:
        _SESSION = None
        _RESTORE_TRIED = True
        try:
            CREDENTIALS_PATH.unlink()
        except FileNotFoundError:
            pass


def status() -> dict[str, Any] | None:
    """Describe the active session, or ``None``. Never includes the token."""
    session = _current()
    if session is None:
        return None
    expires = (_dt.datetime.fromtimestamp(session.expires, _dt.timezone.utc).isoformat()
               if session.expires else None)
    refresh_expires = (session.oauth or {}).get("refresh_expires")
    return {
        "portal": session.portal_url,
        "username": session.username,
        "source": session.source,
        "expires": expires,
        "refreshable": session.refreshable,
        "signin_expires": (_dt.datetime.fromtimestamp(refresh_expires, _dt.timezone.utc).isoformat()
                           if refresh_expires else None),
        "hosts": sorted(session.hosts | _EXTRA_HOSTS),
    }


def trust_host(host: str) -> None:
    """Allow the session token to be sent to ``host`` as well.

    A leading dot trusts every subdomain: ``".gis.myagency.gov"``. Use for an
    ArcGIS Server that trusts the portal but is not listed as federated.
    """
    host = host.strip().lower()
    if not host:
        raise ValueError("host must be non-empty.")
    with _LOCK:
        _EXTRA_HOSTS.add(host)


def credentials_for(url: str) -> tuple[str | None, dict[str, str]]:
    """The session token and extra headers to send to ``url``, if any.

    Returns ``(None, {})`` when no session covers the URL's host, when the URL
    is plain http and the portal is not, or when the token has expired and
    cannot be renewed (an expired token makes even public services answer
    498, so sending none is the better failure).
    """
    session = _current()
    if session is None or not _covers(session, url):
        return None, {}
    token = _fresh_token(session)
    if not token:
        return None, {}
    return token, ({"Referer": session.referer} if session.referer else {})


# ---------------------------------------------------------------------------
# Internals used by _http and portal
# ---------------------------------------------------------------------------


def _session_key(url: str) -> str | None:
    """A stable id for the session that would sign a request to ``url``."""
    session = _current()
    return session.ident if session is not None and _covers(session, url) else None


def _refresh_rejected() -> bool:
    """Renew the session token after a 498. True if a retry is worthwhile."""
    with _LOCK:
        session = _SESSION
        if session is None or not session.refreshable:
            return False
        return _try_refresh(session)


# ---------------------------------------------------------------------------
# Session plumbing
# ---------------------------------------------------------------------------


def _install(session: _Session) -> None:
    global _SESSION, _RESTORE_TRIED
    with _LOCK:
        _SESSION = session
        _RESTORE_TRIED = True


def _current() -> _Session | None:
    with _LOCK:
        if _SESSION is None and not _RESTORE_TRIED:
            restore()
        if _SESSION is not None:
            return _SESSION
    return _env_session()


def _env_session() -> _Session | None:
    token = os.environ.get(TOKEN_ENV, "").strip()
    portal = os.environ.get(PORTAL_ENV, "").strip()
    if not token or not portal:
        return None
    portal_url = _resolve_portal(portal)
    return _Session(portal_url=portal_url, token=token, expires=None,
                    hosts=_default_hosts(portal_url), source="env",
                    ident=f"env:{portal_url}")


def _fresh_token(session: _Session) -> str | None:
    with _LOCK:
        now = time.time()
        if session.token and (session.expires is None or session.expires - now > _REFRESH_MARGIN):
            return session.token
        if session.refreshable and _try_refresh(session):
            return session.token
        if session.token and session.expires is not None and session.expires > now:
            return session.token      # close to expiry but still valid
        return None


def _try_refresh(session: _Session) -> bool:
    global _SESSION
    try:
        if session.oauth is not None:
            token, expires = _oauth_refresh(session)
        else:
            result = session.refresh()  # type: ignore[misc]
            token, expires = result if isinstance(result, tuple) else (result, None)
            expires = _as_epoch(expires)
        if not token:
            raise RuntimeError("refresh returned no token")
    except Exception as exc:  # noqa: BLE001 - reported, then anonymous
        warnings.warn(
            f"ArcGIS sign-in for {session.portal_url} could not be renewed ({exc}); "
            "continuing anonymously. Run georest-login to sign in again.",
            stacklevel=3,
        )
        if _SESSION is session:
            _SESSION = None
        return False
    session.token, session.expires = token, expires
    return True


def _covers(session: _Session, url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if parsed.scheme != "https" and not session.portal_url.startswith("http://"):
        return False
    for entry in session.hosts | _EXTRA_HOSTS:
        if entry.startswith("."):
            if host.endswith(entry) or host == entry[1:]:
                return True
        elif host == entry:
            return True
    return False


def _resolve_portal(portal: str | None) -> str:
    portal = (portal or os.environ.get(PORTAL_ENV, "")).strip()
    if not portal:
        raise ValueError(f"No portal: pass portal= or set {PORTAL_ENV}.")
    if "://" not in portal:
        from .portal import PORTALS  # lazy: portal imports this module

        if portal.lower() not in PORTALS:
            raise ValueError(f"Unknown portal {portal!r}; pass a URL or one of {sorted(PORTALS)}.")
        portal = PORTALS[portal.lower()]
    portal = portal.rstrip("/")
    for suffix in ("/sharing/rest", "/sharing", "/home"):
        if portal.lower().endswith(suffix):
            portal = portal[: -len(suffix)]
    return portal


def _is_agol(portal_url: str) -> bool:
    host = (urllib.parse.urlsplit(portal_url).hostname or "").lower()
    return host == "arcgis.com" or host.endswith(".arcgis.com")


def _default_hosts(portal_url: str) -> frozenset[str]:
    hosts = {(urllib.parse.urlsplit(portal_url).hostname or "").lower()}
    if _is_agol(portal_url):
        hosts.add(".arcgis.com")    # hosted services, tiles, org URLs
    return frozenset(h for h in hosts if h)


def _hosts_for(portal_url: str, token: str) -> frozenset[str]:
    """Default hosts plus the portal's federated servers (best effort)."""
    hosts = set(_default_hosts(portal_url))
    if _is_agol(portal_url):
        return frozenset(hosts)
    try:
        data = fetch_json(f"{portal_url}/sharing/rest/portals/self/servers",
                          {"f": "json", "token": token}, timeout=15)
    except (RuntimeError, ValueError, OSError):
        return frozenset(hosts)
    for server in (data.get("servers") or []) if isinstance(data, dict) else []:
        for key in ("url", "adminUrl"):
            host = urllib.parse.urlsplit(str(server.get(key) or "")).hostname
            if host:
                hosts.add(host.lower())
    return frozenset(hosts)


def _as_epoch(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.timestamp()
    value = float(value)
    return value / 1000 if value > 1e11 else value   # Esri often reports ms


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


def _pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256 code challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _token_request(portal_url: str, form: dict[str, str]) -> dict[str, Any]:
    # token="" keeps _http from attaching the current session to its own renewal.
    data = post_json(f"{portal_url}/sharing/rest/oauth2/token", {**form, "f": "json", "token": ""})
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected token response: {type(data).__name__}")
    if data.get("error"):
        err = data["error"]
        raise RuntimeError(format_esri_error(err) if isinstance(err, dict) else str(err))
    if not data.get("access_token"):
        raise RuntimeError("Token response had no access_token.")
    return data


def _oauth_session(portal_url: str, client_id: str, data: dict[str, Any]) -> _Session:
    now = time.time()
    refresh_in = data.get("refresh_token_expires_in")
    return _Session(
        portal_url=portal_url,
        token=data["access_token"],
        expires=now + float(data.get("expires_in") or _DEFAULT_EXPIRES_IN),
        hosts=_hosts_for(portal_url, data["access_token"]),
        source="oauth",
        username=data.get("username"),
        oauth={
            "client_id": client_id,
            "refresh_token": data.get("refresh_token"),
            "refresh_expires": now + float(refresh_in) if refresh_in else None,
        },
    )


def _oauth_refresh(session: _Session) -> tuple[str, float]:
    oauth = session.oauth or {}
    if not oauth.get("refresh_token"):
        raise RuntimeError("no refresh token")
    data = _token_request(session.portal_url, {
        "client_id": oauth["client_id"],
        "grant_type": "refresh_token",
        "refresh_token": oauth["refresh_token"],
    })
    if data.get("username"):
        session.username = data["username"]
    return data["access_token"], time.time() + float(data.get("expires_in") or _DEFAULT_EXPIRES_IN)


def _save(session: _Session) -> None:
    oauth = session.oauth or {}
    payload = {
        "version": 1,
        "portal": session.portal_url,
        "client_id": oauth.get("client_id"),
        "refresh_token": oauth.get("refresh_token"),
        "refresh_expires": oauth.get("refresh_expires"),
        "username": session.username,
        "hosts": sorted(session.hosts),
    }
    CREDENTIALS_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(CREDENTIALS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    try:
        os.chmod(CREDENTIALS_PATH, 0o600)   # O_CREAT's mode is ignored if the file existed
    except OSError:
        pass


class _CallbackServer(http.server.HTTPServer):
    """One-shot local server that captures the OAuth redirect's query."""

    result: dict[str, str] | None = None

    def wait(self, timeout: float) -> dict[str, str]:
        deadline = time.monotonic() + timeout
        self.timeout = 1
        while self.result is None:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Sign-in timed out after {timeout:.0f} s.")
            self.handle_request()
        return self.result


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        query = dict(urllib.parse.parse_qsl(parsed.query))
        self.server.result = query  # type: ignore[attr-defined]
        if query.get("code"):
            message = "Signed in. You can close this tab."
        else:
            message = "Sign-in failed: " + (query.get("error_description") or query.get("error")
                                           or "no code returned")
        body = f"<!doctype html><title>georest</title><p>{html.escape(message)}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass   # keep the terminal quiet; the query holds the auth code
