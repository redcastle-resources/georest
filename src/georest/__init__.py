"""georest - Python clients for geospatial REST APIs.

Subpackages
-----------
esri
    ArcGIS / Esri REST services: Image, Map and Feature Services, the USFS
    Enterprise Data Warehouse (EDW), and ArcGIS Portal search.
usgs
    USGS APIs: the Water Data OGC API (monitoring locations, daily and
    continuous values, field measurements, peaks).
usfs
    USDA Forest Service APIs: FIA estimates with their sampling errors
    (EVALIDator) and LCMS land cover, land use and change summaries.
fema
    FEMA APIs: the flood zone at a point from the National Flood Hazard
    Layer, and when that map took effect, with new maps coming.

Quick start::

    from georest.esri import edw, portal, services
    from georest.usgs import waterdata

    hits = edw.search_edw_services("fire")
    meta = portal.getServiceMetadata("https://.../ImageServer")
    tiles = services.getImageServiceTileUrl("https://.../ImageServer")
    flow = waterdata.get_daily_values("USGS-09380000", "00060", start="2024-01-01")

The public provider modules are also re-exported here for convenience. They
are imported lazily (PEP 562), so ``import georest`` itself costs almost
nothing::

    from georest import edw          # the same module object as
                                     # georest.esri.edw
    from georest import waterdata    # georest.usgs.waterdata

``esri`` uses only the standard library. ``usgs`` and ``usfs`` return
pandas DataFrames, and ``usfs``' Earth Engine path needs the optional
``georest[ee]`` extra.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.5.0.dev0"

__all__ = ["esri", "edw", "portal", "services", "usgs", "waterdata",
           "usfs", "fia", "lcms", "fema", "nfhl", "__version__"]

if TYPE_CHECKING:  # so IDEs, mypy and pyright resolve the re-exports statically
    from . import esri, fema, usfs, usgs
    from .esri import edw, portal, services
    from .fema import nfhl
    from .usfs import fia, lcms
    from .usgs import waterdata

#: Names resolved on first access by :func:`__getattr__`, mapped to the
#: module they alias. The private transports (``esri._http``,
#: ``usgs._http``, ``usgs._ogc``) are deliberately absent - callers
#: who genuinely need one should reach it through the explicit dotted path so
#: its private status stays visible at the call site.
_LAZY = {
    "esri": ".esri",
    "edw": ".esri.edw",
    "portal": ".esri.portal",
    "services": ".esri.services",
    "usgs": ".usgs",
    "waterdata": ".usgs.waterdata",
    "usfs": ".usfs",
    "fia": ".usfs.fia",
    "lcms": ".usfs.lcms",
    "fema": ".fema",
    "nfhl": ".fema.nfhl",
    # Pre-0.5 names, kept importable (with a DeprecationWarning); not in __all__.
    "restesri": ".restesri",
    "restusgs": ".restusgs",
}


def __getattr__(name: str):
    """PEP 562 lazy attribute access for the re-exported provider modules.

    ``importlib.import_module`` returns the object already registered in
    ``sys.modules``, so ``georest.edw is georest.esri.edw``. That identity
    matters: the test suite patches the network layer with
    ``setattr(edw, ...)``, which would silently stop working against one
    import path if these were distinct module objects.
    """
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target, __name__)
    globals()[name] = module  # cache; __getattr__ won't fire again
    return module


def __dir__() -> list[str]:
    return sorted(__all__)
