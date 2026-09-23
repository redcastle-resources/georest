"""georest.restusgs — stdlib-only clients for USGS REST/OGC APIs.

The first (and so far only) module is :mod:`waterdata`, a client for the
USGS Water Data OGC API (``https://api.waterdata.usgs.gov/ogcapi/``):
monitoring locations, daily and continuous values, field measurements,
annual peaks, and the metadata collections that describe them.

``restusgs`` is the USGS provider. It sits under ``georest`` beside
``restesri`` so providers can be added without a namespace collision, and
it is self-contained: its private ``_http`` and ``_ogc`` modules are its own.

Quick start::

    from georest.restusgs import waterdata

    fc = waterdata.get_daily_values("USGS-09380000", "00060",
                                    start="2024-01-01", end="2024-01-31")
    rows = waterdata.to_rows(fc)

``_http`` and ``_ogc`` are not part of the public API.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import waterdata

__all__ = ["waterdata"]
