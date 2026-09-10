"""ArcGIS / Esri REST service clients — stdlib only, no third-party deps.

Modules
-------
services
    Generic Image/Map/Feature Service queries: tile URL templates, GeoJSON
    feature fetching, and Image Service raster operations (exportImage,
    computeStatisticsHistograms, identifyPixelValue, getSamples,
    queryBoundary, getSupportedOperations).
edw
    USFS Enterprise Data Warehouse client: catalog search, layer-role
    tagging, FGDC/ISO metadata parsing, feature and analytic queries.
portal
    ArcGIS Portal search (IIPP, AGOL, USGS, NOAA, USFS, NASA, or any
    Enterprise portal) and service metadata.
_http
    Internal shared urllib GET/POST/JSON helpers. Not part of the public API.

Copyright 2026 Ryan Rock and Ian Housman
Portions ported from geeViz (esriLib.py, edwLib.py).

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import edw, portal, services

__all__ = ["edw", "portal", "services"]
