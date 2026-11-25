"""Map.addLayer takes anything: what each kind of first argument becomes, and
a reducer for ArcGIS image services. No network."""
import json

import pytest

from georest.view._document import validate
from georest.view._inputs import classify, mosaic_rule
from georest.view._map import mapper

NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
LANDSAT = "https://landsat2.arcgis.com/arcgis/rest/services/Landsat/MS/ImageServer"
FS = "https://services.arcgis.com/x/arcgis/rest/services/fires/FeatureServer/0"
XYZ = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}"
GJ_URL = "https://example.com/data/parks.geojson"
POINT = {"type": "Feature", "properties": {"n": 1}, "geometry": {"type": "Point", "coordinates": [-105, 40]}}


@pytest.mark.parametrize("obj, kind, value", [
    (NFHL, "esri", NFHL),
    (FS + "/", "esri", FS),
    (LANDSAT, "esri", LANDSAT),
    # a tile template beats the ArcGIS rule: this one is a cached MapServer's tiles
    (XYZ, "xyz", XYZ),
    ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "xyz", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
    (GJ_URL, "geojson_url", GJ_URL),
    ("https://example.com/a.json?x=1", "geojson_url", "https://example.com/a.json?x=1"),
    ("https://example.com/query?where=1=1&f=geojson", "geojson_url", "https://example.com/query?where=1=1&f=geojson"),
    (json.dumps(POINT), "geojson", POINT),
    ("  [" + json.dumps(POINT) + "]", "geojson", [POINT]),
    ("USGS/SRTMGL1_003", "asset", "USGS/SRTMGL1_003"),
    ("projects/gtac-data-publish/assets/LCMS/x", "asset", "projects/gtac-data-publish/assets/LCMS/x"),
    ({"url": NFHL + "/", "title": "NFHL"}, "esri", NFHL),
    (POINT, None, POINT),
])
def test_what_a_first_argument_is(obj, kind, value):
    assert classify(obj) == (kind, value)


@pytest.mark.parametrize("bad", ["", "not a url or asset", "ftp://x/y", "{not json", "https://example.com/page.html"])
def test_an_unknown_string_names_what_is_accepted(bad):
    with pytest.raises(ValueError, match="ArcGIS service URL.*tile URL.*GeoJSON"):
        classify(bad)


class _Reducer:  # an ee.Reducer stand-in: georest never imports ee
    def __init__(self, fn):
        self.fn = fn

    def serialize(self):
        return json.dumps({"result": "0", "values": {"0": {"functionInvocationValue": {"functionName": self.fn, "arguments": {}}}}})


@pytest.mark.parametrize("reducer, op", [
    ("max", "MT_MAX"), ("MIN", "MT_MIN"), ("mean", "MT_MEAN"), ("first", "MT_FIRST"), ("last", "MT_LAST"),
    ("sum", "MT_SUM"), ("blend", "MT_BLEND"), (_Reducer("Reducer.max"), "MT_MAX"), (_Reducer("Reducer.mean"), "MT_MEAN"),
    (_Reducer("Reducer.firstNonNull"), "MT_FIRST"), (_Reducer("Reducer.lastNonNull"), "MT_LAST"),
])
def test_a_reducer_is_an_arcgis_mosaic_operation(reducer, op):
    assert mosaic_rule(reducer) == {"mosaicMethod": "esriMosaicNone", "mosaicOperation": op}


@pytest.mark.parametrize("reducer", ["median", _Reducer("Reducer.median"), _Reducer("Reducer.percentile")])
def test_a_reducer_arcgis_cannot_do_is_refused(reducer):
    with pytest.raises(ValueError, match="first, last, min, max, mean, sum, blend"):
        mosaic_rule(reducer)


def _doc(m):
    return validate(m.document())


def test_addLayer_takes_every_kind_of_input():
    m = mapper()
    m.addLayer(NFHL, {"layers": "show:28"}, "Flood")
    m.addLayer(FS, {"where": "acres > 100", "maxFeatures": 500}, "Fires")
    m.addLayer(LANDSAT, {"reducer": "max", "time": ["2020-07-01", "2020-09-30"]}, "Landsat max")
    m.addLayer(XYZ, {"classLegendDict": {"Water": "#0000ff"}, "maxZoom": 16}, "Hydro")
    m.addLayer(GJ_URL, {"strokeColor": "00aa00"}, "Parks")
    m.addLayer(json.dumps(POINT), {}, "Text point")
    m.addLayer(POINT, {}, "Dict point")
    d = _doc(m)
    assert [(l["type"], l["name"]) for l in d["layers"]] == [
        ("esri.map", "Flood"), ("esri.feature", "Fires"), ("esri.image", "Landsat max"), ("xyz", "Hydro"),
        ("geojson", "Parks"), ("geojson", "Text point"), ("geojson", "Dict point")]
    flood, fires, landsat, hydro, parks, text, _ = d["layers"]
    assert flood["source"]["options"]["layers"] == "show:28"
    assert fires["source"]["options"]["where"] == "acres > 100" and fires["source"]["options"]["maxFeatures"] == 500
    assert landsat["source"]["options"]["mosaicRule"] == {"mosaicMethod": "esriMosaicNone", "mosaicOperation": "MT_MAX"}
    assert len(landsat["source"]["options"]["time"]) == 2
    assert "reducer" not in landsat["viz"]
    assert hydro["source"] == {"url": XYZ, "maxZoom": 16}
    assert hydro["viz"]["classLegendDict"] == {"Water": "0000ff"}
    assert parks["source"] == {"url": GJ_URL}
    assert text["source"]["geojson"] == POINT


def test_an_explicit_mosaic_rule_wins_over_a_reducer():
    m = mapper()
    rule = {"mosaicMethod": "esriMosaicAttribute", "sortField": "AcquisitionDate"}
    m.addLayer(LANDSAT, {"reducer": "max", "mosaicRule": rule}, "L")
    assert _doc(m)["layers"][0]["source"]["options"]["mosaicRule"] == rule


def test_addLayer_refuses_an_earth_engine_asset_id_in_georest():
    with pytest.raises(ValueError, match="geeViz"):
        mapper().addLayer("USGS/SRTMGL1_003", {}, "SRTM")


def test_addTimeLapse_takes_a_time_enabled_service_with_a_reducer():
    m = mapper()
    m.addTimeLapse(LANDSAT, {"reducer": "max"}, "Landsat")
    lay = _doc(m)["layers"][0]
    assert lay["type"] == "esri.timelapse"
    assert lay["source"]["options"]["mosaicRule"]["mosaicOperation"] == "MT_MAX"


def test_addTileLayer_legend_is_classLegendDict():
    m = mapper()
    m.addTileLayer(XYZ, name="H", legend={"Water": "#0000ff"})
    assert _doc(m)["layers"][0]["viz"]["classLegendDict"] == {"Water": "0000ff"}


def test_the_docs_send_time_series_to_addTimeLapse():
    assert "addTimeLapse" in mapper.addLayer.__doc__
    assert "time-enabled" in mapper.addLayer.__doc__


def test_addLayer_keeps_visible_for_every_service_kind():
    m = mapper()
    for url in (NFHL, FS, LANDSAT, XYZ, GJ_URL):
        m.addLayer(url, {}, url[-12:], visible=False)
    assert [l["visible"] for l in _doc(m)["layers"]] == [False] * 5
