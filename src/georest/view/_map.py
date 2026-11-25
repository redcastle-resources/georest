"""georest's Map: geeViz's Map methods, for maps without Earth Engine.

Methods keep geeViz's names and arguments, so code and habits carry over.
A Map builds a map document (georest.view._document); ``view()`` shows it
from the local server, ``export_html()`` writes a page that carries it.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
from __future__ import annotations

import calendar
import copy
import datetime
import os
import re
import secrets
import webbrowser

from ._document import add_layer, new_document, validate
from ._page import page_html, with_maps_key

EE_ONLY = "{} needs Earth Engine: use geeViz's Map (geeViz.geeView.Map)."
_STYLE_KEYS = ("strokeColor", "fillColor", "fillOpacity", "strokeWeight", "strokeOpacity", "opacity",
               "canAreaChart", "chartField", "chartStatistic", "chartValueField", "legendTitle",
               "pointRadius", "areaChartParams")
_ESRI_OPT_KEYS = ("layers", "renderingRule", "mosaicRule", "bandIds", "outFields")


def cdn_base(version: str | None = None) -> str:
    """Where the viewer's files are on jsDelivr for a georest release."""
    if version is None:
        from .. import __version__ as version
    return f"https://cdn.jsdelivr.net/gh/redcastle-resources/georest@v{version}/src/georest/view/static"


# ---- ArcGIS layers (shared with geeViz's Map.addEsri*) ----
def esri_time_span(t):
    """[start, end] in milliseconds (UTC) for ``time=``: a year, an ISO date
    or month, a date or datetime, or a (start, end) pair (end inclusive)."""
    def ms(d):
        if isinstance(d, datetime.datetime) and d.tzinfo is not None:
            return calendar.timegm(d.utctimetuple()) * 1000
        return calendar.timegm(d.timetuple()) * 1000

    def span(v):
        if isinstance(v, datetime.datetime):
            return ms(v), ms(v)
        if isinstance(v, datetime.date):
            d = datetime.datetime(v.year, v.month, v.day)
            return ms(d), ms(d + datetime.timedelta(days=1)) - 1
        s = str(v).strip()
        if re.fullmatch(r"\d{4}", s):
            y = int(s)
            return ms(datetime.datetime(y, 1, 1)), ms(datetime.datetime(y + 1, 1, 1)) - 1
        if re.fullmatch(r"\d{4}-\d{2}", s):
            y, mo = map(int, s.split("-"))
            nxt = datetime.datetime(y + (mo == 12), mo % 12 + 1, 1)
            return ms(datetime.datetime(y, mo, 1)), ms(nxt) - 1
        try:
            d = datetime.datetime.fromisoformat(s)
        except ValueError:
            raise ValueError(f"time must be a year, an ISO date, a (start, end) pair or 'all'; got {v!r}") from None
        return span(d.date()) if len(s) <= 10 else (ms(d), ms(d))

    if t is None:
        return None
    if isinstance(t, (list, tuple)):
        if len(t) != 2:
            raise ValueError(f"time as a range is (start, end); got {t!r}")
        return [span(t[0])[0], span(t[1])[1]]
    return list(span(t))


def esri_default_name(url: str) -> str:
    """A layer name from a service URL: the service, and its sublayer."""
    parts = url.split("/rest/services/", 1)[-1].strip("/").split("/")
    for i, p in enumerate(parts):
        if p in ("MapServer", "FeatureServer", "ImageServer"):
            svc = parts[i - 1] if i else p
            sub = parts[i + 1] if i + 1 < len(parts) else None
            return f"{svc} {sub}" if sub is not None else svc
    return parts[-1] if parts else url


def esri_kind(url: str) -> str:
    """The layer type a service URL is drawn as (the viewer confirms it)."""
    u = url.rstrip("/")
    if u.lower().endswith("imageserver"):
        return "esri.image"
    if re.search(r"/(FeatureServer|MapServer)/\d+$", u, re.I) or u.lower().endswith("featureserver"):
        return "esri.feature"
    return "esri.map"


def resolve_url(url_or_result) -> str:
    """A service URL from a URL or a georest.esri.portal search result."""
    if isinstance(url_or_result, dict):
        url = url_or_result.get("url")
        if not url:
            raise ValueError("this search result has no service url")
        return str(url).rstrip("/")
    if isinstance(url_or_result, str) and url_or_result.strip():
        return url_or_result.strip().rstrip("/")
    raise TypeError("give an ArcGIS service URL or a portal search result")


def esri_layer(url: str, kind: str, viz_params=None, time=None, opts=None, secured=None):
    """(type, source, viz) for an ArcGIS layer in a map document."""
    viz_params = dict(viz_params or {})
    viz = {k: viz_params[k] for k in _STYLE_KEYS if k in viz_params}
    options = {k: viz_params[k] for k in _ESRI_OPT_KEYS if k in viz_params}
    options.update({k: v for k, v in (opts or {}).items() if v is not None})
    layer_type = kind
    if isinstance(time, str) and time.strip().lower() == "all":
        if re.search(r"/FeatureServer(/\d+)?$", url, re.I):
            raise ValueError(f"{esri_default_name(url)} is a feature layer: a timelapse of its features is not "
                             "supported yet. Add it with time= for one period.")
        layer_type = "esri.timelapse"
        viz["esriKind"] = kind
    elif time is not None:
        options["time"] = esri_time_span(time)
    if secured:
        viz["esri"] = {"secured": secured}
    return layer_type, {"url": url, "options": options}, viz


def _features(obj):
    """GeoJSON for what addLayer takes, or None."""
    if hasattr(obj, "__geo_interface__"):
        return copy.deepcopy(obj.__geo_interface__)
    if isinstance(obj, dict) and obj.get("type"):
        return copy.deepcopy(obj)
    if isinstance(obj, list) and all(isinstance(f, dict) and f.get("type") == "Feature" for f in obj):
        return {"type": "FeatureCollection", "features": copy.deepcopy(obj)}
    cols = getattr(obj, "columns", None)
    if cols is not None and "geometry" in list(cols):
        feats = []
        for rec in obj.to_dict(orient="records"):
            geom = rec.pop("geometry")
            props = {k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in rec.items()}
            feats.append({"type": "Feature", "properties": props, "geometry": geom})
        return {"type": "FeatureCollection", "features": feats}
    return None


class mapper:
    """A map: layers, view and settings, shown with view() or written with
    export_html(). Method names and arguments are geeViz's."""

    def __init__(self):
        self.clearMap()
        self._maps_key = None

    # ---- the document ----
    def document(self) -> dict:
        """This map's document (a copy, validated)."""
        return validate(copy.deepcopy(self._doc))

    def clearMap(self):
        """Remove every layer and command."""
        self._doc = new_document()

    def clearMapLayers(self):
        self._doc["layers"] = []

    def clearMapCommands(self):
        self._doc["commands"] = []
        self._doc["view"] = None

    def _cmd(self, *command):
        c = list(command)
        if c not in self._doc["commands"]:
            self._doc["commands"].append(c)

    # ---- layers ----
    def addLayer(self, obj, viz=None, name=None, visible=True, opacity=None):
        """GeoJSON (a dict, a list of Features, anything with
        ``__geo_interface__``, or a table with a ``geometry`` column), or an
        ArcGIS service URL."""
        if isinstance(obj, str) and "/rest/services/" in obj:
            return self.addEsriService(obj, viz_params=dict(viz or {}, **({"opacity": opacity} if opacity is not None else {})),
                                       name=name, visible=visible)
        gj = _features(obj)
        if gj is None:
            raise TypeError("addLayer takes GeoJSON, a table with a geometry column, or an ArcGIS service URL")
        add_layer(self._doc, "geojson", {"geojson": gj}, viz=viz, name=name, visible=visible, opacity=opacity)

    def addTileLayer(self, url_template, name="Tile Layer", visible=True, opacity=1.0, max_zoom=20, legend=None):
        if not all(tok in str(url_template) for tok in ("{x}", "{y}", "{z}")):
            raise ValueError(f"url_template must contain {{x}}, {{y}} and {{z}}; got {url_template!r}")
        viz = {}
        if legend:
            viz["classLegendDict"] = {k: str(v).lstrip("#") for k, v in legend.items()}
        add_layer(self._doc, "xyz", {"url": url_template, "maxZoom": int(max_zoom)}, viz=viz, name=name,
                  visible=visible, opacity=opacity)

    def addDynamicMapService(self, service_url, name="Dynamic MapService", visible=True, layers="",
                             transparent=True, dpi=96, min_zoom=0, token=None):
        viz_params = {"layers": layers} if layers else {}
        return self.addEsriMapService(service_url, name=name, token=token, viz_params=viz_params, visible=visible)

    def _esri(self, url_or_result, kind, name, visible, viz_params, time, token, opts=None):
        url = resolve_url(url_or_result)
        secured = None
        if token:
            from ._server import server
            secured = "s" + secrets.token_hex(6)
            server().secure(secured, token)
        viz_params = dict(viz_params or {})
        if visible is None:
            visible = viz_params.get("visible", True)
        opacity = viz_params.get("opacity")
        t, source, viz = esri_layer(url, kind or esri_kind(url), viz_params, time, opts, secured)
        viz.pop("opacity", None)
        add_layer(self._doc, t, source, viz=viz, name=name or esri_default_name(url), visible=visible, opacity=opacity)

    def addEsriImageService(self, url_or_result, viz_params=None, name=None, token=None, time=None):
        """An ArcGIS image service, drawn by export (cached: by tiles)."""
        self._esri(url_or_result, "esri.image", name, None, viz_params, time, token)

    def addEsriMapService(self, url_or_result, name=None, token=None, viz_params=None, visible=None, time=None):
        """An ArcGIS map service; ``viz_params["layers"]`` picks sublayers ("show:28")."""
        self._esri(url_or_result, "esri.map", name, visible, viz_params, time, token)

    def addEsriFeatureService(self, url_or_result, viz_params=None, name=None, max_features=None, where="1=1",
                              token=None, bbox=None, simplify="auto", max_layer_mb=None, time=None):
        """An ArcGIS feature layer, fetched by the viewer for the view as it moves."""
        if isinstance(bbox, str):
            bbox = [float(v) for v in bbox.split(",")]
        opts = {"where": None if where in (None, "", "1=1") else where, "bbox": list(bbox) if bbox is not None else None,
                "maxFeatures": max_features, "simplify": None if simplify == "auto" else simplify,
                "maxLayerMB": max_layer_mb}
        self._esri(url_or_result, "esri.feature", name, None, viz_params, time, token, opts)

    def addEsriService(self, url_or_result, viz_params=None, name=None, token=None, max_features=None,
                       where="1=1", bbox=None, simplify="auto", time=None, visible=None):
        """Any ArcGIS service, by its type."""
        url = resolve_url(url_or_result)
        kind = esri_kind(url)
        if kind == "esri.feature":
            return self.addEsriFeatureService(url, viz_params=viz_params, name=name, max_features=max_features,
                                              where=where, token=token, bbox=bbox, simplify=simplify, time=time)
        self._esri(url, kind, name, visible, viz_params, time, token)

    def addTimeLapse(self, url_or_layers, viz=None, name=None, visible=True):
        """A time-enabled ArcGIS service, one frame per time step."""
        if not isinstance(url_or_layers, (str, dict)):
            raise NotImplementedError("A timelapse from a list of layers is not supported yet; give a time-enabled "
                                      "ArcGIS service URL.")
        url = resolve_url(url_or_layers)
        self._esri(url, esri_kind(url) if esri_kind(url) != "esri.feature" else "esri.feature", name, visible,
                   viz, "all", None)

    def addSelectLayer(self, obj, viz=None, name=None):
        raise NotImplementedError("addSelectLayer for GeoJSON and ArcGIS layers is not supported yet; add the "
                                  "layer with addLayer and chart a drawn or uploaded area.")

    def addAreaChartLayer(self, url_or_result, viz=None, name=None):
        """An ArcGIS image or feature service charted by Chart area, not drawn."""
        viz = dict(viz or {}, canAreaChart=True)
        url = resolve_url(url_or_result)
        self._esri(url, esri_kind(url), name, True, viz, None, None)
        self._doc["layers"][-1]["viz"]["chartOnly"] = True

    # ---- view ----
    def setCenter(self, lng, lat, zoom=None):
        self._doc["view"] = {"center": [lng, lat], "zoom": zoom} if zoom is not None else {"center": [lng, lat]}

    def setZoom(self, zoom):
        self._cmd("mapSetZoom", zoom)

    def centerObject(self, obj, zoom=None):
        """Center on GeoJSON, a [w, s, e, n] box, or a layer's name."""
        if hasattr(obj, "getInfo"):
            raise NotImplementedError(EE_ONLY.format("centerObject on an Earth Engine object"))
        if isinstance(obj, (list, tuple)) and len(obj) == 4 and all(isinstance(v, (int, float)) for v in obj):
            self._doc["view"] = {"bounds": [float(v) for v in obj]}
            return
        if isinstance(obj, str):
            match = [l for l in self._doc["layers"] if l["name"] == obj and "geojson" in l["source"]]
            if not match:
                raise ValueError(f"no GeoJSON layer named {obj!r} to center on")
            obj = match[0]["source"]["geojson"]
        gj = _features(obj)
        xs, ys = [], []

        def walk(c):
            if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
                xs.append(c[0])
                ys.append(c[1])
            elif isinstance(c, (list, tuple)):
                for x in c:
                    walk(x)

        def geoms(o):
            if not o:
                return
            if o.get("type") == "FeatureCollection":
                for f in o.get("features") or []:
                    geoms(f)
            elif o.get("type") == "Feature":
                geoms(o.get("geometry"))
            elif o.get("type") == "GeometryCollection":
                for g in o.get("geometries") or []:
                    geoms(g)
            else:
                walk(o.get("coordinates"))

        geoms(gj)
        if not xs:
            raise ValueError("nothing to center on")
        self._doc["view"] = {"bounds": [min(xs), min(ys), max(xs), max(ys)]}

    def setTitle(self, title):
        self._doc["title"] = str(title)

    setMapTitle = setTitle

    # ---- query and tools ----
    def setQueryWindowMode(self, mode):
        self._cmd("setGlobal", "queryWindowMode", mode)

    def setQueryToInfoWindow(self):
        self.setQueryWindowMode("infoWindow")

    def setQueryToSidePane(self):
        self.setQueryWindowMode("sidePane")

    def setQueryBoxColor(self, color):
        self._cmd("setQueryBoxColor", color)

    def setQueryPrecision(self, chartPrecision=3, chartDecimalProportion=0.25):
        self._cmd("setQueryPrecision", chartPrecision, chartDecimalProportion)

    def turnOnInspector(self):
        self._cmd("turnOnInspector")

    def turnOnUserDefinedAreaCharting(self):
        self._cmd("turnOnUserDefinedAreaCharting")

    def turnOnSelectionAreaCharting(self):
        self._cmd("turnOnSelectionAreaCharting")

    def turnOnAutoAreaCharting(self):
        self._cmd("turnOnAutoAreaCharting")

    def setCanReorderLayers(self, can_reorder=True):
        self._cmd("setProperty", "canReorderLayers", bool(can_reorder))

    def turnOffAllLayers(self):
        self._cmd("turnOffAllLayers")

    def turnOnAllLayers(self):
        self._cmd("turnOnAllLayers")

    # ---- Earth Engine only ----
    def addWindLayer(self, *a, **k):
        raise NotImplementedError(EE_ONLY.format("addWindLayer"))

    def addWindTimeLapse(self, *a, **k):
        raise NotImplementedError(EE_ONLY.format("addWindTimeLapse"))

    def setQueryCRS(self, *a, **k):
        raise NotImplementedError(EE_ONLY.format("setQueryCRS"))

    def setQueryScale(self, *a, **k):
        raise NotImplementedError(EE_ONLY.format("setQueryScale"))

    def setQueryTransform(self, *a, **k):
        raise NotImplementedError(EE_ONLY.format("setQueryTransform"))

    # ---- showing it ----
    def setMapsKey(self, key):
        """Your Google Maps key, in place of the one geoView ships with."""
        self._maps_key = key

    def _key(self):
        return self._maps_key or os.environ.get("GOOGLE_MAPS_API_KEY") or None

    def view(self, open_browser=None, open_iframe=None, iframe_height=525):
        """Show the map from the local server: in a notebook as a frame, else
        in the browser. Returns the page URL."""
        from ._server import server
        srv = server()
        srv.maps_key = self._key()
        doc = self.document()
        doc["proxy"] = "/proxy"
        url = srv.url_for(doc)
        in_notebook = _in_notebook()
        if open_iframe if open_iframe is not None else in_notebook:
            from IPython.display import IFrame, display
            display(IFrame(url, width="100%", height=iframe_height))
        elif open_browser if open_browser is not None else not in_notebook:
            webbrowser.open(url)
        return url

    def export_html(self, filename=None, asset_base=None, title=None):
        """A page that carries the map: the viewer's files from ``asset_base``
        (default: jsDelivr at this georest release). Written to ``filename``
        when given. Secured layers need the local server or the geeViz agent."""
        doc = self.document()
        if title:
            doc["title"] = title
        html = page_html(doc, asset_base or cdn_base(), self._key())
        if filename:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html)
        return html


def _in_notebook() -> bool:
    try:
        from IPython import get_ipython
    except ImportError:
        return False
    ip = get_ipython()
    return bool(ip) and getattr(ip, "kernel", None) is not None


__all__ = ["mapper", "with_maps_key", "cdn_base", "esri_layer", "esri_time_span", "esri_default_name", "esri_kind"]
