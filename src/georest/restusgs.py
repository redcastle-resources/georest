"""Deprecated: renamed :mod:`georest.usgs` in georest 0.5.0.

``import georest.restusgs`` and everything under it keep working, as the same
module objects as ``georest.usgs``, with a DeprecationWarning. Import
``georest.usgs`` instead.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from ._renamed import alias

alias("georest.restusgs", "georest.usgs")
