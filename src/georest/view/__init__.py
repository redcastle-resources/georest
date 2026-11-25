"""geoView: georest's map viewer (the viewer geeViz calls Delta).

The viewer's files are in ``STATIC_DIR``: a page (``geoview.html``), its
scripts, styles and images. It reads a JSON map document and draws Earth
Engine (through geeViz), ArcGIS, GeoJSON and XYZ tile layers.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
