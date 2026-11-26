"""The map document geoView reads: plain JSON, checked before it is served."""
import json

import pytest

from georest.view._document import (
    COMMANDS, LAYER_TYPES, add_layer, new_document, to_json, validate,
)

SOURCES = {
    "ee.image": {"serialized": {"result": "0", "values": {}}},
    "ee.imageCollection": {"serialized": {"result": "0", "values": {}}},
    "ee.vectorImage": {"serialized": {"result": "0", "values": {}}},
    "ee.vector": {"serialized": {"result": "0", "values": {}}},
    "ee.timelapse": {"serialized": {"result": "0", "values": {}}},
    "ee.select": {"serialized": {"result": "0", "values": {}}},
    "ee.areaChart": {"serialized": {"result": "0", "values": {}}},
    "geojson": {"geojson": {"type": "FeatureCollection", "features": []}},
    "xyz": {"url": "https://t/{z}/{x}/{y}.png"},
    "xyz.timelapse": {"frames": [{"label": "2001", "url": "https://t/2001/{z}/{x}/{y}.png"}], "maxZoom": 8},
    "esri.feature": {"url": "https://x/arcgis/rest/services/a/FeatureServer/0"},
    "esri.map": {"url": "https://x/arcgis/rest/services/a/MapServer"},
    "esri.tile": {"url": "https://x/arcgis/rest/services/a/MapServer"},
    "esri.image": {"url": "https://x/arcgis/rest/services/a/ImageServer"},
    "esri.timelapse": {"url": "https://x/arcgis/rest/services/a/ImageServer"},
}


def test_every_layer_type_validates():
    assert set(SOURCES) == set(LAYER_TYPES)
    doc = new_document("All")
    for t, src in SOURCES.items():
        layer = add_layer(doc, t, src, viz={"opacity": 0.5}, name=t, visible=False)
        assert layer["id"].startswith("layer-")
    doc["view"] = {"center": [-105, 40], "zoom": 9}
    doc["commands"] = [["turnOnInspector"], ["setQueryPrecision", 3, 0.25]]
    assert validate(doc) is doc
    assert [l["type"] for l in doc["layers"]] == list(SOURCES)
    assert len({l["id"] for l in doc["layers"]}) == len(SOURCES)
    assert doc["proxy"] is None


@pytest.mark.parametrize("bad, field", [
    ({"geoview": 2}, "geoview"),
    ({"layers": [{"id": "a", "type": "script", "source": {}}]}, "script"),
    ({"commands": [["eval", "x"]]}, "eval"),
    ({"commands": ["turnOnInspector"]}, "commands"),
    ({"view": {"center": [1]}}, "view"),
])
def test_bad_documents_name_what_is_wrong(bad, field):
    doc = {**new_document(), **bad}
    with pytest.raises(ValueError, match=field):
        validate(doc)


def test_commands_are_a_fixed_list():
    assert "turnOnInspector" in COMMANDS and "setGlobal" in COMMANDS
    for adder in ("addLayer", "addSerializedLayer", "addEsriLayer", "addTimeLapse"):
        assert adder not in COMMANDS


def test_to_json_can_be_inlined_in_a_page():
    doc = new_document()
    add_layer(doc, "geojson", {"geojson": {"type": "Feature", "properties": {"n": "</script><script>alert(1)</script>"}, "geometry": None}})
    text = to_json(doc)
    assert "</script" not in text.lower()
    assert "<!--" not in text
    assert json.loads(text)["layers"][0]["source"]["geojson"]["properties"]["n"] == "</script><script>alert(1)</script>"


def test_the_viewer_runs_the_same_command_list():
    import pathlib
    import re
    from georest.view import STATIC_DIR
    js = (pathlib.Path(STATIC_DIR) / "js" / "document.js").read_text(encoding="utf-8")
    block = re.search(r"export const COMMANDS = \[(.*?)\];", js, re.S).group(1)
    assert set(re.findall(r'"(\w+)"', block)) == set(COMMANDS)
