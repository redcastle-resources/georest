# geoREST tests

Standard-library `unittest`, matching the package's own stdlib-only
dependencies. `pytest` collects these natively and is what CI runs; either
runner works locally.

Install first — this is a `src/` layout, so `geoREST` is not importable from
the repository root without it. That is deliberate: the suite imports the
*installed* package, so a packaging mistake fails loudly instead of being
masked by the working directory.

```bash
pip install -e ".[dev]"
```

Then, from the **repository root**:

```bash
python -m pytest                                 # offline, ~0.2s
python -m unittest discover -s tests -t .        # the stdlib runner
python -m unittest discover -s tests -t . -v     # with test names
python -m unittest tests.test_layers             # one module
```

## Offline by default

The offline suite never touches the network and is fully deterministic. The
EDW server is genuinely unreliable — it intermittently answers 404, 500,
`Layer not found`, or 200-with-no-layers for services that work moments
later — so network tests are opt-in:

```bash
EDW_LIVE=1 python -m unittest discover -s tests -t .        # everything live
SERVICES_LIVE=1 python -m unittest tests.test_live_services # services.py only
```

Two flags, because the two live suites hit different servers: `EDW_LIVE`
covers EDW, while `SERVICES_LIVE` covers the public ArcGIS hosts
(`imagery.geoplatform.gov`, `sampleserver6.arcgisonline.com`,
`services.arcgisonline.com`) that `services.py` is exercised against.
`EDW_LIVE` also enables the services tests, so one flag still turns on the
whole network suite.

Live tests retry through transient failures and **skip** rather than fail when
the server stays down, so a red result means a real problem. Note `_http`
signals transient trouble two ways: `RuntimeError` for HTTP/URL errors, and
`ValueError` when a response won't parse as JSON (what a degraded HTML error
page served with status 200 looks like).

## Layout

| File | Covers |
|---|---|
| `support.py` | Fixtures, the `FakeServer`, live gating, catalog-drift logic |
| `test_layers.py` | Layer role tagging, grouping, null-safety, real-catalog regression |
| `test_geometry.py` | Esri↔GeoJSON conversion, ring winding, GeoJSON sanitising |
| `test_analytics.py` | `outAnalytics` payload construction, `partitionBy` nesting |
| `test_pagination.py` | Page/chunk strides, `maxRecordCount`, `in_sr`/`out_sr` |
| `test_search.py` | Search matching, theme-table integrity (offline) |
| `test_services.py` | `services.py`: all 10 public functions, mocked HTTP |
| `test_portal.py` | `portal.py`: search parsing, service-type detection, error surfacing |
| `test_live_catalog.py` | **Live**: catalog↔theme drift, end-to-end smoke |
| `test_live_examples.py` | **Live**: executes `edw.py`'s own docstring examples |
| `test_live_services.py` | **Live**: `services.py` against real Image/Feature Services |

## Fixtures

`fixtures/layer_cache.json` holds every service's real MapServer layer tree,
so the layer-role logic is exercised against genuine data — including the
awkward shapes — without a network round-trip. The key regression test
replays all ~105 of them.

Refresh after EDW restructures services:

```bash
python tools/refresh_fixtures.py
```

Services that can't be fetched keep their existing entry, so a flaky run
costs freshness rather than the fixture.

## Checking the theme catalog

`src/geoREST/RESTesri/edw.py` carries `_SERVICE_THEMES`, a hand-curated table of every
MapServer service with a theme and description. It drives theme filtering and
description matching in `search_edw_services`, and rots silently as EDW
publishes and retires services: an untyped service still appears in results,
but as theme `uncategorized` with no description, so theme filters and keyword
aliases stop reaching it.

```bash
georest-check-themes           # drift report
georest-check-themes --emit    # + paste-ready entries
```

`VALID_THEMES` and `theme_drift()` live in `geoREST.RESTesri.edw` so this
shipped CLI does not import the test suite; `tests/support.py` re-exports both,
so tests can keep importing them from there.

Exit code is 0 in sync, 1 drifted, 2 if the catalog was unreachable — so it
can gate a job. `test_live_catalog.py` asserts the same thing under
`EDW_LIVE=1`.

Only **MapServer** services need a theme. The catalog also carries one
`GPServer` (`RAVG_DataExtract_01`, a geoprocessing tool with no layers), which
is reported separately and is not drift. A *new* non-MapServer service will
fail a test deliberately: `get_service_info`, `get_layer_info` and
`query_features` all build `/MapServer` URLs, so another type needs handling
before it can be used.

## Adding tests

Use `support.patched(...)` to swap out `fetch_json` / `post_json` /
`get_layer_info`; it restores them and clears the `maxRecordCount` cache on
exit. `support.fake_layers([...])` serves a fixed layer list, and
`support.layer(...)` builds entries with EDW's field names.

For `services.py`, use `support.patched_services(...)` instead — the module
binds the HTTP helpers into its own namespace at import time
(`from ._http import fetch_json, ...`), so patching `_http` has no effect.
`support.RecordingFetch(...)` stands in for `fetch_json`: it records every
`(url, params)` pair and replays canned responses in order, raising any
entry that is an exception so the transport-failure and format-fallback
paths can be driven offline.

## What the services tests pin down

`services.py` talks to servers that lie in specific, verified ways, and the
tests exist mostly to keep those workarounds honest:

- `_http` turns **every** HTTP/URL failure into `RuntimeError`, so
  `services.py` raises that rather than `ConnectionError` — which its
  docstrings used to promise and could never deliver. `test_services.py`
  guards the docstrings themselves against that claim reappearing.
- `getSamples` must never raise for a point it cannot sample; a failing
  point is reported in-band via `source`. A missing `RuntimeError` in its
  fallback previously let one unreachable point destroy every other point's
  result.
- `getLayerInfo` must reject a *service root* as well as a group layer;
  ArcGIS reports the two with different keys (`layers` vs `subLayers`), and
  only the second used to be caught.
- `queryFeatureService` returns feature `id`s with different semantics on
  its two response paths (server-assigned on `f=geojson`, positional strings
  on the `f=json` fallback, which also strips dotted property names).
- `getImageServiceTileUrl` does no validation: against an **uncached**
  service the template it returns is well-formed but every tile 404s. The
  live suite asserts both halves of that.

`test_portal.py` guards the same defect class in `portal.py`: `searchPortal`
turned a portal error (an expired token, code 498) into an empty result list
that read as "no matches", and `getServiceMetadata` handed back the Esri
error object as if it were metadata. Both now raise `ValueError`, and
`_detect_service_type` is tested to still degrade to `"Unknown"` rather than
propagating that.
