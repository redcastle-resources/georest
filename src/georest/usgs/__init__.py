"""georest.usgs - clients for USGS APIs.

Modules
-------
waterdata
    The USGS Water Data OGC API: monitoring locations, daily and continuous
    values, field measurements, annual peaks, and their metadata.
nldi
    The Hydro Network-Linked Data Index: find the river reach at a point,
    walk the network upstream or downstream, list the gages and other sites
    along the way, and delineate the basin that drains to a point.
nationalmap
    The National Map: the 3DEP elevation at any point (EPQS), and search
    and download links for 3DEP elevation, lidar, US Topo, NAIP and other
    National Map products (TNM Access).
earthquakes
    The USGS earthquake catalog: events worldwide by time, area, magnitude
    and depth, with PAGER alerts and tsunami flags, and each event's detail.
sciencebase
    ScienceBase, the catalog of USGS data releases: search, item details,
    the items inside an item, and attached files with download links.
wqp
    The Water Quality Portal (USGS and EPA): monitoring locations and
    water-quality results from the USGS, EPA, states, tribes and others.

``usgs`` is the USGS provider. It sits under ``georest`` beside
``esri`` and ``usfs`` so providers can be added without a namespace
collision, and it is self-contained: its private ``_http``, ``_ogc`` and
``_frames`` modules are its own and not part of the public API.

Quick start::

    from georest.usgs import nldi, waterdata

    fc = waterdata.get_daily_values("USGS-09380000", "00060",
                                    start="2024-01-01", end="2024-01-31")
    rows = waterdata.to_rows(fc)

    upstream_gages = nldi.sites_along("nwissite", "USGS-09380000",
                                      "upstream_tributaries", distance_km=100)

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import earthquakes, nationalmap, nldi, sciencebase, waterdata, wqp

__all__ = ["waterdata", "nldi", "nationalmap", "earthquakes", "sciencebase", "wqp"]
