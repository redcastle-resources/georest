# georest — API reference

Stdlib-only Python clients for geospatial REST APIs, one provider subpackage per API
family: `georest.restesri` (ArcGIS Image/Map/Feature Services, the USFS EDW, Portal search)
and `georest.restusgs` (the USGS Water Data OGC API).

Contribution guidelines live in [CONTRIBUTING.md](../CONTRIBUTING.md); installation and
a quick start are in the [README](../README.md).

- [georest.restesri](#georestrestesri) — `services`, `edw`, `portal`, `_http`
- [georest.restusgs](#georestrestusgs) — `waterdata`, `_ogc`, `_http`
- [Using georest from an MCP server](#using-georest-from-an-mcp-server)
- [Tests](#tests)

## georest.restesri

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

## georest.restusgs

The USGS provider. One public module so far, `waterdata`, for the **USGS Water Data OGC
API** (`https://api.waterdata.usgs.gov/ogcapi/v1`) — the modern replacement for the legacy
NWIS Water Services (`/nwis/site`, `/nwis/dv`, `/nwis/iv`, `/nwis/gwlevels`, `/nwis/peak`).

```python
from georest.restusgs import waterdata
from georest import waterdata                # equivalent top-level re-export
```

`restusgs` is self-contained: its private `_http.py` (transport) and `_ogc.py` (a generic
OGC API - Features client) are its own, not shared with `restesri`.

### `waterdata.py` — USGS Water Data OGC API client

Every function returns a GeoJSON `FeatureCollection` dict — one Feature per **observation**
for the time-series collections (not one per series, as NWIS returned), one per site for
`monitoring-locations`. `value` arrives as a string (precision); `to_rows` converts it.

Facts about the API that shape the module (all verified live, 2026-09-14): it does **not**
sort, so the time-series functions sort their output by `(time_series_id, time)`; it reports
no `numberMatched`, so every fetch is bounded by `max_items` and flags `truncated: True`
when the server had more; pagination is cursor-based via `rel="next"` links; any queryable
property name works as a direct equality filter; CQL2 text goes in `filter`.

Constants: `BASE_URL`, `COLLECTIONS` (the 14 data collections; `list_collections()` is the
live, authoritative list), `PARAMETER_CODES` (`00060` discharge, `00065` gage height,
`00010` water temperature, `00045` precipitation, `72019` depth to water), `STATISTIC_CODES`
(`00001` max, `00002` min, `00003` mean, `00008` median), `DEFAULT_MAX_ITEMS = 10_000`.

- `list_collections(api_key=None, timeout=None)` — the live collection catalog.
- `get_queryables(collection, ...)` — `{property_name: json_schema}`; every key is a valid direct filter for that collection (`daily` alone has ~55, including every site attribute).
- `get_items(collection, bbox=None, start=None, end=None, properties=None, skip_geometry=False, filter=None, max_items=10_000, page_size=1_000, api_key=None, timeout=None, **filters)` — the generic fetch under everything below; use it for a collection without a helper (`channel-measurements`, `time-series-methods`, the code tables) or a filter the helpers don't spell out. `start`/`end` accept strings, `date` or `datetime` objects and may be open on either side; `start` may also be a ready interval (`"2020-01-01/2020-01-31"`, `"../2020-12-31"`) or an ISO 8601 duration meaning "the last …" (`"P7D"`, `"PT6H"`, `"P2W"`). The server rejects durations in every form despite its docs, so they are resolved client-side to `<now minus duration>/..`; years and months raise `ValueError` (no fixed length). `properties` trims the response to exactly those keys (this generic path does **not** add the time-series essentials back — the helpers do). `**filters` are direct equality filters (`state_code="49"`); passing `f`, `limit`, `cursor` or `offset` raises `TypeError`.
- `search_monitoring_locations(bbox=None, state_code=None, county_code=None, site_type_code=None, hydrologic_unit_code=None, monitoring_location_id=None, agency_code=None, ..., max_items=1_000)` — find sites. `site_type_code` (`"ST"` stream, `"LK"` lake, `"GW"` well, …) and `monitoring_location_id` accept a list (becomes a CQL2 `IN`); `hydrologic_unit_code` prefix-matches. Each Feature's `id` is the `monitoring_location_id`.
- `get_monitoring_location(monitoring_location_id, ...)` — one site as a single Feature. Bare ids (`"09380000"`) get the `USGS-` prefix everywhere; ids with another agency prefix (`"USEPA-…"`) pass through.
- `get_time_series_metadata(monitoring_location_id=None, parameter_code=None, statistic_id=None, bbox=None, ...)` — which series exist at a site (the old `seriesCatalogOutput=true`), with begin/end dates and `time_series_id`; check this before pulling values.
- `get_daily_values(monitoring_location_id, parameter_code="00060", statistic_id="00003", start=None, end=None, *, skip_geometry=True, max_items=10_000, ...)` — daily statistics (old `/nwis/dv`), sorted. `time` is `YYYY-MM-DD`. 10 000 rows ≈ 27 years of one daily series.
- `get_continuous_values(monitoring_location_id, parameter_code="00060", start=None, end=None, ...)` — sensor readings, typically every 15 minutes (old `/nwis/iv`), sorted. **Volume warning:** ~35 000 rows per year per parameter, so the default `max_items` covers roughly 100 days; narrow the window, or pass `max_items=None` with an API key. `start="P7D"` is the last week.
- `get_latest_values(monitoring_location_id=None, parameter_code=None, *, source="daily", bbox=None, skip_geometry=False, ...)` — the most recent observation per series from `latest-daily` or `latest-continuous`. No site is required: `bbox` + `parameter_code="00060"` is "current discharge at every gage in this area", which is why geometry is kept by default here. Any other `source` raises `ValueError`.
- `get_field_measurements(monitoring_location_id=None, parameter_code=None, start=None, end=None, *, bbox=None, ...)` — manual site-visit readings (old `/nwis/gwlevels` and `/measurements`): groundwater levels, gage height and discharge measurements. Sorted by `(monitoring_location_id, time)`.
- `get_peaks(monitoring_location_id, start=None, end=None, ...)` — annual peak streamflow (old `/nwis/peak`): one Feature per water year with `value`, `time`, `water_year` and a `qualifier` list. The `peaks` collection rejects the `datetime` parameter, so `start`/`end` are sent as a CQL2 `time` filter (ANDed with any `filter` you pass). Beware the same on `get_items("peaks", start=...)` or `get_items("time-series-metadata", start=...)`: those collections answer 400 "datetime query not supported" — use `filter` instead.
- `to_rows(fc, numeric=("value",))` — flatten a FeatureCollection into one plain dict per Feature: the Feature's `properties`, plus its `id` when the properties carry none, with every key in `numeric` converted by `float()` (`None` when missing or non-numeric — a qualifier such as `"Ice"` can stand in for a number). Sorted like the series functions. A flat row list feeds `csv.DictWriter`, `pandas.DataFrame(rows)` and plotting directly; group by `time_series_id` with `itertools.groupby` for a per-series shape.
- `set_api_key(key)` — set the process-wide API key (e.g. from a secret store at server start); `None` clears it.
- `RateLimitError` — re-exported from `_http` (below) so callers can catch it by name.

**Foreign members on results.** Besides `type`/`features`, a FeatureCollection may carry `truncated: True` (the fetch stopped at `max_items` while the server still had more — the API reports no total, so this is the only signal) and `rate_limit: {"limit": n, "remaining": m}` (from the last page's `X-RateLimit-*` headers, when present). Both are plain keys; GeoJSON consumers ignore them.

**API key resolution**, per call: an explicit `api_key=` argument wins (`""` forces anonymous); else the value given to `set_api_key`; else the first non-empty of `USGS_API_KEY` / `USGS_WATERDATA_API_KEY` in the environment; else anonymous. Anonymous use works, rate-limited to "a few queries per hour" — get a key at <https://api.waterdata.usgs.gov/signup/>. The key is sent only as the `X-Api-Key` header, never as a query parameter, so it cannot appear in a URL, an error message, or a server log. Reading a `.env` file is the application's job, not the library's.

**Error contract.** Same as restesri — `RuntimeError` for transport failures, `ValueError` for a non-JSON body — with two refinements. A `RuntimeError` message includes the first 400 characters of the response body, which for this API is a JSON object whose `description` says what was actually wrong (`"Unknown string format: not-a-date"`). And a 429 the client won't wait out raises `RateLimitError`, a `RuntimeError` subclass with `.retry_after` (seconds the server asked for, or `None`). `TypeError` for a reserved query parameter and `ValueError` for a bad `bbox`/`page_size`/`max_items` are raised before any request.

### `_ogc.py` — generic OGC API - Features client (private)

Nothing in it knows about USGS; `waterdata` is a thin layer over it, and another OGC endpoint could reuse it. `list_collections`, `get_collection`, `get_queryables`, `get_item`; `build_item_params(bbox, datetime, properties, skip_geometry, filter, filter_lang, limit, **filters)`; `bbox_param`, `datetime_param(start, end)`, `api_key_headers`; `iter_items(...)` (a generator over pages) and `get_items(...)` (merged FeatureCollection with the `truncated`/`rate_limit` members). Paging follows `rel="next"` hrefs verbatim — the server preserves every original parameter in them — and stops on a missing link, an empty page, `max_items`, or a link that points back at a URL already fetched. `MAX_LIMIT = 50_000` (the OpenAPI schema says 10 000, but 50 000 is accepted live; the live suite guards it), `DEFAULT_PAGE_SIZE = 10_000`. Binds `fetch_json_with_headers` from `_http` at import time, which is the test suite's patch point for the whole provider.

### `_http.py` — restusgs transport (private)

A copy of the restesri transport (`fetch_json`, `fetch_text`, `post_json`, the `georest/<version>` User-Agent, the same RuntimeError/ValueError contract) with what the USGS API needs: a keyword-only `headers=` on every fetcher; `fetch_json_with_headers` returning `(json, response_headers)`; `post_json_body` for a JSON-bodied POST (CQL2-JSON); the error-body excerpt in `RuntimeError` messages; and a retry policy tuned for a tool call inside an agent rather than a batch job — `502/503/504` are retried with a 1 s then 2 s backoff (3 attempts total; `retries=1` disables it), a `429` is retried only when the server's integer `Retry-After` is ≤ 5 s and otherwise raises `RateLimitError` immediately, and every other status (including 500, which from these APIs means "your query failed") raises at once. Worst-case added latency is 3 s. The duplication with `restesri._http` is deliberate for now; the two are candidates for a future shared core.

## Using georest from an MCP server

georest is built to be wrapped as tools for an LLM agent (the geeViz MCP server does this
for `restesri`; it lives in its own repository). The library side of that contract:

- **Set the key once, at startup, and keep `api_key` out of every tool schema.** Read it
  from the server's secret store into `USGS_API_KEY` (or call `waterdata.set_api_key`) the
  way geeViz's server loads its `.env`. A tool parameter named `api_key` invites the model
  to ask users for keys and paste them into the conversation.
- **Keep results bounded and compact.** Leave `max_items` finite, prefer `to_rows`,
  `properties=` and `skip_geometry=True` (the series functions default to it), and relay
  `truncated` and `rate_limit` to the model verbatim so it can narrow a query or back off.
- **Relay rate limits instead of waiting them out.** Catch `waterdata.RateLimitError` and
  surface `.retry_after`; the library never sleeps more than a few seconds inside a call.
- **Error messages are safe to return to the model.** A `RuntimeError` carries the request
  URL and the API's own `description` — never the key, which travels only as a header.

## Tests

`pip install -e ".[dev]"` first — this is a `src/` layout, so the package is not importable from the repository root without it. Then `python -m pytest`, or `python -m unittest discover -s tests -t .` for the stdlib runner; the tests are `unittest.TestCase` classes either way. Offline and deterministic by default; set `EDW_LIVE=1` to include tests that hit the real EDW server, `SERVICES_LIVE=1` for the `services.py` tests that hit public ArcGIS hosts, and `USGS_LIVE=1` for the `waterdata` tests against api.waterdata.usgs.gov. See [tests/README.md](../tests/README.md).

`georest-check-themes` reports drift between the live catalog and the hand-curated `_SERVICE_THEMES` table in `edw.py` (`--emit` prints paste-ready entries).
