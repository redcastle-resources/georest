"""georest.mcp - georest as tools for an LLM agent, over the Model Context Protocol.

``georest-mcp`` (or ``python -m georest.mcp``) starts a stdio MCP server whose
tools are georest's data clients: FIA estimates and LCMS summaries (usfs);
USGS water data, the river network, elevation, earthquakes, ScienceBase and
water quality (usgs); and EDW, ArcGIS portal and Feature Service queries
(esri). Data tools only: there is no code execution.

Install with the optional extra: ``pip install "georest[mcp]"``.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""
