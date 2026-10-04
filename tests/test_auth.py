"""Offline tests for `georest.restesri.auth` and its hooks in `_http`.

`urllib.request.urlopen` is replaced by a `FakeNet` that answers by URL and
records every request, so nothing here touches the network. The OAuth tests
do run the real localhost callback server; a fake "browser" thread follows the
redirect with http.client, which the urlopen patch does not affect.
"""
from __future__ import annotations

import http.client
import io
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import warnings
from pathlib import Path
from unittest import mock

from georest.restesri import _http, auth, portal

AGOL = "https://myorg.maps.arcgis.com"
ENTERPRISE = "https://gis.agency.gov/portal"


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "application/json"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNet:
    """Stand-in for urlopen. `routes` maps a URL substring to a response:
    a dict (JSON body), an int (HTTP error status), or a callable taking the
    request's params and returning either."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests: list[dict] = []

    def __call__(self, req, timeout=None):
        parts = urllib.parse.urlsplit(req.full_url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        form = dict(urllib.parse.parse_qsl(req.data.decode())) if req.data else {}
        params = {**query, **form}
        self.requests.append({"url": req.full_url, "params": params,
                              "headers": dict(req.header_items())})
        for pattern, answer in self.routes.items():
            if pattern in req.full_url:
                if callable(answer):
                    answer = answer(params)
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(req.full_url, answer, "error", {}, None)
                return _Resp(json.dumps(answer).encode())
        return _Resp(b'{"ok": true}')

    def tokens(self) -> list:
        return [r["params"].get("token") for r in self.requests]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AuthTestCase(unittest.TestCase):
    """Isolates module state and the credentials file for every test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cred_path = Path(self._tmp.name) / "creds.json"
        patches = [
            mock.patch.object(auth, "CREDENTIALS_PATH", self.cred_path),
            mock.patch.object(auth, "_SESSION", None),
            mock.patch.object(auth, "_RESTORE_TRIED", True),
            mock.patch.object(auth, "_EXTRA_HOSTS", set()),
            mock.patch.dict("os.environ", {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for name in (auth.TOKEN_ENV, auth.PORTAL_ENV, auth.CLIENT_ID_ENV):
            import os
            os.environ.pop(name, None)
        self.addCleanup(self._tmp.cleanup)
        portal._ORG_ID_CACHE.clear()

    def net(self, routes=None) -> FakeNet:
        fake = FakeNet(routes)
        p = mock.patch("urllib.request.urlopen", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake


class TestPkce(unittest.TestCase):
    def test_rfc7636_vector(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(auth._pkce_challenge(verifier),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


class TestHostScoping(AuthTestCase):
    def test_token_goes_to_agol_hosts_only(self):
        net = self.net()
        auth.set_token("SECRET", portal=AGOL)
        _http.fetch_json("https://services3.arcgis.com/x/FeatureServer/0", {"f": "json"})
        _http.fetch_json(f"{AGOL}/sharing/rest/portals/self", {"f": "json"})
        _http.fetch_json("https://coastalatlas.noaa.gov/arcgis/rest/services", {"f": "json"})
        _http.fetch_json("https://evilarcgis.com/x", {"f": "json"})
        self.assertEqual(net.tokens(), ["SECRET", "SECRET", None, None])

    def test_no_token_over_plain_http(self):
        net = self.net()
        auth.set_token("SECRET", portal=AGOL)
        _http.fetch_json("http://services3.arcgis.com/x", {"f": "json"})
        self.assertEqual(net.tokens(), [None])

    def test_enterprise_federated_servers_are_trusted(self):
        net = self.net({"portals/self/servers": {"servers": [
            {"url": "https://maps.agency.gov/server", "adminUrl": "https://gis-admin.agency.gov:6443/arcgis"}]}})
        auth.set_token("SECRET", portal=ENTERPRISE)
        _http.fetch_json("https://maps.agency.gov/server/rest/services", {"f": "json"})
        _http.fetch_json("https://gis.agency.gov/portal/sharing/rest/search", {"f": "json"})
        _http.fetch_json("https://other.agency.gov/server/rest/services", {"f": "json"})
        self.assertEqual(net.tokens()[1:], ["SECRET", "SECRET", None])

    def test_trust_host(self):
        net = self.net({"portals/self/servers": {"servers": []}})
        auth.set_token("SECRET", portal=ENTERPRISE)
        auth.trust_host(".imagery.agency.gov")
        _http.fetch_json("https://a.imagery.agency.gov/arcgis/rest", {"f": "json"})
        self.assertEqual(net.tokens()[-1], "SECRET")

    def test_explicit_token_wins_and_empty_forces_anonymous(self):
        net = self.net()
        auth.set_token("SECRET", portal=AGOL)
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json", "token": "MINE"})
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json", "token": ""})
        self.assertEqual(net.tokens(), ["MINE", None])

    def test_services_call_uses_session(self):
        net = self.net({"FeatureServer/0": {"name": "layer", "type": "Feature Layer"}})
        auth.set_token("SECRET", portal=AGOL)
        from georest.restesri import services
        services.getLayerInfo("https://services.arcgis.com/abc/arcgis/rest/services/x/FeatureServer/0")
        self.assertEqual(net.tokens(), ["SECRET"])

    def test_env_token_needs_portal(self):
        import os
        net = self.net()
        os.environ[auth.TOKEN_ENV] = "ENVTOK"
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        os.environ[auth.PORTAL_ENV] = AGOL
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertEqual(net.tokens(), [None, "ENVTOK"])
        self.assertEqual(auth.status()["source"], "env")


class TestTokenLifecycle(AuthTestCase):
    def test_referer_header_sent(self):
        net = self.net()
        auth.set_token("SECRET", portal=AGOL, referer="https://pro.example")
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertEqual(net.requests[0]["headers"].get("Referer"), "https://pro.example")

    def test_498_body_refreshes_once_and_retries(self):
        net = self.net({"services.arcgis.com": lambda p: (
            {"error": {"code": 498, "message": "Invalid token."}}
            if p.get("token") == "OLD" else {"ok": p.get("token")})})
        refresh = mock.Mock(return_value="NEW")
        auth.set_token("OLD", portal=AGOL, refresh=refresh)
        self.assertEqual(_http.fetch_json("https://services.arcgis.com/x", {"f": "json"}), {"ok": "NEW"})
        self.assertEqual(net.tokens(), ["OLD", "NEW"])
        refresh.assert_called_once()

    def test_498_retries_only_once(self):
        net = self.net({"services.arcgis.com": {"error": {"code": 498, "message": "Invalid token."}}})
        auth.set_token("OLD", portal=AGOL, refresh=lambda: "STILLBAD")
        data = _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertEqual(data["error"]["code"], 498)
        self.assertEqual(len(net.requests), 2)

    def test_http_498_status_refreshes(self):
        net = self.net({"services.arcgis.com": lambda p: 498 if p.get("token") == "OLD" else {"ok": 1}})
        auth.set_token("OLD", portal=AGOL, refresh=lambda: ("NEW", time.time() + 3600))
        _http.fetch_bytes("https://services.arcgis.com/x/exportImage", {"f": "image"})
        self.assertEqual(net.tokens(), ["OLD", "NEW"])

    def test_no_retry_for_explicit_token(self):
        net = self.net({"services.arcgis.com": {"error": {"code": 498}}})
        auth.set_token("OLD", portal=AGOL, refresh=mock.Mock(return_value="NEW"))
        _http.fetch_json("https://services.arcgis.com/x", {"token": "MINE"})
        self.assertEqual(len(net.requests), 1)

    def test_refreshes_before_expiry(self):
        net = self.net()
        auth.set_token("OLD", portal=AGOL, expires=time.time() + 30,
                       refresh=lambda: ("NEW", time.time() + 3600))
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertEqual(net.tokens(), ["NEW"])

    def test_expired_token_without_refresh_is_not_sent(self):
        net = self.net()
        auth.set_token("OLD", portal=AGOL, expires=time.time() - 10)
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertEqual(net.tokens(), [None])

    def test_failed_refresh_warns_and_goes_anonymous(self):
        net = self.net()
        def boom():
            raise RuntimeError("nope")
        auth.set_token("OLD", portal=AGOL, expires=time.time() - 10, refresh=boom)
        with self.assertWarns(UserWarning):
            _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertEqual(net.tokens(), [None])
        self.assertIsNone(auth.status())

    def test_expires_in_milliseconds(self):
        self.assertAlmostEqual(auth._as_epoch(1_700_000_000_000), 1_700_000_000)
        self.assertEqual(auth._as_epoch(1_700_000_000), 1_700_000_000)

    def test_status_never_contains_token(self):
        auth.set_token("SUPERSECRET", portal=AGOL)
        self.assertNotIn("SUPERSECRET", json.dumps(auth.status()))

    def test_logout(self):
        auth.set_token("SECRET", portal=AGOL)
        self.cred_path.write_text("{}")
        auth.logout()
        self.assertIsNone(auth.status())
        self.assertFalse(self.cred_path.exists())


class TestRedaction(AuthTestCase):
    def test_http_error_message_hides_token(self):
        self.net({"services.arcgis.com": 500})
        auth.set_token("SECRET", portal=AGOL)
        with self.assertRaises(RuntimeError) as ctx:
            _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertIn("token=REDACTED", str(ctx.exception))

    def test_explicit_token_also_redacted(self):
        self.net({"services.arcgis.com": 404})
        with self.assertRaises(RuntimeError) as ctx:
            _http.fetch_text("https://services.arcgis.com/x", {"token": "MINE"})
        self.assertNotIn("MINE", str(ctx.exception))

    def test_498_error_hint(self):
        msg = _http.format_esri_error({"code": 498, "message": "Invalid token."})
        self.assertIn("georest-login", msg)
        self.assertNotIn("georest-login", _http.format_esri_error({"code": 400, "message": "x"}))


class TestOrgCacheKey(AuthTestCase):
    def test_session_identity_keys_the_cache(self):
        orgs = iter(["ORGA", "ORGB"])
        with mock.patch.object(portal, "fetch_json", side_effect=lambda u, p=None: {"id": next(orgs)}):
            auth.set_token("A", portal=AGOL)
            self.assertEqual(portal._resolve_org_id(AGOL), "ORGA")
            self.assertEqual(portal._resolve_org_id(AGOL), "ORGA")   # cached
            auth.set_token("B", portal=AGOL)
            self.assertEqual(portal._resolve_org_id(AGOL), "ORGB")


def _browser(authorize_url: str, *, state=None, code="THECODE"):
    """Follow the authorize URL's redirect the way the portal would."""
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(authorize_url).query))
    redirect = urllib.parse.urlsplit(query["redirect_uri"])

    def go():
        params = {"code": code, "state": state if state is not None else query["state"]}
        for _ in range(50):
            try:
                conn = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=5)
                conn.request("GET", redirect.path + "?" + urllib.parse.urlencode(params))
                conn.getresponse().read()
                conn.close()
                return
            except OSError:
                time.sleep(0.05)
    threading.Thread(target=go, daemon=True).start()
    return True


class TestOAuthLogin(AuthTestCase):
    def token_net(self):
        return self.net({"oauth2/token": lambda p: (
            {"access_token": "ACCESS1", "expires_in": 1800, "username": "jdoe",
             "refresh_token": "REFRESH", "refresh_token_expires_in": 1209600}
            if p["grant_type"] == "authorization_code"
            else {"access_token": "ACCESS2", "expires_in": 1800})})

    def login(self, **kw):
        with mock.patch("webbrowser.open", side_effect=lambda url: _browser(url, **kw)), \
             mock.patch("sys.stderr", io.StringIO()):
            return auth.login(AGOL, client_id="CID", port=_free_port(), timeout=10)

    def test_login_exchanges_code_with_verifier(self):
        net = self.token_net()
        info = self.login()
        self.assertEqual(info["username"], "jdoe")
        self.assertEqual(info["source"], "oauth")
        exchange = net.requests[0]["params"]
        self.assertEqual(exchange["grant_type"], "authorization_code")
        self.assertEqual(exchange["code"], "THECODE")
        self.assertNotIn("token", exchange)
        self.assertTrue(len(exchange["code_verifier"]) >= 43)
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        self.assertEqual(net.tokens()[-1], "ACCESS1")

    def test_state_mismatch_rejected(self):
        self.token_net()
        with self.assertRaises(RuntimeError):
            self.login(state="forged")

    def test_persist_and_restore(self):
        net = self.token_net()
        with mock.patch("webbrowser.open", side_effect=lambda url: _browser(url)), \
             mock.patch("sys.stderr", io.StringIO()):
            auth.login(AGOL, client_id="CID", port=_free_port(), timeout=10, persist=True)
        saved = json.loads(self.cred_path.read_text())
        self.assertEqual(saved["refresh_token"], "REFRESH")
        self.assertNotIn("ACCESS1", self.cred_path.read_text())

        auth._SESSION = None
        auth._RESTORE_TRIED = False          # a fresh process
        _http.fetch_json("https://services.arcgis.com/x", {"f": "json"})
        refresh = net.requests[-2]["params"]
        self.assertEqual(refresh["grant_type"], "refresh_token")
        self.assertEqual(refresh["refresh_token"], "REFRESH")
        self.assertEqual(net.tokens()[-1], "ACCESS2")

    def test_expired_saved_session_ignored(self):
        self.cred_path.write_text(json.dumps({
            "portal": AGOL, "client_id": "CID", "refresh_token": "R",
            "refresh_expires": time.time() - 1, "hosts": ["myorg.maps.arcgis.com"]}))
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            self.assertIsNone(auth.restore())


if __name__ == "__main__":
    unittest.main()
