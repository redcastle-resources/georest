"""georest — stdlib-only Python clients for geospatial REST APIs.

Subpackages
-----------
restesri
    ArcGIS / Esri REST services: Image, Map and Feature Services, the USFS
    Enterprise Data Warehouse (EDW), and ArcGIS Portal search.
restusgs
    USGS APIs: the Water Data OGC API (monitoring locations, daily and
    continuous values, field measurements, peaks).

Quick start::

    from georest.restesri import edw, portal, services
    from georest.restusgs import waterdata

    hits = edw.search_edw_services("fire")
    meta = portal.getServiceMetadata("https://.../ImageServer")
    tiles = services.getImageServiceTileUrl("https://.../ImageServer")
    flow = waterdata.get_daily_values("USGS-09380000", "00060", start="2024-01-01")

The public provider modules are also re-exported here for convenience. They
are imported lazily (PEP 562), so ``import georest`` itself costs almost
nothing::

    from georest import edw          # the same module object as
                                     # georest.restesri.edw
    from georest import waterdata    # georest.restusgs.waterdata

No third-party dependencies — everything is stdlib ``urllib``.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.3.0"

__all__ = ["restesri", "edw", "portal", "services", "restusgs", "waterdata", "__version__"]

if TYPE_CHECKING:  # so IDEs, mypy and pyright resolve the re-exports statically
    from . import restesri, restusgs
    from .restesri import edw, portal, services
    from .restusgs import waterdata

#: Names resolved on first access by :func:`__getattr__`, mapped to the
#: module they alias. The private transports (``restesri._http``,
#: ``restusgs._http``, ``restusgs._ogc``) are deliberately absent — callers
#: who genuinely need one should reach it through the explicit dotted path so
#: its private status stays visible at the call site.
_LAZY = {
    "restesri": ".restesri",
    "edw": ".restesri.edw",
    "portal": ".restesri.portal",
    "services": ".restesri.services",
    "restusgs": ".restusgs",
    "waterdata": ".restusgs.waterdata",
}


def __getattr__(name: str):
    """PEP 562 lazy attribute access for the re-exported provider modules.

    ``importlib.import_module`` returns the object already registered in
    ``sys.modules``, so ``georest.edw is georest.restesri.edw``. That identity
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
