"""Deprecated: ``georest.esri.edw`` is now :mod:`georest.usfs.edw`.

EDW is the Forest Service's data, so it lives with ``georest.usfs`` (FIA,
LCMS) since georest 0.5.2, built on ``georest.esri.services``. This name
still imports that same module object, so existing code keeps working, and
warns once. Import ``from georest.usfs import edw`` instead.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

import sys
import warnings

from ..usfs import edw as _edw

warnings.warn(
    "georest.esri.edw moved to georest.usfs.edw in georest 0.5.2; import "
    "georest.usfs.edw instead. The old name keeps working for now and will "
    "be removed.",
    DeprecationWarning, stacklevel=2,
)

# The same module object under both names, so patching and identity checks
# behave identically whichever name the caller used.
sys.modules[__name__] = _edw
