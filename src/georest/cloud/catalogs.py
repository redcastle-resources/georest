"""A curated registry of STAC catalogs, and the collections in them that matter.

Other STAC clients know the protocol; none of them knows which catalog has
what, or whether a scene's files can be read once you have found it. That is
what this registry records, checked against the live servers::

    from georest.cloud import catalogs

    catalogs.CATALOGS["planetary-computer"]["url"]
    catalogs.resolve("earth-search")["title"]

How a catalog's files can be read (the ``assets`` value) is the thing most
often learned by trial and error:

``open``
    An ordinary https link; anyone can read it.
``sign``
    Planetary Computer: a search is open, but a file refuses to download
    until its link is signed with a short-lived token.
``earthdata``
    NASA: a free Earthdata Login token is needed to download.
``requester-pays``
    An ``s3://`` link in a bucket where the reader pays the transfer cost,
    so it needs AWS credentials.

``tests/test_live_stac.py`` checks every collection listed here still exists.
When a catalog renames or retires one, that test fails; correct the entry.

Copyright 2026 Ryan Rock and Ian Housman

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

#: What each ``assets`` value means, for the person who gets a 403.
ASSET_ACCESS = {
    "open": "Plain https links that anyone can read.",
    "sign": "Links must be signed with a short-lived token before they download "
            "(Planetary Computer).",
    "earthdata": "Downloads need a free NASA Earthdata Login token.",
    "requester-pays": "s3:// links in a requester-pays bucket; reading needs AWS "
                      "credentials, and the reader pays the transfer cost.",
}

#: Catalog name -> entry. ``collections`` maps a collection id to its note and,
#: where it differs from the catalog's default, how its files are read.
CATALOGS: dict[str, dict] = {
    "earth-search": {
        "title": "Earth Search (Element 84)",
        "url": "https://earth-search.aws.element84.com/v1",
        "assets": "open",
        "notes": "Open AWS catalog. Sentinel-2 is plain https; Landsat and NAIP are "
                 "s3:// links in requester-pays buckets.",
        "collections": {
            "sentinel-2-l2a": {
                "note": "Sentinel-2 surface reflectance, 10 to 60 m. Cloud-optimized "
                        "GeoTIFFs. Cloud cover is eo:cloud_cover.",
            },
            "sentinel-2-c1-l2a": {
                "note": "Sentinel-2 Collection 1: the archive reprocessed to one "
                        "processing baseline, better for time series.",
            },
            "landsat-c2-l2": {
                "note": "Landsat Collection 2 Level-2 surface reflectance and surface "
                        "temperature, 30 m.",
                "assets": "requester-pays",
            },
            "naip": {
                "note": "NAIP aerial imagery, about 0.3 to 1 m, United States only, "
                        "re-flown state by state every few years.",
                "assets": "requester-pays",
            },
        },
    },
    "planetary-computer": {
        "title": "Microsoft Planetary Computer",
        "url": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "assets": "sign",
        "notes": "Searching is open, but every file link must be signed first "
                 "(stac.sign). Carries Forest Service data (FIA) and burn "
                 "severity (MTBS) that no other catalog here has.",
        "collections": {
            "sentinel-2-l2a": {"note": "Sentinel-2 surface reflectance, 10 to 60 m."},
            "landsat-c2-l2": {"note": "Landsat Collection 2 Level-2 surface reflectance "
                                      "and surface temperature, 30 m."},
            "naip": {"note": "NAIP aerial imagery, United States only; the archive here "
                             "runs 2010 to 2023."},
            "hls2-l30": {"note": "Harmonized Landsat Sentinel-2, Landsat side, 30 m, "
                                 "from 2020. Landsat and Sentinel-2 on one grid."},
            "hls2-s30": {"note": "Harmonized Landsat Sentinel-2, Sentinel-2 side, 30 m, "
                                 "from 2020."},
            "mtbs": {"note": "Monitoring Trends in Burn Severity: fire severity "
                             "mosaics, 1984 to 2018."},
            "fia": {"note": "Forest Inventory and Analysis: forest location, health, "
                            "growth, mortality and production, from 2020. For "
                            "estimates with sampling errors, use georest.usfs.fia."},
            "usgs-lcmap-conus-v13": {"note": "USGS LCMAP land cover and change, "
                                             "conterminous United States, 1985 to 2021."},
            "3dep-seamless": {"note": "USGS 3DEP seamless elevation, 1 to 60 m."},
            "3dep-lidar-copc": {"note": "USGS 3DEP lidar point clouds in COPC form, "
                                        "2012 to 2022."},
        },
    },
    "landsatlook": {
        "title": "USGS LandsatLook",
        "url": "https://landsatlook.usgs.gov/stac-server",
        "assets": "open",
        "notes": "The USGS's own Landsat catalog; links are plain https. Also has "
                 "Level-3 products (burned area, snow, surface water).",
        "collections": {
            "landsat-c2l2-sr": {"note": "Landsat Collection 2 Level-2 surface "
                                        "reflectance, 30 m."},
            "landsat-c2l2-st": {"note": "Landsat Collection 2 Level-2 surface "
                                        "temperature, 30 m."},
            "landsat-c2l1": {"note": "Landsat Collection 2 Level-1, top-of-atmosphere."},
            "landsat-c2l3-ba": {"note": "Landsat Level-3 burned area."},
            "landsat-c2l3-fsca": {"note": "Landsat Level-3 fractional snow-covered area."},
            "landsat-c2l3-dswe": {"note": "Landsat Level-3 dynamic surface water extent."},
        },
    },
    "nasa-lpcloud": {
        "title": "NASA CMR-STAC, LP DAAC (LPCLOUD)",
        "url": "https://cmr.earthdata.nasa.gov/cloudstac/LPCLOUD",
        "assets": "earthdata",
        "notes": "NASA's land data center. Searching is open; downloads need an "
                 "Earthdata Login token (EARTHDATA_TOKEN). Collection ids carry a "
                 "version, like HLSS30_2.0.",
        "collections": {
            "HLSS30_2.0": {"note": "Harmonized Landsat Sentinel-2, Sentinel-2 side, "
                                   "30 m, 2-3 day revisit."},
            "HLSL30_2.0": {"note": "Harmonized Landsat Sentinel-2, Landsat side, 30 m."},
            "GEDI02_A_003": {"note": "GEDI L2A lidar footprints: canopy height and "
                                     "elevation metrics. HDF5 granules, not rasters."},
            "ECO_L2T_LSTE_002": {"note": "ECOSTRESS land surface temperature, 70 m."},
        },
    },
    "nasa-ornl-cloud": {
        "title": "NASA CMR-STAC, ORNL DAAC (ORNL_CLOUD)",
        "url": "https://cmr.earthdata.nasa.gov/cloudstac/ORNL_CLOUD",
        "assets": "earthdata",
        "notes": "NASA's biogeochemical data center: GEDI biomass, Daymet climate, "
                 "ABoVE and fire products. Downloads need an Earthdata Login token.",
        "collections": {
            "GEDI_L4A_AGB_Density_V2_1_2056_2.1": {
                "note": "GEDI L4A aboveground biomass density, footprint level, v2.1."},
            "GEDI_L4A_AGB_Density_V3_2508_3": {
                "note": "GEDI L4A aboveground biomass density, footprint level, v3."},
            "Daymet_Daily_V4R1_2129_4.1": {
                "note": "Daymet daily weather for North America on a 1 km grid."},
            "CMS_Global_Fire_Atlas_1642_1": {
                "note": "Global fire atlas: individual fire perimeters and progression."},
            "ABoVE_ForestDisturbance_Agents_1924_1": {
                "note": "Forest disturbance agents (fire, harvest, insects) across the "
                        "ABoVE region of Alaska and western Canada."},
        },
    },
}


def names() -> list[str]:
    """The registered catalog names."""
    return list(CATALOGS)


def resolve(catalog: str) -> dict:
    """The registry entry for a catalog name, or a bare entry for a URL.

    Args:
        catalog: A name from :data:`CATALOGS` ("earth-search"), or the root
            URL of any STAC API (``https://...``), which is used as given.

    Returns:
        A dict with ``name``, ``title``, ``url``, ``assets``, ``notes`` and
        ``collections``. A URL that is not registered has ``assets`` set to
        ``"unknown"``.

    Raises:
        ValueError: If ``catalog`` is neither a registered name nor an
            http(s) URL. The message lists the names.
    """
    key = str(catalog or "").strip()
    if key in CATALOGS:
        return {"name": key, **CATALOGS[key]}
    if key.lower().startswith(("https://", "http://")):
        url = key.rstrip("/")
        for name, entry in CATALOGS.items():
            if entry["url"].rstrip("/") == url:
                return {"name": name, **entry}
        return {"name": url, "title": url, "url": url, "assets": "unknown",
                "notes": "Not in the georest registry; how its files are read is not known.",
                "collections": {}}
    raise ValueError(f"unknown catalog {catalog!r}; use a name ({', '.join(CATALOGS)}) "
                     f"or the root URL of a STAC API")
