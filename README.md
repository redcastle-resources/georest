# georest

Stdlib-only Python clients for geospatial REST APIs — ArcGIS Image, Map and Feature
Services, the USFS Enterprise Data Warehouse (EDW), and ArcGIS Portal search.

**Zero dependencies.** Everything is `urllib` and `json` from the standard library.
Nothing to resolve, nothing to pin, nothing to break — it installs cleanly into an
ArcGIS Pro `arcgispro-py3` clone, a locked-down agency environment, or a bare
`python:slim` container.

```bash
pip install georest
```

## Quick start

```python
from georest.restesri import edw, portal, services

# Search the USFS EDW catalog by keyword or theme
hits = edw.search_edw_services("fire")

# Pull features as GeoJSON (layer 63 = "Burned Area Boundaries (All Years)")
geojson = edw.query_features(
    "EDW_MTBS_01", 63,
    where="fire_name LIKE '%CAMERON PEAK%'",
    out_fields="fire_name,acres,year",
)

# Search any ArcGIS Portal — IIPP, ArcGIS Online, USGS, NOAA, USFS, NASA,
# or any ArcGIS Enterprise install
results = portal.searchPortal("naip 2023", portal="agol")

# Generic ArcGIS service operations
tile_url = services.getImageServiceTileUrl("https://.../ImageServer")
```

The three modules are also re-exported at the top level, so `from georest import edw`
works too. They load lazily, so `import georest` costs almost nothing.

## Layout

| Import path | What it covers |
|---|---|
| `georest.restesri.edw` | USFS EDW: catalog search, layer-role tagging, FGDC/ISO metadata, feature and analytic queries |
| `georest.restesri.portal` | ArcGIS Portal search and service metadata |
| `georest.restesri.services` | Generic Image/Map/Feature Service queries and raster operations |

`restesri` is the Esri/ArcGIS provider. It sits under `georest` as a subpackage so
other REST providers can be added alongside it without a namespace collision.

Full per-function reference: **[docs/api.md](docs/api.md)**.
Runnable notebooks: **[examples/](examples/)**.

## Maintenance tool

```bash
georest-check-themes          # report drift vs. the live EDW catalog
georest-check-themes --emit   # + paste-ready _SERVICE_THEMES entries
```

`edw._SERVICE_THEMES` is hand-curated and powers theme filtering and description
matching. EDW publishes and retires services without notice, so the table rots
quietly — an untyped service still appears in search results, but as theme
`uncategorized` with no description for keyword matching to reach. Exit status is
0 in sync, 1 drifted, 2 unreachable.

## Migrating from RESTesri

This package was previously a loose `RESTesri/` directory that worked only when the
repository root happened to be on `sys.path`. Update imports:

```python
from RESTesri import edw                    # before
from georest.restesri import edw            # after

from RESTesri._http import fetch_json       # before
from georest.restesri._http import fetch_json   # after
```

Then delete any `sys.path.insert(...)` bootstrap — `pip install georest` (or
`pip install -e .` from a clone) makes it unnecessary.

## Development

```bash
git clone https://github.com/redcastle-resources/georest
cd georest
pip install -e ".[dev]"

python -m pytest                              # or: python -m unittest discover -s tests -t .
EDW_LIVE=1 SERVICES_LIVE=1 python -m pytest   # include the network tests
```

This is a `src/` layout, so `pip install -e .` is required before the tests can
import the package — that is deliberate. It makes the suite import the installed
artifact rather than the source directory, so a packaging mistake fails loudly
instead of being masked by the working directory.

Live tests skip rather than fail when the servers are unreachable; EDW
intermittently answers 404, 500, `Layer not found`, or 200-with-no-layers for
services that work moments later. See [tests/README.md](tests/README.md) and
[CONTRIBUTING.md](CONTRIBUTING.md).

## Requirements

Python 3.9 or newer. The 3.9 floor is deliberate: ArcGIS Pro 3.0–3.2 ships Python
3.9, and that is squarely this library's audience.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

`portal.py` and `services.py` were ported from
[geeViz](https://github.com/gee-community/geeViz)'s `esriLib.py`, with the
viewer-rendering functions removed; these modules fetch data rather than render it.
