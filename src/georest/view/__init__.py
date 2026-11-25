"""geoView: georest's map viewer, and geeViz's default viewer (its "geoview" skin).

The viewer's files are in ``STATIC_DIR``: a page (``geoview.html``), its
scripts, styles and images. It reads a JSON map document and draws Earth
Engine (through geeViz), ArcGIS, GeoJSON and XYZ tile layers.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

from ._map import mapper  # noqa: E402  (after STATIC_DIR, which the modules read)

#: The module's map, as geeViz's ``geeViz.geeView.Map``; ``mapper()`` makes another.
Map = mapper()


def view(open_browser=None, open_iframe=None, iframe_height=525):
    """Show ``Map`` (see :meth:`mapper.view`)."""
    return Map.view(open_browser=open_browser, open_iframe=open_iframe, iframe_height=iframe_height)


def export_html(filename=None, asset_base=None, title=None):
    """Write ``Map`` as a page (see :meth:`mapper.export_html`)."""
    return Map.export_html(filename=filename, asset_base=asset_base, title=title)


__all__ = ["Map", "mapper", "view", "export_html", "STATIC_DIR"]
