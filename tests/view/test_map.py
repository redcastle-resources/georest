"""georest's Map: geeViz's method names, a map document, a local view and an
exported page. No network: the server runs locally and nothing is fetched."""
import json
import re
import urllib.request

import pytest

import georest
from georest import view
from georest.view import _server
from georest.view._document import validate
from georest.view._map import mapper

NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
LCMS = "https://imagery.geoplatform.gov/iipp/rest/services/Vegetation/USFS_EDW_LCMS_AnnualChange_CONUS/ImageServer"
FS = "https://services.arcgis.com/x/arcgis/rest/services/fires/FeatureServer/0"
SQUARE = {"type": "Feature", "properties": {"n": 1},
          "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}


def layers(m):
    return [(l["type"], l["name"]) for l in m.document()["layers"]]


def test_the_module_has_a_map_like_geeviz():
    assert isinstance(view.Map, mapper)
    assert isinstance(view.mapper(), mapper) and view.mapper() is not view.Map
    assert georest.view is view


def test_each_add_method_makes_its_layer():
    m = mapper()
    m.addLayer(SQUARE, {"strokeColor": "ff0000"}, "Square")
    m.addLayer([SQUARE, SQUARE], name="Two")
    m.addLayer(FS, name="Fires")
    m.addTileLayer("https://t/{z}/{x}/{y}.png", name="Tiles", max_zoom=15, legend={"Forest": "#00ff00"})
    m.addDynamicMapService(NFHL, name="Flood", layers="show:28")
    m.addEsriImageService(LCMS, name="LCMS 2021", time=2021)
    m.addEsriMapService(NFHL, name="Flood 2")
    m.addEsriFeatureService(FS, name="Big fires", where="acres > 1000", max_features=500)
    m.addEsriService(LCMS, name="Auto")
    m.addTimeLapse(LCMS, {"canAreaChart": True}, "Change")
    m.addAreaChartLayer(LCMS, name="Chart only")
    doc = validate(m.document())
    assert layers(m) == [
        ("geojson", "Square"), ("geojson", "Two"), ("esri.feature", "Fires"), ("xyz", "Tiles"),
        ("esri.map", "Flood"), ("esri.image", "LCMS 2021"), ("esri.map", "Flood 2"), ("esri.feature", "Big fires"),
        ("esri.image", "Auto"), ("esri.timelapse", "Change"), ("esri.image", "Chart only"),
    ]
    L = doc["layers"]
    assert L[0]["viz"]["strokeColor"] == "ff0000"
    assert L[1]["source"]["geojson"]["type"] == "FeatureCollection" and len(L[1]["source"]["geojson"]["features"]) == 2
    assert L[3]["source"] == {"url": "https://t/{z}/{x}/{y}.png", "maxZoom": 15}
    assert L[3]["viz"]["classLegendDict"] == {"Forest": "00ff00"}
    assert L[4]["source"]["options"]["layers"] == "show:28"
    assert L[5]["source"]["options"]["time"] == [1609459200000, 1640995199999]
    assert L[7]["source"]["options"]["where"] == "acres > 1000" and L[7]["source"]["options"]["maxFeatures"] == 500
    assert L[9]["viz"]["canAreaChart"] is True
    assert L[10]["viz"]["chartOnly"] is True and L[10]["viz"]["canAreaChart"] is True


def test_a_table_with_geometry_and_a_geo_interface():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame([{"name": "a", "geometry": SQUARE["geometry"]}])
    m = mapper()
    m.addLayer(df, name="Table")

    class Geo:
        __geo_interface__ = {"type": "FeatureCollection", "features": [SQUARE]}

    m.addLayer(Geo(), name="Geo")
    gj = m.document()["layers"][0]["source"]["geojson"]
    assert gj["features"][0]["properties"] == {"name": "a"}
    assert m.document()["layers"][1]["source"]["geojson"]["features"][0]["properties"] == {"n": 1}


def test_view_and_commands():
    m = mapper()
    m.setCenter(-105, 40, 9)
    m.setTitle("Fires")
    m.turnOnInspector()
    m.setQueryPrecision(2, 0.5)
    m.setQueryToSidePane()
    m.setQueryBoxColor("ff0000")
    m.turnOnUserDefinedAreaCharting()
    m.setCanReorderLayers(False)
    doc = validate(m.document())
    assert doc["view"] == {"center": [-105, 40], "zoom": 9}
    assert doc["title"] == "Fires"
    assert ["turnOnInspector"] in doc["commands"]
    assert ["setQueryPrecision", 2, 0.5] in doc["commands"]
    assert ["setGlobal", "queryWindowMode", "sidePane"] in doc["commands"]
    assert ["setProperty", "canReorderLayers", False] in doc["commands"]
    m.centerObject([-106, 39, -104, 41])
    assert validate(m.document())["view"] == {"bounds": [-106, 39, -104, 41]}
    m.clearMap()
    assert m.document()["layers"] == [] and m.document()["commands"] == [] and m.document()["view"] is None


@pytest.mark.parametrize("call", [
    lambda m: m.addWindLayer(None), lambda m: m.addWindTimeLapse(None),
    lambda m: m.setQueryCRS("EPSG:4326"), lambda m: m.setQueryScale(30), lambda m: m.setQueryTransform([30, 0, 0, 0, -30, 0]),
])
def test_earth_engine_methods_point_to_geeviz(call):
    with pytest.raises(NotImplementedError, match="geeViz"):
        call(mapper())


def test_center_object_on_an_earth_engine_object_points_to_geeviz():
    class Fake:
        def getInfo(self):
            return {}
    with pytest.raises(NotImplementedError, match="geeViz"):
        mapper().centerObject(Fake())


def test_select_layers_and_lists_say_not_yet():
    with pytest.raises(NotImplementedError, match="not supported yet"):
        mapper().addSelectLayer(SQUARE)
    with pytest.raises(NotImplementedError, match="time-enabled ArcGIS service"):
        mapper().addTimeLapse([SQUARE])
    with pytest.raises(ValueError, match="feature layer"):
        mapper().addTimeLapse(FS)


def test_a_token_never_reaches_the_document_or_the_page():
    m = mapper()
    m.addEsriFeatureService(FS, name="Private", token="SECRET-TOKEN")
    doc = m.document()
    key = doc["layers"][0]["viz"]["esri"]["secured"]
    assert key and "SECRET" not in key
    assert "SECRET-TOKEN" not in json.dumps(doc)
    assert "SECRET-TOKEN" not in m.export_html()
    assert _server.server().tokens[key] == ("https://services.arcgis.com/x/arcgis/rest/services/fires/FeatureServer", "SECRET-TOKEN")


def test_export_html_inlines_the_document_with_assets_from_the_cdn(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    m = mapper()
    m.addLayer({"type": "Feature", "properties": {"n": "</script><script>alert(1)</script>"}, "geometry": None}, name="Bad")
    html = m.export_html()
    base = f"https://cdn.jsdelivr.net/gh/redcastle-resources/georest@v{georest.__version__}/src/georest/view/static/"
    assert f'<base href="{base}">' in html
    assert "./js/main.js" in html
    inline = re.search(r'<script type="application/json" id="geoview-document">(.*?)</script>', html, re.S).group(1)
    assert json.loads(inline)["layers"][0]["source"]["geojson"]["properties"]["n"] == "</script><script>alert(1)</script>"
    from georest.view._page import page_template
    # the document adds no </script>: the element ends where the page's did
    assert html.count("</script>") == page_template().count("</script>")
    assert json.loads(inline)["proxy"] is None
    assert "key=AIza" in html


def test_the_maps_key_can_be_replaced(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "AIzaFROMENV")
    m = mapper()
    assert "key=AIzaFROMENV" in m.export_html()
    m.setMapsKey("AIzaFROMCALL")
    html = m.export_html()
    assert "key=AIzaFROMCALL" in html and "AIzaFROMENV" not in html


def test_export_html_writes_a_file_and_takes_an_asset_base(tmp_path):
    m = mapper()
    m.addLayer(SQUARE, name="Square")
    out = tmp_path / "map.html"
    html = m.export_html(str(out), asset_base="/static/geoview")
    assert out.read_text(encoding="utf-8") == html
    assert '<base href="/static/geoview/">' in html


def test_view_serves_each_map_on_one_server(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "AIzaVIEWKEY")
    a, b = mapper(), mapper()
    a.addLayer(SQUARE, name="A")
    b.addLayer(SQUARE, name="B")
    ua = a.view(open_browser=False, open_iframe=False)
    ub = b.view(open_browser=False, open_iframe=False)
    assert ua.split("/geoview/")[0] == ub.split("/geoview/")[0]
    for url, name in ((ua, "A"), (ub, "B")):
        base, doc = url.split("?doc=")
        with urllib.request.urlopen(base.rsplit("/geoview/", 1)[0] + doc, timeout=10) as r:
            d = json.loads(r.read())
        assert d["layers"][0]["name"] == name and d["proxy"] == "/proxy"
    with urllib.request.urlopen(ua, timeout=10) as r:
        assert b"key=AIzaVIEWKEY" in r.read()


def test_a_notebook_map_is_a_self_contained_page(monkeypatch):
    """Spec (user decision 2026-11-24): in a notebook the map is a page with
    the document inline and geoView's files from jsDelivr, so it shows on
    hosted notebooks and in saved output; no local server."""
    from georest.view import _map, _server
    shown = []
    monkeypatch.setattr(_map, "_in_notebook", lambda: True)
    monkeypatch.setattr(_map, "_display_html", shown.append)
    monkeypatch.setattr(_server, "server", lambda: pytest.fail("a notebook map must not need the local server"))
    m = mapper()
    m.addLayer({"type": "Feature", "properties": {"n": "</script>"}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
               {}, "Pt")
    assert m.view() is None
    (frame,) = shown
    assert frame.startswith("<iframe srcdoc=\"") and "cdn.jsdelivr.net/gh/redcastle-resources/georest@v" in frame
    assert "<script" not in frame  # the page is escaped into the attribute


def test_a_notebook_map_with_a_secured_layer_uses_the_local_server(monkeypatch):
    from georest.view import _map
    shown = []
    monkeypatch.setattr(_map, "_in_notebook", lambda: True)
    monkeypatch.setattr(_map, "_display_frame", lambda url, h: shown.append(url))
    m = mapper()
    m.addEsriMapService("https://example.com/arcgis/rest/services/a/MapServer", token="T")
    url = m.view()
    assert shown == [url] and url.startswith("http://127.0.0.1:")


def test_a_script_keeps_serving_its_map_after_it_ends(monkeypatch):
    """python mymap.py: the server is a daemon thread, so without this the
    page would find nothing once the script returns."""
    from georest.view import _map
    registered = []
    monkeypatch.setattr(_map, "_in_notebook", lambda: False)
    monkeypatch.setattr(_map, "_interactive", lambda: False)
    monkeypatch.setattr(_map.webbrowser, "open", lambda u: None)
    monkeypatch.setattr(_map.atexit, "register", lambda f, *a: registered.append(f))
    monkeypatch.setattr(_map, "_KEEP_SERVING", [False])
    mapper().view()
    mapper().view()
    assert registered == [_map._keep_serving]
    # at a Python prompt nothing waits
    registered.clear()
    monkeypatch.setattr(_map, "_interactive", lambda: True)
    monkeypatch.setattr(_map, "_KEEP_SERVING", [False])
    mapper().view()
    assert registered == []
