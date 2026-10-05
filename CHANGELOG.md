# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Spatial filters given a GeoJSON geometry were rejected or wrong. `geometryType` stayed
  `esriGeometryEnvelope` whatever the geometry, so a GeoJSON polygon (converted to Esri
  `rings`) was sent labelled an envelope and ArcGIS answered HTTP 400. The type now follows
  the converted geometry in `services` (`queryFeatureServiceCount`, `queryFeatureService`,
  `computeStatisticsHistograms`) and `edw` (`query_features`, `query_features_analytic`,
  `query_features_with_pagination`, `_query_object_ids`); an explicit non-default type is
  kept.
- GeoJSON polygons are now wound the way Esri reads them: exterior rings clockwise, holes
  counter-clockwise (RFC 7946 is the reverse). Copied as-is, a polygon with a hole sent the
  hole as a second exterior, so the filter covered the area meant to be excluded.
  `LineString`/`MultiLineString` and `MultiPoint` now convert to `paths`/`points`. The
  two copies of `_convert_geometry` (`services`, `edw`) share one implementation in
  `_geojson`.

## [0.4.0] - 2026-10-04

### Added

- `georest.restesri.auth`: sign in to ArcGIS Online or an Enterprise portal as a user.
  `auth.login(...)` runs an OAuth 2.0 authorization code + PKCE flow in the browser, so
  enterprise SSO (SAML/eAuth) works; `auth.set_token(...)` accepts a token from elsewhere,
  such as `GIS("Pro")` in the arcgis API, with optional `referer=` and `refresh=`. Calls
  in `portal` and `services` made without `token=` use the session, but only on hosts it
  covers (the portal, `*.arcgis.com` for AGOL, federated servers, `auth.trust_host`).
  Tokens are refreshed before expiry and once after a 498. `persist=True` saves the
  refresh token to `~/.georest/esri_credentials.json`, restored automatically on the first
  request.
- `georest-login` command for signing in from a terminal (`--status`, `--logout`), the
  intended route for MCP servers.

### Changed

- `token=""` on restesri calls now forces an anonymous request; `token=None` (the
  default) defers to the signed-in session. Previously both meant anonymous.
- restesri `_http` error messages replace any `token` query value with `REDACTED`.
- Esri errors with code 498 or 499 now include a hint to sign in with `georest-login`.
- The Esri JSON -> GeoJSON converters live in one private module,
  `restesri/_geojson.py`, shared by `services` and `edw` (which re-exports
  them under the same names). Two copies is how the fallback drifted.

### Fixed

- `restesri.services.queryFeatureService`: on its `f=json` fallback (taken
  whenever the `f=geojson` request fails, including a transient network
  error) polygons are now split by ring winding, so a multipart polygon comes
  back as a `MultiPolygon` and holes stay holes. It used to hand Esri's flat
  ring list to one GeoJSON Polygon, turning every part after the first into a
  hole in the first. Feature ids on that path now come from the object-id
  attribute (`OBJECTID`, `FID` ...) instead of the feature's position, and a
  server-supplied id is never overwritten - the same rules `edw` already
  followed.

## [0.3.0] - 2026-09-23

### Added

- `georest.restusgs`, a second provider subpackage, with `restusgs.waterdata`: a client
  for the USGS Water Data OGC API (`https://api.waterdata.usgs.gov/ogcapi/v1`), the
  modern replacement for the legacy NWIS Water Services. `list_collections`,
  `get_queryables`, `get_items` (any collection), `search_monitoring_locations`,
  `get_monitoring_location`, `get_time_series_metadata`, `get_daily_values`,
  `get_continuous_values`, `get_latest_values`, `get_field_measurements`, `get_peaks`,
  and `to_rows` to flatten a FeatureCollection into plain dicts. Results are GeoJSON
  `FeatureCollection` dicts like the rest of georest; the time-series functions sort by
  `(time_series_id, time)` client-side because the API does not sort.
- Every fetch is bounded by `max_items` (default 10 000; `None` for unlimited) and the
  result carries `truncated: True` when the server had more. Results also carry
  `rate_limit: {"limit", "remaining"}` when the API reports the caller's budget.
- API-key support: `USGS_API_KEY` (or `USGS_WATERDATA_API_KEY`) in the environment,
  `waterdata.set_api_key(...)`, or a per-call `api_key=`. The key is sent only as the
  `X-Api-Key` header. Anonymous use still works. A 429 the client won't wait out raises
  `waterdata.RateLimitError` (a `RuntimeError` subclass) with `.retry_after`.
- `restusgs` has its own private transport, `restusgs._http`, which adds to the restesri
  one: `headers=` on every fetcher, `fetch_json_with_headers`, `post_json_body`, the first
  400 characters of an HTTP error body in `RuntimeError` messages, and a short retry on
  502/503/504 (1 s, 2 s) and on 429 when `Retry-After` is at most 5 s.
- Top-level lazy re-exports `georest.restusgs` and `georest.waterdata`.
- `USGS_LIVE=1` gates the new live tests; `tests/support.py` gains `patched_module`,
  `patched_ogc`, `FakeOgcServer` and `observation`.

### Changed

- `docs/api.md` is now the reference for the whole package rather than `restesri` alone,
  and gains a "Using georest from an MCP server" section.
- `tests.support.RecordingFetch` accepts and records a `headers=` keyword.

## [0.2.0] - 2026-09-14

### Added

- `portal.searchPortal(..., org_scoped=None)`. An ArcGIS Online organization URL
  (`https://<org>.maps.arcgis.com`) searches all of ArcGIS Online unless the query
  names the organization, so searching a city's portal for "evacuation" returned
  other states' layers first. `org_scoped` restricts results to the portal's own
  organization: `None` (default) scopes organization URLs automatically when `raw_q`
  isn't given, `True` always scopes, `False` never does.

### Changed

- **BREAKING:** `searchPortal` on an ArcGIS Online organization URL now returns only
  that organization's items. This includes the built-in `portal="nasa"`. Pass
  `org_scoped=False` for the 0.1.0 results.
- **BREAKING:** passing `q`, `num` or `f` through `searchPortal`'s `**filters` now
  raises `TypeError`. In 0.1.0, `q` silently replaced the assembled query (dropping
  `query` and `data_only`), and `f`/`num` overrode the response format and bypassed the
  100-result cap. Use `raw_q` and `limit` instead.
- Scoped searches make one extra `/sharing/rest/portals/self` request per portal and
  token, cached for the life of the process. If the organization id can't be
  resolved, `searchPortal` raises `RuntimeError` rather than silently searching all
  of ArcGIS Online.
- When scoping, parentheses and quotes that would break the `orgid:` clause are
  removed from a free-text `query`, which is searched as plain words. A `raw_q` that
  would break it raises `ValueError`.

## [0.1.0] - 2026-09-10

First release as an installable distribution. Previously this was a loose
`RESTesri/` directory that imported only because the repository root happened to be
on `sys.path`.

### Changed

- **BREAKING:** `RESTesri` is no longer a top-level package. It is now
  `georest.restesri`, a subpackage of the new `georest` distribution — lowercase,
  because a mixed-case import name resolves on Windows and macOS but fails on Linux.

  ```python
  from RESTesri import edw                  # before
  from georest.restesri import edw          # after — or: from georest import edw
  ```

  There is no compatibility shim: the package was never published, so every user
  installed it by cloning, and a top-level `RESTesri` module inside the wheel would
  squat a global import name.

- The `User-Agent` sent on every outbound request is now `georest/<version>` rather
  than `hostedServiceTools/1.0`, a name left over from a predecessor project.

- `VALID_THEMES` and `theme_drift()` moved from `tests/support.py` into
  `georest.restesri.edw`, so the shipped CLI no longer imports the test suite.
  `tests.support` re-exports both, so existing test imports still work.

- The theme-drift checker moved out of `tests/` and is now installed as the
  `georest-check-themes` console script.

- `refresh_fixtures.py` moved to `tools/`; it writes into `tests/fixtures/` and is
  deliberately repo-only, not an entry point.

### Added

- `pyproject.toml` (hatchling), `__version__`, PEP 561 `py.typed` marker, and an
  Apache-2.0 declaration via PEP 639.
- Lazy top-level re-exports (PEP 562): `from georest import edw, portal, services`.
- `tests/test_packaging.py`, which enforces the re-export identity
  (`georest.edw is georest.restesri.edw`) and the zero-runtime-dependency guarantee.
- CI: offline test matrix, a weekly live/drift run, and PyPI publishing via Trusted
  Publishing.

### Removed

- `scratch/` — both generator scripts hardcoded paths into a temporary directory
  that no longer exists, so neither could run; `georest-check-themes --emit`
  supersedes them.
- `esriToolsTest.ipynb` — a scratch notebook importing `arcgis.gis`, contrary to the
  stdlib-only design.
- All five `sys.path.insert(...)` bootstraps (two CLIs, three notebooks), made
  unnecessary by the `src/` layout plus an editable install.

[Unreleased]: https://github.com/redcastle-resources/georest/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/redcastle-resources/georest/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/redcastle-resources/georest/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/redcastle-resources/georest/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/redcastle-resources/georest/releases/tag/v0.1.0
