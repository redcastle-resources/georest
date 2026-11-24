"""ArcGIS / Esri REST service clients - stdlib only, no third-party deps.

Modules
-------
services
    Generic Image/Map/Feature Service queries: tile URL templates, GeoJSON
    feature fetching (one request, or paged past the server's limit),
    window-function analytics (queryAnalytic, topNPerGroup), and Image
    Service raster operations (exportImage, computeStatisticsHistograms,
    identifyPixelValue, getSamples, queryBoundary, getSupportedOperations).
portal
    ArcGIS Portal search (IIPP, AGOL, USGS, NOAA, USFS, NASA, or any
    Enterprise portal) and service metadata.
auth
    Sign in as a user (OAuth 2.0 + PKCE, or a token handed in from the
    arcgis API). Calls without ``token=`` then use the session on the
    hosts it covers.
_http
    Internal shared urllib GET/POST/JSON helpers. Not part of the public API.

The agency clients built on these live with their agency:
``georest.usfs.edw`` (the Forest Service Enterprise Data Warehouse, here as
``georest.esri.edw`` until 0.5.2; that name still works, with a warning),
``georest.fema.nfhl`` and ``georest.nifc.perimeters``.

Copyright 2026 Ryan Rock and Ian Housman
Portions ported from geeViz (esriLib.py, edwLib.py).

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import importlib

from . import auth, portal, services
from ._http import EsriServiceError

__all__ = ["EsriServiceError", "auth", "portal", "services"]


def __getattr__(name: str):
    # georest.esri.edw moved to georest.usfs.edw; the old attribute still
    # resolves (importing it warns) so `georest.esri.edw.x` keeps working.
    if name == "edw":
        return importlib.import_module(".edw", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
