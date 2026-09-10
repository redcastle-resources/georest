"""georest — stdlib-only Python clients for geospatial REST APIs.

Subpackages
-----------
restesri
    ArcGIS / Esri REST services: Image, Map and Feature Services, the USFS
    Enterprise Data Warehouse (EDW), and ArcGIS Portal search.

Quick start::

    from georest.restesri import edw, portal, services

    hits = edw.search_edw_services("fire")
    meta = portal.getServiceMetadata("https://.../ImageServer")
    tiles = services.getImageServiceTileUrl("https://.../ImageServer")

The three public restesri modules are also re-exported here for convenience.
They are imported lazily (PEP 562), so ``import georest`` itself costs almost
nothing::

    from georest import edw          # the same module object as
                                     # georest.restesri.edw

No third-party dependencies — everything is stdlib ``urllib``.

Copyright 2026 Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.1.0"

__all__ = ["restesri", "edw", "portal", "services", "__version__"]

if TYPE_CHECKING:  # so IDEs, mypy and pyright resolve the re-exports statically
    from . import restesri
    from .restesri import edw, portal, services

#: Names resolved on first access by :func:`__getattr__`. ``_http`` is
#: deliberately absent — it is private, and callers who genuinely need it
#: should reach it through the explicit ``georest.restesri._http`` path so
#: its private status stays visible at the call site.
_LAZY = frozenset({"restesri", "edw", "portal", "services"})


def __getattr__(name: str):
    """PEP 562 lazy attribute access for the re-exported restesri modules.

    ``importlib.import_module`` returns the object already registered in
    ``sys.modules``, so ``georest.edw is georest.restesri.edw``. That identity
    matters: the test suite patches the network layer with
    ``setattr(edw, ...)``, which would silently stop working against one
    import path if these were distinct module objects.
    """
    if name in _LAZY:
        target = ".restesri" if name == "restesri" else f".restesri.{name}"
        module = importlib.import_module(target, __name__)
        globals()[name] = module  # cache; __getattr__ won't fire again
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
