"""Private: keep the pre-0.5 subpackage names importable after the rename.

``georest.restesri`` became ``georest.esri`` and ``georest.restusgs`` became
``georest.usgs`` in 0.5.0. The old names shipped in 0.4.0, and code that
uses them, including other packages that import georest, keeps working:
each old name, and every module under it, resolves to the very same module
object as its new name, so patching or identity checks behave identically.
Importing an old name warns once.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import warnings


def alias(old: str, new: str) -> None:
    """Point package ``old`` and all its submodules at package ``new``.

    Registers ``old.<sub>`` in ``sys.modules`` for every module under
    ``new`` (private ones too: ``_http`` was imported by name downstream),
    so ``import old.sub`` and ``from old.sub import x`` find the new module
    without ever loading a second copy.
    """
    warnings.warn(
        f"{old} was renamed {new} in georest 0.5.0; import {new} instead. "
        f"The old name keeps working for now and will be removed.",
        DeprecationWarning, stacklevel=3,
    )
    package = importlib.import_module(new)
    sys.modules[old] = package
    for info in pkgutil.walk_packages(package.__path__, prefix=new + "."):
        if ".tests" in info.name:
            continue
        module = importlib.import_module(info.name)
        sys.modules[old + info.name[len(new):]] = module
