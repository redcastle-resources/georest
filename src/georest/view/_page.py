"""A geoView page for one map: the viewer's page with its map document inline.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
from __future__ import annotations

import os
import re

from . import STATIC_DIR
from ._document import to_json

_KEY = re.compile(r"(maps\.googleapis\.com/maps/api/js\?key=)AIza[\w-]+")
_SLOT = '<script type="application/json" id="geoview-document"></script>'
_MAIN = '<script type="module" src="./js/main.js"></script>'


def page_template() -> str:
    with open(os.path.join(STATIC_DIR, "geoview.html"), encoding="utf-8") as f:
        return f.read()


def default_maps_key() -> str:
    """The Google Maps key the viewer ships with."""
    m = re.search(r"maps\.googleapis\.com/maps/api/js\?key=(AIza[\w-]+)", page_template())
    return m.group(1) if m else ""


def with_maps_key(html: str, key: str | None) -> str:
    """``html`` with its Google Maps key replaced (unchanged for no key)."""
    return _KEY.sub(lambda m: m.group(1) + key, html) if key else html


def page_html(doc: dict | None, asset_base: str, maps_key: str | None = None, extra_head_html: str = "") -> str:
    """The page for ``doc``: the viewer's files from ``asset_base`` (a URL or
    path ending at the viewer's static folder), the Google Maps key, the
    document inline, and ``extra_head_html`` before the viewer starts (geeViz
    adds its Earth Engine sign-in settings there)."""
    html = page_template()
    html = html.replace("<head>", '<head>\n    <base href="' + asset_base.rstrip("/") + '/">', 1)
    html = with_maps_key(html, maps_key)
    if doc is not None:
        html = html.replace(_SLOT, '<script type="application/json" id="geoview-document">' + to_json(doc) + "</script>", 1)
    if extra_head_html:
        html = html.replace(_MAIN, extra_head_html + "\n    " + _MAIN, 1)
    return html
