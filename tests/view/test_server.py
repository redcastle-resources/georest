"""geoView's local server: the viewer's files, map documents, and a proxy for
ArcGIS services that holds tokens and refuses anything but public ArcGIS
REST URLs. No network: name resolution and the outbound opener are faked."""
import json
import socket
import urllib.request

import pytest

from georest.view import _server
from georest.view._document import add_layer, new_document
from georest.view._server import ProxyRefused, ViewServer, check_proxy_url, forward

FS = "https://services.arcgis.com/x/arcgis/rest/services/a/FeatureServer/0/query"


def resolver(*ips):
    def resolve(host, port, *a, **k):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    return resolve


PUBLIC = resolver("52.0.0.1")


@pytest.mark.parametrize("url, why", [
    ("ftp://x/rest/services/a", "http"),
    ("https://user:pw@services.arcgis.com/x/arcgis/rest/services/a", "user"),
    ("https://services.arcgis.com/admin", "ArcGIS REST"),
    ("https:///rest/services/a", "host"),
])
def test_refused_by_shape(url, why):
    with pytest.raises(ProxyRefused, match=why):
        check_proxy_url(url, resolve=PUBLIC)


@pytest.mark.parametrize("ips", [("127.0.0.1",), ("10.1.2.3",), ("169.254.169.254",), ("::1",), ("fd00::1",), ("52.0.0.1", "192.168.1.5")])
def test_refused_for_private_addresses(ips):
    with pytest.raises(ProxyRefused, match="public"):
        check_proxy_url(FS, resolve=resolver(*ips))


def test_public_arcgis_urls_pass():
    check_proxy_url(FS, resolve=PUBLIC)
    check_proxy_url("https://www.arcgis.com/sharing/rest/search?q=x", resolve=PUBLIC)


class FakeOpener:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, req, timeout=None):
        self.asked.append(req)
        return self.answers.pop(0)


def test_a_redirect_to_a_private_address_is_not_followed():
    def resolve(host, port, *a, **k):
        return resolver("127.0.0.1" if host == "inside.example" else "52.0.0.1")(host, port)
    op = FakeOpener([(302, {"Location": "http://inside.example/rest/services/x"}, b"")])
    with pytest.raises(ProxyRefused, match="public"):
        forward(FS, "GET", None, {}, opener=op, resolve=resolve)
    assert len(op.asked) == 1


def test_the_token_goes_to_the_service_and_cookies_do_not_come_back():
    op = FakeOpener([(200, {"Content-Type": "application/json", "Set-Cookie": "a=b"}, b'{"ok":1}')])
    status, headers, body = forward(FS + "?f=json", "GET", None, {}, token="SECRET", opener=op, resolve=PUBLIC)
    assert status == 200 and body == b'{"ok":1}'
    assert "token=SECRET" in op.asked[0].full_url
    assert "Set-Cookie" not in headers
    assert headers["Content-Type"] == "application/json"
    op = FakeOpener([(200, {"Content-Type": "application/json"}, b"{}")])
    forward(FS, "POST", b"f=json&where=1%3D1", {"Content-Type": "application/x-www-form-urlencoded"}, token="SECRET", opener=op, resolve=PUBLIC)
    assert b"token=SECRET" in op.asked[0].data and "token" not in op.asked[0].full_url


@pytest.fixture
def srv():
    s = ViewServer()
    s.start(port=0)
    yield s
    s.stop()


def get(url, data=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_serves_the_viewer(srv):
    status, headers, body = get(srv.base + "/geoview/geoview.html")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b"geoview-document" in body
    status, headers, _ = get(srv.base + "/geoview/js/main.js")
    assert status == 200 and "javascript" in headers["Content-Type"]
    assert headers["Cache-Control"] == "no-store"


def test_serves_a_document_and_nothing_outside_the_viewer(srv):
    doc = new_document("x")
    add_layer(doc, "xyz", {"url": "https://t/{z}/{x}/{y}.png"})
    url = srv.url_for(doc)
    assert url.startswith(srv.base + "/geoview/geoview.html?doc=/maps/")
    status, _, body = get(srv.base + url.split("doc=")[1])
    assert status == 200 and json.loads(body)["title"] == "x"
    assert get(srv.base + "/maps/nope.json")[0] == 404
    for path in ("/geoview/../_server.py", "/geoview/%2e%2e/_server.py", "/geoview/..%5c_server.py", "/_server.py"):
        assert get(srv.base + path)[0] == 404, path


def test_the_proxy_refuses_a_private_target(srv):
    status, _, body = get(srv.base + "/proxy?url=" + urllib.request.quote("http://127.0.0.1:1/rest/services/x", safe=""))
    assert status == 403 and b"public" in body


def test_the_proxy_adds_a_secured_layers_token(srv, monkeypatch):
    seen = []

    def fake_forward(url, method, body, headers, token=None, **k):
        seen.append((url, method, token))
        return 200, {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}, b"{}"

    monkeypatch.setattr(_server, "forward", fake_forward)
    srv.secure("k1", "SECRET")
    status, _, _ = get(srv.base + "/proxy?layer=k1&url=" + urllib.request.quote(FS, safe=""))
    assert status == 200 and seen[-1] == (FS, "GET", "SECRET")
    get(srv.base + "/proxy?url=" + urllib.request.quote(FS, safe=""), data=b"f=json")
    assert seen[-1] == (FS, "POST", None)


def test_one_server_per_process_and_a_taken_port_moves_on():
    a = _server.server()
    assert _server.server() is a
    b = ViewServer()
    b.start(port=a.port)
    try:
        assert b.port != a.port
    finally:
        b.stop()
