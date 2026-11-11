"""georest.nifc - the National Interagency Fire Center's open data.

Modules
-------
perimeters
    Wildfire perimeters by fire name: the official interagency perimeter
    with its acres and its discovery, containment and fire-out dates -
    the dates a before/after comparison is built around.

``nifc`` is the NIFC provider. It sits under ``georest`` beside ``esri``,
``fema``, ``noaa``, ``usgs`` and ``usfs``, and uses only the standard
library. NIFC publishes on ArcGIS Online, so its requests go through the
same transport as ``georest.esri``.

Quick start::

    from georest.nifc import perimeters

    perimeters.find_fires("Monroe Canyon", year=2025)
    fc = perimeters.fire_perimeter("Cameron Peak", year=2020)

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import perimeters

__all__ = ["perimeters"]
