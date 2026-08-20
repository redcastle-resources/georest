# python-repo-template
A template Python repository for GTAC programmers. Please provide feedback if you are GTAC Python programmer! 

## Set up

Complete the following steps to start a new project (NEW-PROJECT-NAME):

1. Clone this repository to your local machine `git clone TEMPLATE-URL NEW-PROJECT-NAME`
2. `cd` into the cloned repository
3. Make a fresh start of the git history for this project with `rm -rf .git && git init`
4. Move the example Environment file to `.env` that will be ignored by git and read by your code `mv example.env .env`

## Create your new repo in GitHub 
1. DESCRIBE THESE STEPS
2. Create a new repository in GitHub making sure that the name matches the `NEW-PROJECT-NAME` specified in the setup.
2. Copy the commands provide by GitHub to push an existing repository from the command line
    `git remote add origin <GITHUB URL>`
    `git branch -M main`
    `git push -u origin main`

## Collaboration
1. DESCRIBE THE GUIDELINES FOR COLLABORATION AND BEST PRACTICES

## Modules

Stdlib-only Python clients for ArcGIS REST services (Image/Map/Feature Services, EDW, and Portal search).

### `services.py` — generic ArcGIS service queries

Tile URLs, feature queries, and image export for any Image/Map/Feature Service.

- `getImageServiceTileUrl(url_or_result, token=None)` — build the `{z}/{y}/{x}` tile URL template for an Image Service or cached Map Service.
- `queryFeatureServiceCount(url_or_result, where="1=1", geometry=None, ...)` — return the count of features matching a where clause and/or spatial filter.
- `queryFeatureService(url_or_result, where="1=1", geometry=None, max_features=1000, ...)` — fetch features as GeoJSON, with a pre-flight count check against `max_features` to avoid huge payloads.
- `getLayerInfo(url_or_result, token=None)` — get layer metadata: fields, geometry type, extent, capabilities.
- `exportImage(url_or_result, bbox, size=(512, 512), image_format="tiff", mosaic_rule=None, rendering_rule=None, ...)` — **added 2026-08-19.** Export a rendered image from an ArcGIS Image Service, with support for mosaic/rendering rules, pixel type, NoData, and resampling. Returns raw bytes, or writes to `out_path` if given. Depends on the new `fetch_bytes` helper in `_http.py` (see below).

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

- `fetch_json(url, params=None)` — GET and parse JSON.
- `fetch_text(url, params=None)` — GET and return raw text (for XML/HTML endpoints like EDW's `/metadata`).
- `fetch_bytes(url, params=None)` — **added 2026-08-19.** GET and return `(raw bytes, content-type)`, for binary endpoints like `exportImage` where a UTF-8 decode would corrupt the response.
- `post_json(url, params)` — POST form-encoded params and parse JSON (used for large query payloads).
- `build_params(base, token)` — merge an optional token into a params dict.
