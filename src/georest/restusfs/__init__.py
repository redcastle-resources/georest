"""Forest Service data services, made usable.

Wraps two public USDA Forest Service APIs that answer complementary
halves of the same question and look nothing alike:

**FIA** (Forest Inventory and Analysis) — a probability sample of forest
plots going back to 1984. Answers *what is in the forest, and how much,
with a standard error*. Its API exposes 752 estimate attributes, 96
grouping variables and 1,143 evaluations through one endpoint, and its
documentation page still says "under construction".

**LCMS** (Landscape Change Monitoring System) — wall-to-wall 30 m maps of
land cover, land use, and change from 1985 to 2025. Answers *what
changed, and where*. Small, clean API; almost nobody connects its output
to anything else.

Quick start::

    from georest import restusfs as fs

    fs.find_attributes("carbon")            # what can I estimate?
    fs.find_evaluations("Oregon")           # which inventory? -> wc=412022
    fs.estimate(wc=412022, snum=2,          # run it
                rselected="County code and name")

    fs.lcms_summary(state="Oregon", county="Crook")

Two things worth knowing before trusting a number out of here:

* **FIA estimates always carry their sampling error.** ``estimate()``
  returns ``se_pct`` and ``plots`` alongside every value and flags cells
  that are too thin to report. An FIA estimate without its error is not
  a fact — a real query for white/red/jack pine in Alabama returns
  15,748 acres with a **54.9% standard error from four plots**.
* **LCMS and FIA are not directly comparable.** One is a classified map,
  the other a probability sample. Comparing map-derived area to a
  design-based estimate conflates map accuracy with sampling error. This
  package makes the comparison easy and labels it; it will not hand you
  a single blended number that hides which is which.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from ._http import UpstreamError, UpstreamUnavailable, USFSError
from .align import (
    COMPARISON_CAVEATS,
    compare_area,
    fia_forest_area,
    lcms_tree_area,
    summarize_comparison,
)
from .fia import (
    FIAValidationError,
    estimate,
    reliable,
    validate,
)
from .lcms import (
    latest_release,
    lcms_classes,
    lcms_products,
    lcms_releases,
    lcms_summary,
    lcms_summary_areas,
    lcms_vis_params,
    release_products,
)
from .lcms_ee import (
    lcms_asset_id,
    lcms_class_properties,
    lcms_ee_collection,
)
from .vocab import (
    cache_dir,
    describe_grouping,
    find_attributes,
    find_evaluations,
    find_groupings,
    get_attribute,
    get_evaluation,
    grouping_labels,
    load_catalog,
    refresh_all,
)

__all__ = [
    # FIA discovery
    "find_attributes",
    "get_attribute",
    "find_groupings",
    "describe_grouping",
    "find_evaluations",
    "get_evaluation",
    "grouping_labels",
    "load_catalog",
    # FIA estimates
    "estimate",
    "validate",
    "reliable",
    "FIAValidationError",
    # LCMS
    "lcms_releases",
    "lcms_products",
    "lcms_classes",
    "lcms_summary_areas",
    "lcms_summary",
    "lcms_vis_params",
    "latest_release",
    "release_products",
    # FIA <-> LCMS comparison, labelled as not directly comparable.
    "COMPARISON_CAVEATS",
    "compare_area",
    "fia_forest_area",
    "lcms_tree_area",
    "summarize_comparison",
    # LCMS in Earth Engine (needs the optional 'ee' extra).
    "lcms_asset_id",
    "lcms_ee_collection",
    "lcms_class_properties",
    # Cache management
    "refresh_all",
    "cache_dir",
    # Errors
    "USFSError",
    "UpstreamError",
    "UpstreamUnavailable",
]

