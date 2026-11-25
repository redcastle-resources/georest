"""The map document geoView reads.

A map is plain data, not a program: its layers (each a plug-in type and that
plug-in's source), where the map looks, and the map commands to run, each a
name from a fixed list with its arguments. It can be saved, embedded in a
page, compared and read without running anything.

    {"geoview": 1, "title": str,
     "brand": {"name": str, "accent": str, "logo": "georest" | "geeviz"} | None,
     "layers": [{"id", "type", "name", "source", "viz", "visible", "opacity"}],
     "view": {"center": [lng, lat], "zoom": z} | {"bounds": [w, s, e, n]} | None,
     "commands": [[name, *args], ...],
     "ee": {"authProxyAPIURL": str | None, "workloadTag": str | None} | None,
     "proxy": "/proxy" | None}

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
from __future__ import annotations

import json

VERSION = 1

LAYER_TYPES = (
    "ee.image", "ee.imageCollection", "ee.vectorImage", "ee.vector", "ee.timelapse",
    "ee.select", "ee.areaChart", "geojson", "xyz",
    "esri.feature", "esri.map", "esri.tile", "esri.image", "esri.timelapse",
)

#: The map commands a document may ask for: the viewer's Map methods that
#: set the map up (the layer adders are the document's own layers), and the
#: few calls geeViz's map script makes outside Map.
COMMANDS = frozenset({
    "centerObject", "setCenter", "setTitle", "setDefaultWorkloadTag",
    "turnOnInspector", "turnOffInspector",
    "turnOnAutoAreaCharting", "turnOffAutoAreaCharting",
    "turnOnUserDefinedAreaCharting", "turnOffUserDefinedAreaCharting",
    "turnOnSelectionAreaCharting", "turnOffSelectionAreaCharting",
    "turnOnTimeSeriesCharting", "turnOffTimeSeriesCharting",
    "hideSelectLayerAreaCharting", "showSelectLayerAreaCharting",
    "setQueryCRS", "setQueryScale", "setQueryTransform", "setQueryPrecision",
    "setQueryDateFormat", "setQueryBoxColor", "showSpinner", "hideSpinner",
    "turnOffAllLayers", "turnOnAllLayers",
    # outside Map: globals the map script assigns, Map properties, map.setZoom,
    # synchronousCenterObject and areaChart.populateChartLayerSelect
    "setGlobal", "setProperty", "mapSetZoom", "synchronousCenterObject",
    "populateChartLayerSelect",
})

_KEYS = {"geoview", "title", "brand", "layers", "view", "commands", "ee", "proxy"}


def new_document(title: str = "") -> dict:
    """An empty map document."""
    return {"geoview": VERSION, "title": title, "brand": None, "layers": [],
            "view": None, "commands": [], "ee": None, "proxy": None}


def add_layer(doc: dict, type: str, source: dict, viz: dict | None = None, name: str | None = None,
              visible: bool = True, opacity: float | None = None) -> dict:
    """Append a layer to ``doc`` and return it (its id is ``layer-<n>``)."""
    n = len(doc["layers"]) + 1
    layer = {"id": f"layer-{n}", "type": type, "name": name if name is not None else f"Layer {n}",
             "source": source, "viz": dict(viz or {}), "visible": bool(visible)}
    if opacity is not None:
        layer["opacity"] = float(opacity)
    doc["layers"].append(layer)
    return layer


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(doc: dict) -> dict:
    """Check a document; raise ``ValueError`` naming what is wrong. Returns it."""
    if not isinstance(doc, dict):
        raise ValueError("a map document is a JSON object")
    if doc.get("geoview") != VERSION:
        raise ValueError(f"geoview: this viewer reads version {VERSION} documents, not {doc.get('geoview')!r}")
    extra = set(doc) - _KEYS
    if extra:
        raise ValueError(f"unknown document keys: {sorted(extra)}")
    layers = doc.get("layers") or []
    if not isinstance(layers, list):
        raise ValueError("layers: a list")
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("type") not in LAYER_TYPES:
            t = layer.get("type") if isinstance(layer, dict) else layer
            raise ValueError(f"layer type {t!r} is not one of {', '.join(LAYER_TYPES)}")
        if not isinstance(layer.get("source"), dict):
            raise ValueError(f"layer {layer.get('id')!r}: source is an object")
    view = doc.get("view")
    if view is not None:
        ok = isinstance(view, dict) and (
            ("center" in view and isinstance(view["center"], list) and len(view["center"]) == 2
             and all(_number(v) for v in view["center"]))
            or ("bounds" in view and isinstance(view["bounds"], list) and len(view["bounds"]) == 4
                and all(_number(v) for v in view["bounds"])))
        if not ok:
            raise ValueError("view: {'center': [lng, lat], 'zoom': z} or {'bounds': [w, s, e, n]}")
    commands = doc.get("commands") or []
    if not isinstance(commands, list):
        raise ValueError("commands: a list of [name, *args]")
    for c in commands:
        if not isinstance(c, list) or not c or not isinstance(c[0], str):
            raise ValueError(f"commands: each is [name, *args], not {c!r}")
        if c[0] not in COMMANDS:
            raise ValueError(f"command {c[0]!r} is not one a map document may run")
    return doc


def to_json(doc: dict) -> str:
    """The document as compact JSON that can be inlined in a page's script
    element: ``</`` and ``<!--`` cannot end or confuse the element."""
    text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    return text.replace("</", "<\\/").replace("<!--", "<\\u0021--")
