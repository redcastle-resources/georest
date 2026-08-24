# RESTesri
A set of Python tools that streamline interacting with ESRI Map, Feature and Image services. 

## Collaboration
1. DESCRIBE THE GUIDELINES FOR COLLABORATION AND BEST PRACTICES

## Modules

Stdlib-only Python clients for ArcGIS REST services (Image/Map/Feature Services, EDW, and Portal search).

### `services.py` — generic ArcGIS service queries

Tile URLs, feature queries, and Image Service raster operations (export, statistics, pixel identify/sampling, boundary, supported-operations discovery) for any Image/Map/Feature Service.

- `getImageServiceTileUrl(url_or_result, token=None)` — build the `{z}/{y}/{x}` tile URL template for an Image Service or cached Map Service.
- `queryFeatureServiceCount(url_or_result, where="1=1", geometry=None, ..., timeout=None)` — return the count of features matching a where clause and/or spatial filter.
- `queryFeatureService(url_or_result, where="1=1", geometry=None, max_features=1000, ..., timeout=None)` — fetch features as GeoJSON, with a pre-flight count check against `max_features` to avoid huge payloads. Falls back to `f=json` + client-side GeoJSON conversion for services that reject `f=geojson` outright (e.g. an ImageServer's native raster-catalog `/query`).
- `getLayerInfo(url_or_result, token=None, timeout=None)` — get layer metadata: fields, geometry type, extent, capabilities. Raises a clear error naming the real sub-layers if pointed at a mosaic/group container layer instead of a queryable layer.
- `getSupportedOperations(url_or_result, token=None, timeout=None)` — **added 2026-08-19.** List every REST operation a service or layer actually exposes, parsed from the HTML browse page — the only place ArcGIS Server publishes the full list (the JSON `capabilities` string is an incomplete substitute; a service can expose e.g. `computeStatisticsHistograms` with no corresponding capability flag at all).
- `exportImage(url_or_result, bbox, size=(512, 512), image_format="tiff", mosaic_rule=None, rendering_rule=None, ..., timeout=None)` — **added 2026-08-19.** Export a rendered image from an ArcGIS Image Service, with support for mosaic/rendering rules, pixel type, NoData, and resampling. Returns raw bytes, or writes to `out_path` if given. Depends on the `fetch_bytes` helper in `_http.py` (see below).
- `computeStatisticsHistograms(url_or_result, geometry, geometry_type="esriGeometryPolygon", mosaic_rule=None, rendering_rule=None, pixel_size=None, ..., timeout=None)` — **added 2026-08-19.** Per-band pixel statistics (min/max/mean/stddev/median/mode/count) and a value histogram over an area of an Image Service, merged into one result per band from a single request. At native resolution, a high-resolution service (e.g. sub-meter imagery) can exceed a server-side size limit over any non-trivial area — pass a coarser `pixel_size` to avoid that.
- `identifyPixelValue(url_or_result, x, y, mosaic_rule=None, rendering_rule=None, return_catalog_items=False, ..., timeout=None)` — **added 2026-08-19.** Identify the pixel value at a single point, plus per-band/per-raster values. Point-only by design — the underlying `identify` operation silently collapses polygon input to its centroid rather than sampling an area; use `computeStatisticsHistograms` for area-wide distribution instead.
- `getSamples(url_or_result, points, mosaic_rule=None, pixel_size=None, ..., timeout=None)` — **added 2026-08-19.** Sample pixel values at multiple points in one batch call, with an automatic per-point fallback to `identifyPixelValue` for any points the batch call can't resolve — verified against a live service, `getSamples` can fail outright (400, sometimes 500) for a point landing on NoData, or even silently drop points from its response, while `identify` handles the identical location cleanly.
- `queryBoundary(url_or_result, mosaic_rule=None, out_sr=4326, ..., timeout=None)` — **added 2026-08-19.** Get an Image Service's true coverage boundary (not just its rectangular `extent`) — useful as a pre-flight check before spending an `exportImage`/`computeStatisticsHistograms` call on an AOI the service doesn't actually cover.

Every function above accepts an optional `timeout` (seconds; default 60, set in `_http.py`). Some Image Services with large or complex mosaic catalogs (hundreds of contributing rasters) can genuinely take 30–45+ seconds to respond even for a simple result — pass a larger value rather than assuming a hang.

### `edw.py` — USFS Enterprise Data Warehouse (EDW) client

Search, metadata, and spatial queries against `https://apps.fs.usda.gov/arcx/rest/services/EDW` only. This is a single flat catalog, not a portal — for portal search (IIPP, ArcGIS Online, etc.) see `portal.py` below.

- `search_edw_services(query="", theme="")` — search the EDW catalog (and only the EDW catalog) by keyword and/or theme (biota, boundaries, environment, geoscientific, inland_waters, planning_cadastre, structure, transportation), using substring, theme, and keyword-alias matching (e.g. "riparian" → inland waters services). To search IIPP or another portal instead, use `portal.searchPortal(query, portal="iipp")`.
- `get_service_info(service_name)` — metadata for an EDW MapServer service: description, spatial reference, layer list.
- `get_layer_info(service_name, layer_id)` — layer metadata: fields, geometry type, extent, capabilities.
- `get_layer_metadata(service_name, layer_id, include_domains=False)` — parse a layer's FGDC/ISO metadata XML for abstract, purpose, keywords, and per-field definitions (and coded-value/range domains if requested).
- `query_features(service_name, layer_id, geometry=None, where="1=1", max_features=1000, ...)` — query features as GeoJSON, with an automatic object-ID fallback for layers that silently drop features on complex spatial queries.
- `query_features_analytic(service_name, layer_id, out_analytics, partition_by=None, ...)` — run SQL window-function analytics (RANK, SUM, LAG/LEAD, etc.) via the `queryAnalytic` operation.
- `top_n_per_group(service_name, layer_id, field, group_by, n=1, ...)` — convenience wrapper over `query_features_analytic` to get the top N features by a field within each group.
- `query_features_with_pagination(service_name, layer_id, max_features=5000, ...)` — like `query_features` but pages past the 2000-record server limit.

### `portal.py` — ArcGIS Portal search and service metadata

Search any ArcGIS Portal (IIPP, ArcGIS Online, USGS, NOAA, USFS, NASA, or a custom Enterprise portal) and inspect services.

- `searchPortal(query, portal="iipp", limit=20, data_only=True, raw_q=None, token=None, **filters)` — free-text search against `/sharing/rest/search`, with a bundled exclusion list to filter out non-data items (styles, dashboards, web apps, etc.).
- `getServiceMetadata(url, token=None)` — fetch `?f=json` metadata for any ArcGIS service or sub-layer URL.
- `PORTALS` — module-level dict of short portal names to base URLs (extend at runtime by adding keys).

### `_http.py` — shared HTTP helpers

Stdlib-only (`urllib`) GET/POST/JSON helpers used by all the modules above.

- `fetch_json(url, params=None, timeout=None)` — GET and parse JSON.
- `fetch_text(url, params=None, timeout=None)` — GET and return raw text (for XML/HTML endpoints like EDW's `/metadata` or an ArcGIS Server HTML browse page).
- `fetch_bytes(url, params=None, timeout=None)` — **added 2026-08-19.** GET and return `(raw bytes, content-type)`, for binary endpoints like `exportImage` where a UTF-8 decode would corrupt the response.
- `post_json(url, params, timeout=None)` — POST form-encoded params and parse JSON (used for large query payloads).
- `build_params(base, token)` — merge an optional token into a params dict.

Default request timeout is 60 seconds (`_TIMEOUT`) — every fetch function above accepts an optional `timeout` override for services that legitimately need longer.
