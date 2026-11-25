"""geoView's local server: the viewer's files, the map documents Map.view()
opens, and a proxy for ArcGIS REST services.

The proxy is for services that do not allow cross-site requests and for
secured layers: their token stays here, in this process, and is added to
the forwarded request, so it never reaches the page. It forwards only to
public addresses (every address the host resolves to, and every redirect,
is checked) and only to ArcGIS REST paths.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
from __future__ import annotations

import http.server
import ipaddress
import mimetypes
import os
import secrets
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import STATIC_DIR
from ._document import to_json

DEFAULT_PORT = 8765
_REST_PATHS = ("/rest/services/", "/sharing/rest/")
_MAX_REDIRECTS = 3
_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
          ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml"}


class ProxyRefused(Exception):
    """A URL the proxy will not forward to; the message says why."""


def check_proxy_url(url: str, resolve=socket.getaddrinfo) -> None:
    """Raise ProxyRefused unless ``url`` is an http(s) ArcGIS REST URL on a
    host whose every address is public."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise ProxyRefused("only http and https URLs are forwarded")
    if p.username or p.password:
        raise ProxyRefused("URLs with user info are not forwarded")
    if not p.hostname:
        raise ProxyRefused("the URL has no host")
    if not any(s in p.path.lower() for s in _REST_PATHS):
        raise ProxyRefused("only ArcGIS REST paths (/rest/services/ or /sharing/rest/) are forwarded")
    try:
        infos = resolve(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        raise ProxyRefused(f"{p.hostname} could not be found") from None
    if not infos:
        raise ProxyRefused(f"{p.hostname} could not be found")
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not ip.is_global:
            raise ProxyRefused(f"{p.hostname} is not a public address")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req, timeout=60):
    """(status, headers, body) for a request, redirects not followed."""
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()


def forward(url: str, method: str, body: bytes | None, headers: dict, token: str | None = None,
            opener=None, resolve=socket.getaddrinfo):
    """Forward a request to an ArcGIS service: (status, headers, body).

    ``token`` (a secured layer's) is added as a query parameter, or a form
    field for a POST. Redirects are followed here, at most three, each
    checked like the first URL."""
    opener = opener or _open
    if token:
        if method == "POST":
            body = (body or b"") + (b"&" if body else b"") + urllib.parse.urlencode({"token": token}).encode()
        else:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"token": token})
    send = {k: v for k, v in (headers or {}).items() if k.lower() in ("content-type", "accept")}
    for _ in range(_MAX_REDIRECTS + 1):
        check_proxy_url(url, resolve=resolve)
        req = urllib.request.Request(url, data=body if method == "POST" else None, method=method, headers=send)
        req.add_header("User-Agent", "georest-geoview")
        status, got, data = opener(req, timeout=60)
        if status in (301, 302, 303, 307, 308) and got.get("Location"):
            url = urllib.parse.urljoin(url, got["Location"])
            if status == 303:
                method, body = "GET", None
            continue
        out = {"Access-Control-Allow-Origin": "*"}
        ctype = got.get("Content-Type") or got.get("content-type")
        if ctype:
            out["Content-Type"] = ctype
        return status, out, data
    raise ProxyRefused("too many redirects")


class _HTTPServer(http.server.ThreadingHTTPServer):
    # Never share a port: on Windows SO_REUSEADDR lets a second server bind a
    # port already in use, so ask for the port exclusively instead.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ViewServer:
    """A local server for geoView pages: start(), url_for(doc), secure(), stop()."""

    def __init__(self):
        self.docs = {}
        self.tokens = {}
        self.port = None
        self.base = None
        self._httpd = None

    def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        handler = _handler_for(self)
        try:
            self._httpd = _HTTPServer((host, port), handler)
        except OSError:
            self._httpd = _HTTPServer((host, 0), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.base = f"http://{host}:{self.port}"
        threading.Thread(target=self._httpd.serve_forever, name="geoview-server", daemon=True).start()
        return self.base

    def url_for(self, doc: dict) -> str:
        """Keep ``doc`` and return the page URL that shows it."""
        key = secrets.token_hex(8)
        self.docs[key] = to_json(doc).encode("utf-8")
        return f"{self.base}/geoview/geoview.html?doc=/maps/{key}.json"

    def secure(self, layer_key: str, token: str) -> None:
        """Keep a secured layer's token here; the page asks /proxy?layer=<key>."""
        self.tokens[layer_key] = token

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def _static_path(rel: str):
    """A file under STATIC_DIR for a URL path, or None (never outside it)."""
    rel = urllib.parse.unquote(rel).replace("\\", "/")
    root = os.path.realpath(STATIC_DIR)
    full = os.path.realpath(os.path.join(root, rel.lstrip("/")))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full if os.path.isfile(full) else None


def _handler_for(server: ViewServer):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body=b"", ctype="text/plain; charset=utf-8", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                if k.lower() not in ("content-type", "content-length"):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self, method):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            url = (q.get("url") or [""])[0]
            token = server.tokens.get((q.get("layer") or [""])[0])
            body = None
            if method == "POST":
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                status, headers, data = forward(url, method, body, dict(self.headers.items()), token=token)
            except ProxyRefused as e:
                return self._send(403, str(e).encode())
            except (OSError, ValueError) as e:
                return self._send(502, f"The service could not be reached: {e}".encode())
            self._send(status, data, headers.get("Content-Type", "application/octet-stream"), headers)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/proxy":
                return self._proxy("GET")
            if path.startswith("/maps/") and path.endswith(".json"):
                doc = server.docs.get(path[len("/maps/"):-len(".json")])
                return self._send(200, doc, "application/json") if doc else self._send(404, b"no such map")
            if path.startswith("/geoview/"):
                full = _static_path(path[len("/geoview/"):])
                if full:
                    with open(full, "rb") as f:
                        data = f.read()
                    ext = os.path.splitext(full)[1].lower()
                    return self._send(200, data, _TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream")
            self._send(404, b"not found")

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path == "/proxy":
                return self._proxy("POST")
            self._send(404, b"not found")

    return Handler


_SERVER = None
_LOCK = threading.Lock()


def server() -> ViewServer:
    """This process's geoView server, started on first use (port 8765, or a
    free one when that is taken)."""
    global _SERVER
    with _LOCK:
        if _SERVER is None:
            s = ViewServer()
            s.start(port=int(os.environ.get("GEOVIEW_PORT", DEFAULT_PORT)))
            _SERVER = s
        return _SERVER
