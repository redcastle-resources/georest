"""georest.cloud - clients for cloud-native geospatial catalogs.

Finds imagery and gridded data over a place and hands back links. It does
not read pixels: rasterio, odc-stac, zarr and the rest of the cloud-native
ecosystem do that well, and the guides show the hand-off.

Modules
-------
stac
    STAC APIs: the catalogs georest knows and how each one's files are read,
    and the collections in a catalog, searchable by word.
catalogs
    The curated registry behind it: Earth Search, Planetary Computer,
    USGS LandsatLook and NASA's CMR-STAC, with the collections that matter
    for forest and land work.

``cloud`` is the cloud-native provider, beside ``esri``, ``usgs``, ``usfs``,
``fema`` and ``noaa``. It returns pandas DataFrames for tables, and is
self-contained: its private ``_http`` and ``_frames`` modules are its own.

Quick start::

    from georest.cloud import stac

    stac.catalogs()
    stac.collections("planetary-computer", "fire")

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import catalogs, stac

__all__ = ["stac", "catalogs"]
