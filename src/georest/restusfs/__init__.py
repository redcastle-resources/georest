"""georest.restusfs — clients for USDA Forest Service data APIs.

Two public Forest Service APIs that answer complementary halves of the
same question and look nothing alike:

**FIA** (Forest Inventory and Analysis): a probability sample of forest
plots going back to 1984. Answers *what is in the forest, and how much,
with a standard error*. Its API exposes 752 estimate attributes, 96
grouping variables and 1,143 evaluations through one endpoint.

**LCMS** (Landscape Change Monitoring System): wall-to-wall 30 m maps of
land cover, land use, and change from 1985 to 2025. Answers *what
changed, and where*.

Modules
-------
fia
    FIA estimates with their sampling errors, and the searches that find
    what to estimate (attributes, evaluations, groupings).
lcms
    LCMS summaries for the API's precomputed areas, with class tables
    and palettes from the API.
lcms_ee
    LCMS as Earth Engine image collections, for summaries over your own
    polygon. Needs the optional extra: ``pip install "georest[ee]"``.
align
    LCMS treed area beside FIA forest-land area, kept apart and labelled.
vocab
    The cached FIA catalogs behind ``fia``'s searches, and their refresh.

Quick start::

    from georest.restusfs import fia, lcms

    fia.find_attributes("carbon")                     # what can be estimated
    wc = fia.find_evaluations("Oregon")["wc"].iloc[0]  # which inventory
    fia.estimate(wc, 2, rselected="County code and name")

    lcms.lcms_summary("Land_Cover", state="Oregon", county="Crook")

Two things worth knowing before trusting a number out of here:

* **FIA estimates always carry their sampling error.** ``fia.estimate``
  returns ``se_pct`` and ``plots`` alongside every value and flags cells
  that are too thin to report. An FIA estimate without its error is not
  a fact: a real query for white/red/jack pine in Alabama returns
  15,748 acres with a **54.9% standard error from four plots**.
* **LCMS and FIA are not directly comparable.** One is a classified map,
  the other a probability sample. Comparing map-derived area to a
  design-based estimate conflates map accuracy with sampling error.
  ``align`` makes the comparison easy and labels it; it will not hand you
  a single blended number that hides which is which.

The error classes are shared by every module, so they are importable from
here: ``USFSError`` and its subclasses ``UpstreamError`` (the API refused
the request) and ``UpstreamUnavailable`` (it could not be reached).
``fia.FIAValidationError`` is a request caught locally before it was sent.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

from . import align, fia, lcms, lcms_ee, vocab
from ._http import UpstreamError, UpstreamUnavailable, USFSError

__all__ = [
    "fia", "lcms", "lcms_ee", "align", "vocab",
    "USFSError", "UpstreamError", "UpstreamUnavailable",
]
