"""georest.fema - clients for FEMA APIs.

Modules
-------
nfhl
    The National Flood Hazard Layer: the flood zone at a point from the
    effective Flood Insurance Rate Map, and when that map took effect -
    with any later revision, and new maps that are pending or preliminary.

``fema`` is the FEMA provider. It sits under ``georest`` beside ``esri``,
``usgs`` and ``usfs``, uses only the standard library, and is
self-contained: its private ``_http`` module is its own and not part of
the public API.

Quick start::

    from georest.fema import nfhl

    nfhl.flood_zone(-95.3698, 29.7604)
    nfhl.map_dates(-80.1300, 25.7907)

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import nfhl

__all__ = ["nfhl"]
