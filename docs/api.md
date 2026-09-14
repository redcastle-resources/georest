# georest.restesri — API reference

A set of Python tools that streamline interacting with ESRI Map, Feature and Image services.

Contribution guidelines live in [CONTRIBUTING.md](../CONTRIBUTING.md); installation and
a quick start are in the [README](../README.md).

## Modules

Stdlib-only Python clients for ArcGIS REST services (Image/Map/Feature Services, EDW, and Portal search).

All four modules live under `georest.restesri`:

```python
from georest.restesri import edw, portal, services
from georest import edw                      # equivalent top-level re-export
```

### `services.py` — generic ArcGIS service queries

Tile URLs, feature queries, and Image Service raster operations (export, statistics, pixel identify/sampling, boundary, supported-operations discovery) for any Image/Map/Feature Service.

- `getImageServiceTileUrl(url_or_result, token=None)` — build the `{z}/{y}/{x}` tile URL template for an Image Service or cached Map Service. Pure string construction, so it cannot tell whether the service is actually tiled: an **uncached** service yields a well-formed template whose tiles all answer 404. Check for `tileInfo` in the service's `?f=json` first; render uncached Image Services with `exportImage` instead.
- `queryFeatureServiceCount(url_or_result, where="1=1", geometry=None, ..., timeout=None)` — return the count of features matching a where clause and/or spatial filter.
- `queryFeatureService(url_or_result, where="1=1", geometry=None, max_features=1000, ..., timeout=None)` — fetch features as GeoJSON, with a pre-flight count check against `max_features` to avoid huge payloads. Falls back to `f=json` + client-side GeoJSON conversion for services that reject `f=geojson` outright (e.g. an ImageServer's native raster-catalog `/query`).
- `getLayerInfo(url_or_result, token=None, timeout=None)` — get layer metadata: fields, geometry type, extent, capabilities. Raises a clear error naming the real sub-layers if pointed at a container rather than a queryable layer — either a group layer (`subLayers`) or a MapServer/FeatureServer **service root** (`layers` and no `fields`). An ImageServer root stays a valid target: it carries its mosaic catalog's own fields.
- `getSupportedOperations(url_or_result, token=None, timeout=None)` — **added 2026-08-19.** List every REST operation a service or layer actually exposes, parsed from the HTML browse page — the only place ArcGIS Server publishes the full list (the JSON `capabilities` string is an incomplete substitute; a service can expose e.g. `computeStatisticsHistograms` with no corresponding capability flag at all).
- `exportImage(url_or_result, bbox, size=(512, 512), image_format="tiff", mosaic_rule=None, rendering_rule=None, ..., timeout=None)` — **added 2026-08-19.** Export a rendered image from an ArcGIS Image Service, with support for mosaic/rendering rules, pixel type, NoData, and resampling. Returns raw bytes, or writes to `out_path` if given. Depends on the `fetch_bytes` helper in `_http.py` (see below).
- `computeStatisticsHistograms(url_or_result, geometry, geometry_type="esriGeometryPolygon", mosaic_rule=None, rendering_rule=None, pixel_size=None, ..., timeout=None)` — **added 2026-08-19.** Per-band pixel statistics (min/max/mean/stddev/median/mode/count) and a value histogram over an area of an Image Service, merged into one result per band from a single request. At native resolution, a high-resolution service (e.g. sub-meter imagery) can exceed a server-side size limit over any non-trivial area — pass a coarser `pixel_size` to avoid that.
- `identifyPixelValue(url_or_result, x, y, mosaic_rule=None, rendering_rule=None, return_catalog_items=False, ..., timeout=None)` — **added 2026-08-19.** Identify the pixel value at a single point, plus per-band/per-raster values. Point-only by design — the underlying `identify` operation silently collapses polygon input to its centroid rather than sampling an area; use `computeStatisticsHistograms` for area-wide distribution instead.
- `getSamples(url_or_result, points, mosaic_rule=None, pixel_size=None, ..., timeout=None)` — **added 2026-08-19.** Sample pixel values at multiple points in one batch call, with an automatic per-point fallback to `identifyPixelValue` for any points the batch call can't resolve — verified against a live service, `getSamples` can fail outright (400, sometimes 500) for a point landing on NoData, or even silently drop points from its response, while `identify` handles the identical location cleanly.
- `queryBoundary(url_or_result, mosaic_rule=None, out_sr=4326, ..., timeout=None)` — **added 2026-08-19.** Get an Image Service's true coverage boundary (not just its rectangular `extent`) — useful as a pre-flight check before spending an `exportImage`/`computeStatisticsHistograms` call on an AOI the service doesn't actually cover.

Every function above accepts an optional `timeout` (seconds; default 60, set in `_http.py`). Some Image Services with large or complex mosaic catalogs (hundreds of contributing rasters) can genuinely take 30–45+ seconds to respond even for a simple result — pass a larger value rather than assuming a hang.

**Error contract.** These functions raise `RuntimeError` for transport failures (unreachable host, HTTP error status) and `ValueError` when a service answers 200 with an Esri error object, or with a body that isn't JSON. They do **not** raise `ConnectionError` — earlier docstrings promised it, but `_http` converts `URLError` to `RuntimeError` before it can be caught, so no caller could ever have received one. The single exception to raising at all is `getSamples`, which reports a point it cannot sample in-band via that entry's `source` field so the remaining points still return values.

### `edw.py` — USFS Enterprise Data Warehouse (EDW) client

Search, metadata, and spatial queries against `https://apps.fs.usda.gov/arcx/rest/services/EDW` only. This is a single flat catalog, not a portal — for portal search (IIPP, ArcGIS Online, etc.) see `portal.py` below.

- `search_edw_services(query="", theme="")` — search the EDW catalog (and only the EDW catalog) by keyword and/or theme (biota, boundaries, environment, geoscientific, inland_waters, planning_cadastre, structure, transportation), using substring, theme, and keyword-alias matching (e.g. "riparian" → inland waters services). To search IIPP or another portal instead, use `portal.searchPortal(query, portal="iipp")`.
- `get_service_info(service_name)` — metadata for an EDW MapServer service: description, spatial reference, layer list.
- `get_layer_roles(service_name)` — every layer tagged with its cartographic `role`: `group` (a container, not queryable), `detail` (full resolution), `coarse` (a small-scale duplicate superseded by a `detail` sibling), or `standalone`. Many EDW services publish a theme at several resolutions; querying the coarse one can silently return generalized geometry. Nothing is dropped, and `groupKey` shows why two layers were treated as versions of each other.
- `get_detail_layers(service_name)` — convenience filter over `get_layer_roles`: just the `detail` and `standalone` layers, i.e. the ones you normally want to query.
- `get_layer_info(service_name, layer_id)` — layer metadata: fields, geometry type, extent, capabilities.
- `get_layer_metadata(service_name, layer_id, include_domains=False)` — parse a layer's FGDC/ISO metadata XML for abstract, purpose, keywords, and per-field definitions (and coded-value/range domains if requested).
- `query_features(service_name, layer_id, geometry=None, where="1=1", max_features=1000, ...)` — query features as GeoJSON, with an automatic object-ID fallback for layers that silently drop features on complex spatial queries.
- `query_features_analytic(service_name, layer_id, out_analytics, partition_by=None, ...)` — run SQL window-function analytics (RANK, SUM, LAG/LEAD, etc.) via the `queryAnalytic` operation.
- `top_n_per_group(service_name, layer_id, field, group_by, n=1, ...)` — convenience wrapper over `query_features_analytic` to get the top N features by a field within each group.
- `query_features_with_pagination(service_name, layer_id, max_features=5000, ...)` — like `query_features` but pages past the server's per-request cap, using the layer's own `maxRecordCount` as the page size.

### `portal.py` — ArcGIS Portal search and service metadata

Search any ArcGIS Portal (IIPP, ArcGIS Online, USGS, NOAA, USFS, NASA, or a custom Enterprise portal) and inspect services.

- `searchPortal(query, portal="iipp", limit=20, data_only=True, raw_q=None, token=None, org_scoped=None, **filters)` — free-text search against `/sharing/rest/search`, with a bundled exclusion list to filter out non-data items (styles, dashboards, web apps, etc.). An empty list means the search genuinely matched nothing: a portal error (notably code 498, an invalid or expired token) raises `ValueError` rather than being swallowed into `[]`.
  - **Organization scoping.** Searching an ArcGIS Online organization URL (`https://<org>.maps.arcgis.com`) searches *all* of ArcGIS Online unless the query names the organization, so by default `searchPortal` adds an `orgid:` clause for those URLs, including the built-in `portal="nasa"`. This costs one `/sharing/rest/portals/self` request per portal and token, cached for the process. If the organization id can't be resolved it raises `RuntimeError` rather than falling back to a global search. `org_scoped=False` turns scoping off; `org_scoped=True` forces it on for Enterprise portals, vanity domains, or `raw_q`, which isn't scoped automatically.
  - When scoping, parentheses and quotes that would break the `orgid:` clause are dropped from a free-text `query`, which is then searched as plain words; a `raw_q` that would break it raises `ValueError` instead.
  - `q`, `num` and `f` are set by `searchPortal` itself and raise `TypeError` if passed in `**filters`; use `raw_q` and `limit` instead.
- `getServiceMetadata(url, token=None)` — fetch `?f=json` metadata for any ArcGIS service or sub-layer URL. Raises `ValueError` if the service answers with an Esri error object — which is how ArcGIS reports a missing service, rather than with an HTTP 404.
- `PORTALS` — module-level dict of short portal names to base URLs (extend at runtime by adding keys).

### `_http.py` — shared HTTP helpers

Stdlib-only (`urllib`) GET/POST/JSON helpers used by all the modules above.

- `fetch_json(url, params=None, timeout=None)` — GET and parse JSON.
- `fetch_text(url, params=None, timeout=None)` — GET and return raw text (for XML/HTML endpoints like EDW's `/metadata` or an ArcGIS Server HTML browse page).
- `fetch_bytes(url, params=None, timeout=None)` — **added 2026-08-19.** GET and return `(raw bytes, content-type)`, for binary endpoints like `exportImage` where a UTF-8 decode would corrupt the response.
- `post_json(url, params, timeout=None)` — POST form-encoded params and parse JSON (used for large query payloads).
- `build_params(base, token)` — merge an optional token into a params dict.
- `format_esri_error(err)` — render an Esri error object (`code`, `message`, `details`) into one diagnostic string. Lives here so `services.py` and `portal.py` can share it without a circular import; `services._format_esri_error` remains as an alias.

Default request timeout is 60 seconds (`_TIMEOUT`) — every fetch function above accepts an optional `timeout` override for services that legitimately need longer.

Errors surface two ways: `RuntimeError` for HTTP and URL failures, and `ValueError` when a response won't parse as JSON — which is what a degraded HTML error page served with status 200 looks like. Callers retrying around a flaky server need to catch both. `services.py` and `portal.py` follow the same contract and add one more case: a service that answers **200 with an Esri error object** (a missing service, a bad `where` clause, an expired token) raises `ValueError` too. None of these modules raise `ConnectionError`, despite what earlier docstrings claimed.

## Tests

`pip install -e ".[dev]"` first — this is a `src/` layout, so the package is not importable from the repository root without it. Then `python -m pytest`, or `python -m unittest discover -s tests -t .` for the stdlib runner; the tests are `unittest.TestCase` classes either way. Offline and deterministic by default; set `EDW_LIVE=1` to include tests that hit the real EDW server, and `SERVICES_LIVE=1` for the `services.py` tests that hit public ArcGIS hosts. See [tests/README.md](../tests/README.md).

`georest-check-themes` reports drift between the live catalog and the hand-curated `_SERVICE_THEMES` table in `edw.py` (`--emit` prints paste-ready entries).
