# georest.cloud and STAC: status, decisions and plan

Updated 2026-11-19 on branch `feat/cloud-stac` (four commits ahead of `main`; pushed to
`origin/feat/cloud-stac`; not merged or released).

This reconciles the 2026-11-04 planning sessions with the code as it stands. The original
plan was published as a private page, "georest Cloud Plan"
(<https://claude.ai/artifact/D2U7DP4FuzsHqgQXpDoFxh>), which also holds the step 4 and
step 5 handoffs and the askterra analysis. A copy saved as of 2026-11-04 is in
[cloud-stac-plan-2026-11-04.md](cloud-stac-plan-2026-11-04.md). Where this file and that
page disagree, this file is newer. Items marked **(new)** were not in the earlier sessions and are suggestions only.

## 1. The brief and the decisions already made

The original ask: "build out capability for cloud-native geospatial technologies (querying
STAC catalogs, pulling in COGs and Zarr data, accessing S3 buckets). Outline a suggested
structure. Conform to contribution guidelines."

After an overlap analysis against pystac-client, odc-stac, rasterio, zarr, obstore, geopandas
and duckdb, the scope was cut to **option A, a thin STAC discovery layer**. Decisions to
treat as settled unless you reopen them:

| Decision | Detail |
|---|---|
| Scope | Find imagery and gridded data over a place and return links. **Never read pixels.** COG, Zarr, GeoParquet, datacubes and storage access are handed off to the reader's own libraries. |
| Where georest adds value | Small, flat, honest results (DataFrames, `truncated`, errors that say what to change); the EDW/MTBS-perimeter-to-scene join only georest can make; curated forestry knowledge in the registry; an agent tool surface. |
| Name | Subpackage `georest.cloud` (STAC is a spec, not an organization, and the name leaves room for later modules). Top-level alias `georest.stac` only. |
| MCP tools | Links and metadata only, never pixels, **never signed links**. `sign` and `assets(sign=True)` are not exposed because the links carry a live token. |
| Perimeters | No shapes pass through the model. `stac_search` takes the EDW service, layer and filter and fetches a simplified perimeter itself. |
| Auth | Environment-only (`EARTHDATA_TOKEN` for NASA). No credentials in arguments or tool schemas. |
| Dependencies | Standard library plus pandas. No new runtime dependency or extra. `rasterio` is added to the `examples` extra only, for the guide. |
| Consumer | **geeViz_Agent**, the multi-tenant chat agent (FastAPI and Google ADK) that serves the askterra tenant, consumes georest through the geeViz MCP server, with small results and links only. georest changes must drop in without edits on the geeViz_Agent side beyond a version bump and a tool list entry (see section 4). |
| Guides | One notebook first. The second, "Did the forest change here?", waits until the first has been reviewed. |
| Repo rules | Apache header on every module; `Example::` docstrings run by the live docstring tests; live tests behind `STAC_LIVE` (with `EDW_LIVE` as the umbrella) that skip rather than fail; Python 3.9; `ruff check .` clean, no `ruff format`; CHANGELOG under Unreleased with no version bump; commit messages prefixed `cloud:`. |

## 2. Where it stands (plan steps 1 to 5)

| Step | State | Commit |
|---|---|---|
| 1. Skeleton: `_http.py`, `_frames.py`, lazy `georest.cloud` / `georest.stac`, packaging guards | done | `ca17c5e` |
| 2. Registry and `stac.catalogs` / `stac.collections` | done | `68bd45d` |
| 3. `stac.search`, `assets`, `footprints`, `sign` | done | `97558fb` |
| 4. MCP tools `stac_collections`, `stac_search`, `stac_assets`, plus `INSTRUCTIONS` | done | `4a0eb6d` |
| 5. Guide notebook, docs, packaging and CI | **not started** | |

What exists:

- **Registry** (`cloud/catalogs.py`): Earth Search, Planetary Computer, USGS LandsatLook and
  two NASA CMR-STAC endpoints (LPCLOUD, ORNL_CLOUD). Each carries an `assets` mode (`open`,
  `sign`, `earthdata`, `requester-pays`) and the forest and land collections that matter. A
  live test checks every registered collection still exists. Any other STAC root URL also
  works, with `assets="unknown"`.
- **Python API:** `stac.catalogs()`, `collections(catalog, query)`, `search(...)`,
  `assets(...)`, `footprints(scenes)`, `sign(href, collection)`.
- **Transport:** retries 502/503/504 with short backoff; retries a 429 only if `Retry-After`
  is 5 s or less, otherwise raises with `retry_after` set; keeps the start of error bodies.
- **Search behavior worth protecting:** follows both POST and GET next links; pages of 50
  because NASA's CMR-STAC returns 502 at 100; `max_items` bounded at 5,000 (the MCP tool clamps
  to 500); `df.attrs["truncated"]` and `["matched"]`; cloud cover re-checked client-side;
  `intersects` accepts a geometry, Feature or FeatureCollection up to 10,000 vertices, drops
  repeated vertices and collapsed polygons (a tiny simplified fire polygon otherwise gets a
  400 from Earth Search), and refuses projected input with the fix.
- **MCP tools:** a blank catalog in `stac_collections` lists the catalogs (no fourth tool);
  `stac_search` takes a `bbox` or the three perimeter arguments (not both), refuses a filter
  matching zero or 20 or more features, drops the `geometry` column, and returns `truncated`,
  `matched` and `catalog`; catalog URLs go through `public_https_url`.
- **Verification at step 4:** 867 tests passed offline, `ruff` clean, live checks passed for a
  bbox search, WILDER and Cameron Peak. `lcms_summary` in `test_live_mcp.py` fails on the
  untouched baseline too; it is not part of this work.

I could not run the suite for this update (no `pytest` in the default Python). Build a venv
outside the repo first (see step 5 setup) and re-confirm the 867 baseline.

## 3. Next: finish step 5

Scope for this step is **one guide notebook plus the docs, packaging and CI items**. Stop
after the first guide even if it goes well.

### Setup

- Make a venv outside the repo and install `-e ".[dev,mcp,examples]"`, then `pip install rasterio`.
- Check `python -c "import mcp, plotly, folium, rasterio, jupyterlab"`. In step 4 an install
  looked finished while `mcp` was missing, which makes every MCP test skip and look like a pass.
- Baseline: `pytest tests -q` and `ruff check .`. Live suites need `STAC_LIVE=1` and `EDW_LIVE=1`.

### The guide: `examples/guide_fire_imagery.ipynb`, "What imagery exists over this fire?"

Rules enforced by `tests/test_example_guides.py`: every code cell run and saved with an
`execution_count`; no error output and nothing on stderr (GDAL and rasterio warnings count);
only public georest modules, called through the module. Guides keep their saved output.
Charts carry an HTML copy (commit `be34bcb`): copy the setup cell from `guide_fire_history`,
including `pio.renderers.default = "notebook_connected"`.

Cells:

1. Setup (`edw`, `stac`, pandas, plotly, folium).
2. The fire: Cameron Peak 2020 from `EDW_MTBS_01`, found by layer name because per-year layer
   ids shift; perimeter simplified with `max_allowable_offset=0.001` (the raw one has about
   34,000 vertices against the 10,000 limit).
3. Where to look: `stac.catalogs()` and `stac.collections("earth-search", "sentinel")`, with a
   sentence on `access`.
4. Search: the perimeter passed straight to `intersects`, ignition date plus 90 days,
   `max_cloud_cover=30`; print the count, `matched` and `truncated`.
5. When could you see it: plotly scatter of date against cloud cover.
6. Where are the scenes: `stac.footprints` and the perimeter on one folium map.
7. One scene's files: `stac.assets` as a short table.
8. The only pixel-reading cell: rasterio opens the `visual` COG, windows it to the perimeter
   box, reads it downsampled, and shows it. Say plainly that georest found the link and
   rasterio read the pixels. Must run in seconds and write nothing to stderr.
9. Where to go next (another catalog with `stac.sign`, another collection, the MCP tools). Do
   not promise the second guide by name.

Traps: stay on Earth Search, because signed Planetary Computer links expire inside a saved
notebook; don't write archive-dependent numbers into the markdown; if a host is down, stop
rather than saving a notebook with an error; add `rasterio` to the `examples` extra with a
comment, and confirm `test_packaging.py` still passes.

### The rest of step 5 (nothing here is done)

- `README.md`: add `georest.cloud` to the provider table and sentence.
- `docs/api.md`: a `georest.cloud` section in the style of the `usgs` one, plus the contents
  list and the new MCP tools if tools are listed. It currently names only `esri`, `usgs` and
  `usfs`, so `fema` and `noaa` are missing too.
- `pyproject.toml`: description and keywords (`stac`, `cloud-native`, `earthdata`).
- `tests/README.md`: the layout table, the `STAC_LIVE` flag, and the two fakes,
  `FakeStacServer` and `FakeStacSearch`.
- `.github/workflows/live.yml`: run the STAC live tests weekly. `ci.yml`: the wheel must
  contain `georest/cloud/stac.py` and `georest/cloud/catalogs.py`, and the smoke test imports `stac`.
- `CHANGELOG.md`: one line under Unreleased for the guide.
- Verify: `pytest tests -q`, `ruff check .`, `python -m build` with the CI wheel assertions
  run locally, and the wheel installed in a clean venv with `import georest.cloud.stac`.

Ask before touching these two:

- `georestDocs/source/examples.rst` (a separate repo) lists the guides by name and needs an
  entry for the new one.
- `CONTRIBUTING.md` still says "zero third-party runtime dependencies" while pandas is core.
  The enforced rule is in `test_packaging.py`. A small separate docs fix.

## 4. After step 5

### Release

Merge the branch and cut 0.6.0 (a new provider) so the Unreleased CHANGELOG entry moves out.
The earlier plan said to pin the consumer to a georest release once step 5 ships, so this gates
the geeViz and geeViz_Agent work below. Note that geeViz_Agent installs from `main` by default.

### Second guide

"Did the forest change here?": a search and a scene comparison alongside `lcms` and `fia`
through `usfs.align`. Start only after the first guide is reviewed.

### Consuming georest from geeViz_Agent (askterra is one of its tenants)

Rewritten 2026-11-19 after reading the `geeViz_Agent` and `geeViz` checkouts. **This replaces
the 2026-11-04 askterra analysis**, which looked at the older askterra Django app (Gemini in
`chat_simple`, `%%urlStart%%` links) and proposed passing georest functions in-process. That
is not the route. The askterra tenant is now served by geeViz_Agent.

How the pieces connect today:

- **geeViz_Agent** (FastAPI and Google ADK, the Agent Development Kit) runs several
  **tenants**. A tenant is a folder, `tenants/<slug>/tenant.yaml`, that sets branding, model,
  which tools exist, and which MCP servers to start. `tenants/askterra/tenant.yaml` is the
  askterra one. (MCP, the Model Context Protocol, is the standard for offering tools to a
  model.)
- The agent's tools come from two MCP servers named in that file: **`geeviz`** (a local
  stdio process running `geeViz/mcp/server.py --sandbox`) and **`gmaps`** (Google Maps, over
  HTTP). georest's own `georest-mcp` server is **not** started by any tenant.
- georest reaches the agent in two ways, both **through geeViz**: the geeViz server wraps
  georest in its own tools (`search_fia`, `fia_estimate`, `lcms_summary`), and it preloads
  `georest` and `georest.restesri` into the `run_code` sandbox, where model-written Python
  can call them. The sandbox blocks all other HTTP libraries and tells the model to use
  "domain wrappers" such as georest. Code running from a `/georest/` path may connect to public
  hosts but not to loopback, private or link-local addresses, so the STAC hosts work.
- **How georest is installed:** the geeViz_Agent `Dockerfile` and `deploy/deploy.sh` install
  georest **from source**, not PyPI: the local checkout (`GEOREST_SRC`, default `../georest`)
  or else GitHub `GEOREST_REF` (default `main`). `constraints.txt` never pins georest, and a
  test (`test_the_image_installs_georest_from_source`) guards that. geeViz declares georest as
  a core dependency, and its release notes bound it `>=0.2,<0.4`.
- **Tool access** is set per tenant. `tiers` map each plan level to the tool names it allows
  (a tool no tier names is open to everyone and logged). The `picker` lists optional tools a
  person can switch on or off per conversation, each costing prompt tokens on every model step.

What this means for the plan:

1. **Deploying `main` ships this branch.** An image built with the default `GEOREST_REF=main`
   picks up georest as soon as the branch merges, with no version bump. Merge only when
   `pytest` and the guide tests pass, and treat `main` as production for geeViz_Agent.
2. **Fix the version bound first (blocker).** geeViz bounds georest `<0.4`, and this work is
   headed for 0.6.0 (the plan says "based on 0.5.0"). Check the bound in geeViz's
   `pyproject`/release notes and widen it in the same change as the georest release, or
   `pip` will refuse or downgrade georest. geeViz's `esriLib` also uses georest private
   names; confirm the `cloud` work did not touch any (it should not have, as `cloud` is new).
3. **The new tools need a home in geeViz, not geeViz_Agent.** Add `stac_collections`,
   `stac_search` and `stac_assets` to `geeViz/mcp/server.py` as thin wrappers over
   `georest.cloud.stac`, as `fia_estimate` already wraps its library, so the work stays in
   one repo. Do not re-describe the tools there: reuse georest's `INSTRUCTIONS` text and
   argument names. Also add a short STAC section to `geeViz/mcp/agent-instructions.md`
   (when to call them, results are links and metadata, never invent scenes, call
   `stac_collections` first, the `access` rule).
4. **Then one entry per tenant file.** In `tenants/askterra/tenant.yaml`, add the three names
   under the `geeviz` list in the `basic` tier, and under the picker group `analysis` (or a new
   group "Imagery search") with a label and note. Leave them off by default in the picker if
   token cost matters. Other tenants (`geeviz`, `lcms_navigator`, `demo`) opt in the same way.
5. **Keep the contract steady.** geeViz_Agent consumes names and shapes, so the following must
   not change without a coordinated bump: the three tool names and arguments; the result keys
   `count`, `rows`, `truncated`, `matched`, `catalog`; the `{"error", "hint"}` failure shape;
   `stac.search`, `stac.assets` and `stac.footprints` signatures; `df.attrs["truncated"]` and
   `["matched"]`. Put these in a short "Stable surface" list in `docs/api.md` and add a
   test that fails when one is renamed.
6. **Place is a bbox, worldwide**, from askterra's maps (`region=xmin,ymin,xmax,ymax`), so the
   `bbox` path of `stac_search` is primary; EDW perimeters are US-only and suit the forestry
   tenants.
7. **Offline and live tests.** In geeViz: tool tests with `georest.cloud.stac` mocked, plus a
   sandbox test that `run_code` can call `georest.cloud.stac.search` against a fake. In
   geeViz_Agent: the existing tier/picker tests should cover the new names; measure latency
   and token cost on test staging.

Order of work: finish step 5, merge and release georest, widen geeViz's bound and add the
wrappers, then the tenant entries. Nothing here needs a change to the askterra Django app.

Open questions: whether the wrappers should import georest's decorated functions directly
(one definition, but ties geeViz to `georest.mcp`, which needs the `mcp` extra) or re-declare
them (duplicates the description text); and whether geeViz_Agent should install a georest
release rather than `main` for the production tenant.

### Future add-ins (noted, not built)

From the earlier plan, in rough order. None is in the first release.

1. **Lidar:** COPC and EPT, including USGS 3DEP. `copc.info()` from the LAS header, reads
   through an optional extra. Useful for canopy height and biomass. The one named as next.
2. **Raster and array readers:** `cog.info` / `cog.read`, `zarr.describe` / `zarr.open`,
   `stac.to_xarray`, as thin wrappers, if demand goes beyond the guide hand-offs.
3. **Cloud-native vector:** GeoParquet (Overture, Source Cooperative, stac-geoparquet) with
   bbox pruning, then FlatGeobuf and PMTiles.
4. **Unified storage:** S3, GCS and Azure listing and range reads through obstore.
5. Later: kerchunk and virtual Zarr (HRRR, gridMET, Daymet); a generic OGC API client by
   promoting the USGS `_ogc` module to a shared `_core`; COG validation and writing; zonal
   statistics joined to FIA and LCMS; a local range-read cache under `~/.georest`.

## 5. Ideas not from earlier sessions (new)

Suggestions from reading the code, for you to accept or drop. They respect the decisions in
section 1.

- **Make `earthdata` real without breaking the rule.** The registry says NASA downloads need
  `EARTHDATA_TOKEN`, but nothing reads it. A Python-only option could attach it where the
  caller opts in, with the MCP layer staying link-only. Translating `s3://` requester-pays
  links to https where allowed would help too, without adding boto.
- **Richer search:** a direct `stac.item(catalog, collection, id)` lookup, `sortby` and
  `fields`, and a `queryables` call so callers learn what is filterable.
- **Coverage summary:** per-month scene counts and median cloud cover over a place, which is
  what askterra-style questions ("which dates are usable?") need before pulling any data.
- **Product aliases across catalogs:** the same product (Sentinel-2 L2A, Landsat C2) lives in
  several catalogs with different asset keys. A small registry mapping could reconcile them.
- **Registry upkeep:** a `tools/refresh_registry.py` beside `tools/refresh_fixtures.py` that
  reports new and removed collections. The live test catches removals only.

## 6. Related, but separate

- `~/.claude/plans/idempotent-doodling-koala.md` is a plan for publishing tree canopy cover to
  Source Cooperative with Portolan (the `tcc-catalog` repo). It is a different project; it
  could consume `stac` output later but nothing depends on it.
- `~/.claude/plans/is-there-a-versioned-groovy-puppy.md` is a geeViz pre-PR checklist and is
  unrelated.
