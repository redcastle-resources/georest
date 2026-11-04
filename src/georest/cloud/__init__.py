"""georest.cloud - clients for cloud-native geospatial catalogs.

Finds imagery and gridded data over a place and hands back links. It does
not read pixels: rasterio, odc-stac, zarr and the rest of the cloud-native
ecosystem do that well, and the guides show the hand-off.

``cloud`` is the cloud-native provider, beside ``esri``, ``usgs``, ``usfs``,
``fema`` and ``noaa``. It returns pandas DataFrames for tables, and is
self-contained: its private ``_http`` and ``_frames`` modules are its own.
The STAC client itself is added in the modules that follow this skeleton.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

__all__: list[str] = []
