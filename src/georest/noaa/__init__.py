"""georest.noaa - clients for NOAA APIs.

Modules
-------
nws
    The National Weather Service API: active watches, warnings and
    advisories (at a point, in a state, or nationwide), the forecast at a
    point, and the latest observations from the nearest stations - in one
    unit system per call.
nwps
    The National Water Prediction Service: river forecast gauges, their
    stage and flood category now, the forecast crest, and the stage at
    which each gauge floods.
coops
    CO-OPS Tides and Currents: tide stations, measured water levels, and
    tide predictions.
nhc
    The National Hurricane Center: the tropical cyclones it is advising on,
    with position, strength, motion and links to the forecast cone.

``noaa`` is the NOAA provider, beside ``esri``, ``usgs`` and ``usfs``.
It returns pandas DataFrames for tables, and is self-contained:
its private ``_http`` and ``_frames`` modules are its own.

Quick start::

    from georest.noaa import nws, nwps

    nws.alerts(point=(-95.37, 29.76))
    nws.forecast(-95.37, 29.76)
    nwps.gauges((-95.9, 29.4, -95.0, 30.2), flooding_only=True)

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import coops, nhc, nwps, nws

__all__ = ["nws", "nwps", "coops", "nhc"]
