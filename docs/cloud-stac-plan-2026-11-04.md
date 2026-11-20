# Cloud-native discovery for georest (original plan, 2026-11-04)

> **Archived copy, as of 2026-11-04.** This is the original planning page, converted from the
> private artifact (<https://claude.ai/artifact/D2U7DP4FuzsHqgQXpDoFxh>). It is superseded by
> [cloud-stac-plan.md](cloud-stac-plan.md) (updated 2026-11-19), which reconciles it with the
> code. Kept for the step 4 and step 5 handoffs and the askterra analysis. Its status lines (for
> example "step 5 is next") are not updated.

A thin STAC layer that finds imagery and gridded data over a place and hands back links.
Reading pixels stays with rasterio, odc-stac, zarr and the rest of the ecosystem. The first
consumer is askterra, through the existing `georest-mcp` server.

Based on georest 0.5.0. Follows CONTRIBUTING.md and the enforced packaging tests. Steps 1 to 4
built and pushed (2026-11-04); next is step 5, one guide notebook first (handoff below).

## Top priorities, in build order

1. **Subpackage skeleton and packaging guards** (done, `ca17c5e`). `georest.cloud` with its own
   `_http.py` and `_frames.py`, the lazy `georest.stac` alias, and the `test_packaging.py`
   updates. No new runtime dependency and no new extra: stdlib plus the pandas the other
   providers already use.
2. **Curated catalog registry** (done, `68bd45d`). `catalogs.py` lists Earth Search, Planetary
   Computer, USGS LandsatLook and the NASA CMR-STAC providers, with the collections that matter
   for forestry (Sentinel-2, Landsat, HLS, NAIP, 3DEP, GEDI) and each catalog's auth and asset
   quirks. This is the domain knowledge other STAC clients do not carry.
3. **STAC search that returns small, honest results** (done, `97558fb`). `stac.search`,
   `collections`, `assets`, `footprints` and `sign`. One DataFrame row per scene, `truncated`
   set when `max_items` stops the fetch, and `intersects` accepting an EDW or MTBS perimeter
   directly.
4. **MCP tools for askterra** (done, `4a0eb6d`). `stac_collections`, `stac_search` and
   `stac_assets` in `mcp/server.py`, under the server's own rules: data tools only, explicit
   limits, errors returned as answers, public https only, no secrets in arguments. Tools return
   links and metadata, never pixels. `stac_search` fetches a fire perimeter itself from an EDW
   layer, because `edw_query` returns attributes and not shapes. Full spec in the handoff below.
5. **Guide notebooks and docs** (next). Step 5 starts with one guide, "What imagery exists over
   this fire?", in the repo's saved-output style, ending with a short hand-off to the reader's
   own raster tools. "Did the forest change here?" follows only after the first is reviewed.
   Step 5 also covers the README, API reference, packaging and CI items. Full handoff below.

## Handoff for step 4 (MCP tools)

Written so a new session can run step 4 without re-reading steps 1 to 3. Branch
`feat/cloud-stac`; commit only when the user says so, message prefix `cloud:`, with the
Co-Authored-By line the session's system reminder gives.

> **Step 4 outcome (2026-11-04).** Built as specified: `stac_collections`, `stac_search`,
> `stac_assets`, the `INSTRUCTIONS` paragraph, new offline tests, and five live MCP calls. 867
> tests pass offline and `ruff` is clean. Live, a bbox search, the WILDER fire perimeter (5
> Sentinel-2 scenes) and the empty, too-broad and mistyped-collection errors all behaved. One
> deviation: the live Cameron Peak call exposed a bug in step 3. Simplifying a tiny fire polygon
> at `max_allowable_offset=0.001` collapses it to one repeated point, and Earth Search rejects
> that with a 400. `stac.search` now removes repeated vertices and drops collapsed polygons
> (refusing, with the fix, if nothing is left), so notebooks benefit too. Unrelated:
> `lcms_summary` fails in `test_live_mcp.py` on the untouched baseline as well.

### Set up and read

- There is no repo venv. Make one outside the repo: `python -m venv <scratch>/venv`, then
  `<venv>/Scripts/python -m pip install -e ".[dev,mcp]"`. Without the `mcp` extra every test in
  `test_mcp_server.py` skips, which looks like a pass.
- Baseline: `pytest tests -q` (836 pass offline) and `ruff check .` are clean. Live checks:
  `STAC_LIVE=1`.
- Read only these: `src/georest/mcp/server.py` lines 1-90 (rules, imports, INSTRUCTIONS), 96-180
  (`records`, `answer`, `tool`, `public_https_url`, `_bbox`), 456-490 (`earthquakes`, the
  limit-and-refuse pattern) and 578-606 (`edw_query`); `tests/test_mcp_server.py` lines 1-160.
  The `cloud` code needs no re-reading; the API is below.

### The step 3 API, as built

```python
from georest.cloud import stac
stac.catalogs()                         # name,title,url,assets,notes
stac.collections(catalog, query="", *, max_items=500)
stac.search(catalog, collections, *, bbox=None, intersects=None, datetime=None,
            max_cloud_cover=None, query=None, max_items=100, newest_first=True)
  # cols: catalog,collection,id,datetime,cloud_cover,platform,assets,thumbnail,bbox,geometry
  # attrs: truncated, matched, catalog.   max_items 1..5000
stac.assets(catalog, collection, item, *, sign=False)
  # cols: catalog,collection,item,key,title,type,roles,access,href
stac.footprints(scenes) -> GeoJSON FeatureCollection
stac.sign(href, collection, *, catalog="planetary-computer")
```

`catalog` is a registry name or any STAC root URL. `intersects` takes a GeoJSON geometry,
Feature or FeatureCollection (several polygons become one MultiPolygon; over 10,000 vertices or
non-lon/lat coordinates raise `ValueError` with the fix). Errors: `ValueError` for bad
arguments, `georest.cloud._http.CloudError` (a `RuntimeError`, with `.status`) for the server.
The server's `@tool` wrapper already turns both into `{"error": ...}` answers.

### What building step 3 taught, that shapes step 4

- **No shapes through the agent.** `edw_query` returns attributes, not geometry (its docstring
  says so), so "find the perimeter, then pass it to search" cannot happen in the model's
  context, and a perimeter can be megabytes. `stac_search` therefore takes a reference to the
  perimeter, fetches it server-side and passes it to `intersects`.
- **The `geometry` column is for `footprints`, not for the agent.** Drop it from tool rows;
  keep `bbox`.
- **Do not expose signing.** `sign` and `assets(sign=True)` return links carrying a live read
  token, which breaks "no secrets in tool results". The tool returns unsigned links and
  `access`; the instructions tell the model that `access="sign"` links need
  `georest.cloud.stac.sign` in the worker that reads them, and that `earthdata` and
  `requester-pays` links need that worker's own credentials.
- A mistyped collection id gives an empty table, not an error (Earth Search). The tool
  docstring should say to check ids with `stac_collections` when a search is empty.
- Search pages are 50 scenes because NASA's CMR-STAC returns 502 at 100. Nothing for step 4 to
  do; do not raise it.
- `df.attrs["truncated"]` and `df.attrs["matched"]` must be passed into `answer(...)` as
  `truncated=` and `matched=`, as `edw_query` does for `truncated`.

### Step 4 spec

| Tool | Arguments | Behaviour |
|---|---|---|
| `stac_collections` | `catalog=""`, `query=""`, `max_items=200` | Blank catalog returns the `stac.catalogs()` table, so there is no fourth tool. Otherwise `stac.collections`. Pass `truncated` through. |
| `stac_search` | `catalog`, `collections: list[str]`, `bbox`, `perimeter_service`, `perimeter_layer`, `perimeter_where`, `datetime`, `max_cloud_cover`, `max_items=50` | Give `bbox` or the three perimeter arguments, not both. The perimeter is fetched with `edw.query_features(service, layer, where=..., out_fields="objectid", max_features=20, max_allowable_offset=0.001)` and passed to `intersects`. Zero features: return an error telling the model to check the `where` with `edw_query`. Twenty or more: error asking for a narrower `where`. Clamp `max_items` to 500 and say so. Drop `geometry` from rows. Return `truncated`, `matched`, `catalog`. |
| `stac_assets` | `catalog`, `collection`, `item` | `stac.assets(..., sign=False)`, all rows, with `access`. |

- Any `catalog` that is a URL goes through `public_https_url` first; registry names do not. Add
  the new tools to the existing URL-guard test.
- Add `from georest.cloud import stac` with the other imports at the top of `server.py`. A test
  fails any import inside a tool.
- Add one short STAC paragraph to `INSTRUCTIONS`: the three tools, "links and metadata, never
  pixels", what `truncated` means, the `access` rule above, and that a fire's imagery is found
  by giving `stac_search` the same service, layer and `where` used with `edw_query`.
- Tests, in `test_mcp_server.py`: add the three names to `ToolCatalog.EXPECTED`; no `sign` or
  token parameter in any schema (the existing secrets test covers names); rows carry no
  `geometry`; `truncated` and `matched` reach the result; a bad bbox, both bbox and perimeter,
  an empty perimeter and a non-public URL each return `{"error"}`; an upstream `CloudError` is
  an answer. Stand in for the network with `mock.patch.object(server.stac, "search", ...)` and
  `server.edw`, following the `Results` class.
- Add a CHANGELOG line under Unreleased's existing `georest.cloud` entry. No version bump. Run
  `STAC_LIVE=1` checks only if a live MCP test is added to `test_live_mcp.py`.
- Assumes askterra uses `georest-mcp` (the open point below). If the user says it calls the
  Python API directly, stop and ask before building.

### Step 5 note

Both notebooks can call `stac.search(..., intersects=edw.query_features(...))` directly, with a
simplified perimeter (`max_allowable_offset=0.001`), then hand `stac.assets` links to rasterio.
Saved-output style and notebook rules are in CONTRIBUTING.md and `tests/test_example_guides.py`.

## Handoff for step 5 (one guide notebook, then docs and CI)

Written so a new session can run step 5 without re-reading steps 1 to 4. Branch
`feat/cloud-stac`, pushed through `4a0eb6d` (step 4). Commit only when the user says so,
message prefix `cloud:`, with the Co-Authored-By line the session's system reminder gives.

> **Scope: build one guide notebook to start.** That is "What imagery exists over this fire?"
> as `examples/guide_fire_imagery.ipynb`. The second guide, "Did the forest change here?", is
> *not* part of this step; it waits until the first has been reviewed. Step 5 also covers the
> docs, packaging and CI items below, which do not depend on the second guide.

### Set up

- No repo venv. Make one outside the repo and install `-e ".[dev,mcp,examples]"`, then
  `pip install rasterio` in it for the hand-off cell. Check
  `python -c "import mcp, plotly, folium, rasterio, jupyterlab"` afterwards: in step 4 an
  install looked finished while `mcp` was missing.
- Baseline at step 4: `pytest tests -q` gives 867 passed offline and `ruff check .` is clean.
  The live suites need `STAC_LIVE=1` and `EDW_LIVE=1`. `lcms_summary` in `test_live_mcp.py`
  already fails on the baseline; ignore it.
- Read only: `tests/test_example_guides.py` (the whole file, 60 lines), the first 12 cells of
  `examples/guide_fire_history.ipynb` (the pattern to copy: setup cell, markdown between every
  code cell, folium map, plotly chart), and the docstrings of `stac.search`, `assets` and
  `footprints` in `src/georest/cloud/stac.py`. The API is summarised under "Step 3 API" above.

### The notebook: what it must satisfy

- **Enforced by `test_example_guides.py`:** every code cell was run and has an
  `execution_count`; no `error` output and nothing on stderr saved (GDAL and rasterio warnings
  count, so watch the hand-off cell); at least one `from georest.<module> import ...`; only
  public modules, called through the module (`stac.search`, `edw.query_features`); no
  `import _x` or `._x`.
- **Saved with output.** Guides are the exception to the CONTRIBUTING.md advice to strip
  outputs; georest.io shows them as saved. Run end to end with
  `jupyter nbconvert --to notebook --execute --inplace examples/guide_fire_imagery.ipynb`, then
  open the JSON and confirm the chart cell holds a `text/html` output.
- **Charts carry an HTML copy** (commit `be34bcb`): copy the setup cell from
  `guide_fire_history`, including `pio.renderers.default = "notebook_connected"`, and draw
  charts with `fig.show()`. Folium maps are the cell's last expression, as in that guide.
- Style: a title and bold subtitle, a markdown cell before every code cell saying what it shows
  and why, and a closing "Where to go next" list. No emoji.

### Suggested cells

1. **Setup.** `from georest.esri import edw` and `from georest.cloud import stac`, plus pandas,
   plotly and folium as in the other guides.
2. **The fire.** Cameron Peak, Colorado, 2020, from `EDW_MTBS_01`. Find the all-years layer by
   name as `guide_fire_history` does, because per-year layer ids shift (63 was used for 2020 in
   the live tests, 84 is 2025 today). `edw.query_features(..., where="fire_name = 'CAMERON PEAK'
   and year = 2020", max_allowable_offset=0.001)` returns the perimeter as GeoJSON. Print its
   ignition date and acres.
3. **Where to look.** `stac.catalogs()`, then `stac.collections("earth-search", "sentinel")`, to
   show where `sentinel-2-l2a` comes from. One sentence on `access` (open, sign, earthdata).
4. **Search.** `stac.search("earth-search", "sentinel-2-l2a", intersects=perimeter, datetime
   from the ignition date to 90 days later, max_cloud_cover=30, max_items=200)`. Print `len`,
   `df.attrs["matched"]` and `df.attrs["truncated"]`. Pass the GeoJSON straight in; that join
   is the point of the guide.
5. **When could you see it?** A plotly scatter of scene date against cloud cover, hover showing
   the scene id. This answers "which dates are usable".
6. **Where are the scenes?** `stac.footprints(scenes)` and the perimeter on one folium map,
   scene outlines thin, the fire filled.
7. **Pick one and list its files.** The clearest post-fire scene; show `stac.assets(...)` as a
   short table (`key`, `type`, `access`, `href`) for the bands and `visual`.
8. **Hand-off to rasterio (the only cell that reads pixels).** Open the `visual` COG link,
   window it to the perimeter's bounding box (transform the box to the raster's CRS with
   `rasterio.warp.transform_bounds`), read it downsampled with `out_shape`, and show it with
   `px.imshow`. Say plainly that georest found the link and rasterio read the pixels. Keep the
   read small; this cell must run in seconds. If it writes anything to stderr, wrap it in
   `rasterio.Env(CPL_LOG="/dev/null")` or similar, or the guide test will fail.
9. **Where to go next.** Another catalog (Planetary Computer, with `stac.sign`), another
   collection (Landsat, NAIP), and a note that the MCP tools do the same search for an agent. Do
   not promise the second guide by name.

### Decisions and traps

- **Add `rasterio` to the `examples` extra** in `pyproject.toml` (with a comment like the
  others), since the guide imports it. It is not a runtime dependency; `test_packaging.py` must
  still pass.
- Cameron Peak's unsimplified perimeter has about 34,000 vertices, over the 10,000 `stac`
  allows, so simplify as above. Simplifying small fires can collapse a ring to a point; step 4
  made `stac.search` drop those, so this is handled.
- Search results depend on today's archive. Do not write numbers into the markdown that the
  output could contradict; let the printed output carry them, or phrase them loosely.
- Earth Search links are open https COGs on AWS, so the hand-off needs no credentials. Do not
  switch the guide to Planetary Computer; signed links expire inside a saved notebook.
- If a live call fails because a host is down, stop and say so rather than saving a notebook
  with an error. The guide is only valid if it ran clean.
- One guide only. If the notebook is going well, report that and stop; do not start "Did the
  forest change here?".

### The rest of step 5

None of these is done yet (checked: no `stac` in them).

- `README.md`: add `georest.cloud` to the provider table (see the `georest.noaa` row) and the
  provider sentence below it.
- `docs/api.md`: a `georest.cloud` section in the style of the `usgs` one, with the module list
  in the contents; add the new MCP tools if the file lists tools.
- `pyproject.toml`: the description and keywords (`stac`, `cloud-native`, `earthdata`).
- `tests/README.md`: the layout table, the `STAC_LIVE` flag, and the two fakes,
  `FakeStacServer` and `FakeStacSearch`.
- `.github/workflows/live.yml`: run the STAC live tests weekly. `.github/workflows/ci.yml`: the
  wheel must contain `georest/cloud/stac.py` and `georest/cloud/catalogs.py`, and the smoke test
  imports `stac`.
- `CHANGELOG.md`: one line under Unreleased for the guide. No version bump.
- Verification: `pytest tests -q`, `ruff check .`, `python -m build` with the CI wheel
  assertions run locally, and the wheel installed in a clean venv with
  `import georest.cloud.stac`.

Not for this step, but to report: `georestDocs/source/examples.rst` (a separate repo) lists the
guides by name and will need an entry for the new one; and CONTRIBUTING.md still says "zero
third-party runtime dependencies" while pandas is core. Ask the user before editing either.

## What georest builds, and what it hands off

The cloud-native ecosystem already reads this data well. Rebuilding it would add maintenance
without adding anything georest is known for. The package keeps to discovery and the joins only
it can make.

| Capability | Decision | Why |
|---|---|---|
| STAC search, bounded | build | Flat DataFrame, `truncated` flag and fast failure suit an agent tool call; pystac-client returns heavy objects. |
| Catalog and collection registry | build | Curated forestry knowledge, in the spirit of `usfs/vocab`. |
| EDW and MTBS to scenes | build | Only georest has both ends of the join. |
| Footprints for Esri tools | build | GeoJSON out, matching how `georest.esri` returns geometry. |
| COG and Zarr reading | hand off | rasterio, zarr and xarray do this; guides show the call. |
| Datacubes | hand off | odc-stac and stackstac. |
| Cloud storage access | hand off | obstore and fsspec. |
| GeoParquet queries | hand off | geopandas, pyarrow and duckdb. |

## Proposed structure

```
src/georest/cloud/
  __init__.py   docstring, __all__ = ["stac", "catalogs"]
  _http.py      private transport (retry, backoff, CloudError, User-Agent)
  _frames.py    frame(), check_bbox()
  stac.py       catalogs, collections, search, assets, footprints, sign
  catalogs.py   curated registry with notes
```

The subpackage is named `cloud` because the other packages are organizations (`esri`, `usgs`,
`noaa`) and STAC is a specification. Use it the way the other providers are used:
`from georest.cloud import stac`, then `stac.search(...)`.

## How askterra uses it

Read from the askterra source (cloned beside this repo), 2026-11-04. The earlier assumption,
that askterra already consumes georest through the MCP server, was wrong.

### What askterra is today

- **No tool calling in the chat path.** `chat2` calls `chat_simple.chat`, which streams Gemini
  with only the built-in `google_search` and `url_context` tools (or Perplexity `sonar`, which
  has none). The system prompt (`model_instructions_perplexity.py`) tells the model to answer
  with links such as `%%urlStart%%terraChange?region=xmin,ymin,xmax,ymax&...%%urlEnd%%`, and
  Django views (`terraView`, `terraChange`, `terraRisk`) render Earth Engine maps from them.
- **No MCP client, and no georest reference anywhere.** `services/modules/mcp_askterra` is a
  five-tool stub (tile URLs, asset info), not wired to anything, plus a Gemini example that
  passes an MCP session as `tools=[session]`.
- **Place is always a bbox.** Every `terra*` endpoint takes `region=xmin,ymin,xmax,ymax`,
  worldwide. Its fire lookup (`fire_services.find_fire`) reads MTBS from Earth Engine and
  returns name, event id, date, acres and a centre point, never a shape.
- **Runtime.** Python 3.13.3 on Heroku, gunicorn with gevent, 2 workers, 120 s timeout. Already
  pinned: `mcp` 1.26.0 (georest needs >=1.2,<2), `pandas` 2.2.3, `rasterio` 1.4.3, `shapely`,
  `google-genai` 1.62.0. georest needs nothing new.
- **Gemini limits.** Gemini 3 models can combine `google_search` with function declarations in
  one request (a preview feature, per
  [Google's docs](https://ai.google.dev/gemini-api/docs/tool-combination)). Gemini 2 models and
  Perplexity cannot, so tools can only be offered on the Gemini 3 path.

### Answers to the open questions

- **Does askterra consume georest through MCP?** No, not yet. Step 4's tools are still the
  right unit: they are plain Python functions that keep their signature and docstring, return
  JSON strings, and are also served by `georest-mcp` for Claude Code or Cursor. Askterra can
  pass them to Gemini as callables, with no MCP server and no subprocess.
- **Perimeter-by-reference arguments.** Keep them; they serve georest's own agents. For
  askterra the `bbox` path is primary, because the bbox is what its endpoints already carry.
  EDW perimeters are US-only, so outside the US only `bbox` applies.
- **Links and metadata, never pixels.** Fits askterra: it never puts raster data in the model's
  context. Its frontend does not render STAC links, so scene results are text for the model, or
  a new view (below).
- **Subpackage name.** No conflict found. Askterra would import `georest.mcp.server` tool
  functions or `georest.cloud.stac` directly.

### Where STAC adds to askterra

Askterra's maps come from Earth Engine composites, so the model cannot tell a user which dates
have a usable scene. STAC search answers "what imagery exists over this place and when", and
"was it cloudy". It also reaches data Earth Engine does not hold: NAIP on Planetary Computer,
HLS and GEDI from NASA, 3DEP. It supports the existing fire reports too: given the date range of
an MTBS fire from `find_fire`, the best pre and post scenes can be shown next to the burn map.

### Integration plan, in order

1. **Install and smoke test (small).** Add `georest` to askterra's `requirements.txt` (which is
   UTF-16 encoded, so edit it with a tool that keeps the encoding). Call
   `georest.mcp.server.stac_search` from a Django shell for a known bbox. Pin to a georest
   release once step 5 ships.
2. **Tool wrapper module.** New `services/modules/georest_tools.py` exporting the three STAC
   functions, wrapped to enforce askterra's own limits (a lower `max_items`, a bbox size cap
   like `constrainRegion`) and to record usage as it does for other services. Plain functions,
   so they work in `generate_content_stream` with `tools=[...]`.
3. **Gemini 3 only, behind a flag.** In `chat_simple._stream_google`, add the wrappers to the
   `gemini-3` config next to `google_search`, gated by a setting so it can be turned off. Check
   that streaming with automatic function calling works on google-genai 1.62.0; if not, run the
   call loop by hand. Perplexity and Gemini 2 are unchanged.
4. **Prompt addition.** One block in the system prompt: when to call the STAC tools
   (availability, cloud cover, dates of imagery), that results are links and metadata, to
   summarise them and never to invent scenes, and the `access` rule from the server's
   instructions. Keep the existing rule that map links use the `terra*` endpoints.
5. **Optional: a `terraScenes` view.** A new endpoint that takes `region` and dates, calls
   `stac.search` and shows footprints (`stac.footprints`) and thumbnails on the existing map
   viewer, so the model can answer with a `%%urlStart%%terraScenes?...%%urlEnd%%` link like every
   other result. This is the only step that touches the frontend.
6. **Optional: fire reports.** After `find_fire` returns a date and centre, add a scenes table
   to the report page.
7. **Tests and cost.** Askterra's `services/tests.py` is small; add offline tests with the tool
   functions mocked, as georest does. Tool calls add latency inside the 120 s window and tokens
   to the usage monitor; measure both on the staging app first.

### Alternative: run the MCP server

Askterra could instead run `georest-mcp` and point `mcp_askterra/example_gemini_mcp.py` at it.
For a Heroku web dyno this means a stdio subprocess per request or a separately hosted HTTP
server, and `georest-mcp` currently speaks stdio only. Not recommended unless askterra wants the
same tool server for external clients; if so, add a streamable-http option to `georest-mcp`
first (a small change in `main()`).

### Risks

- Tool combination with Google Search is a preview feature and can change; the flag in step 3
  is the guard.
- The model may call `stac_search` with a wrong collection id. The tool says so when the result
  is empty, and the prompt should say to call `stac_collections` first.
- Signed Planetary Computer links expire, so they should never be stored in conversation
  history. The STAC tools never return them.

## Following the repo's rules

- Every new module carries the Apache header and is covered by the attribution test.
- Offline tests use `FakeStacServer` (collections) and `FakeStacSearch` (search, items, tokens)
  in `tests/support.py`, modelled on `FakeOgcServer`. Live tests sit behind a new `STAC_LIVE`
  flag, skip when a host is down, and run weekly in `live.yml`.
- Every public function has an `Example::` section, checked by the docstring-example tests.
- CHANGELOG entries go under Unreleased, with no version bump. CI wheel checks gain the new
  modules.
- CONTRIBUTING.md still says "zero third-party runtime dependencies" while pandas is a core
  dependency. The plan follows the enforced tests; the sentence deserves a small separate fix.

## Future add-ins, if there is demand

None of these is in the first release. Lidar is the one most likely to come next.

- **Lidar point clouds** (next). COPC and EPT, including USGS 3DEP. A `copc.info()` from the LAS
  header, with reads through an optional extra. Useful for canopy height and biomass.
- **Raster and array readers** (likely). `cog.info` and `cog.read`, `zarr.describe` and
  `zarr.open`, and `stac.to_xarray`, as thin wrappers over rasterio, zarr and odc-stac.
- **Cloud-native vector** (likely). GeoParquet for Overture, Source Cooperative and
  stac-geoparquet archives, with bounding-box pruning. FlatGeobuf and PMTiles after that.
- **Unified storage** (later). S3, GCS and Azure listing and range reads through obstore, with
  anonymous S3 and GCS kept in the standard library.
- **Kerchunk and virtual Zarr** (later). Reference files that expose NetCDF, HDF5 and GRIB
  (HRRR, gridMET, Daymet) as Zarr.
- **Generic OGC API client** (later). Promote the USGS `_ogc` module to a shared `_core`, the
  refactor the transport docstrings already anticipate.
- **COG validation and writing** (later). Validate cloud-optimized layout from the header;
  writing COGs through rio-cogeo.
- **Zonal statistics** (later). Mean canopy cover per polygon over a COG, joined to FIA and LCMS
  summaries.
- **Local range-read cache** (later). Header and listing cache under `~/.georest`, following the
  `vocab` pattern.

## Open points

- Is `cloud` the right subpackage name, with `georest.stac` as the only top-level alias?
- MCP tools return links and metadata only, never pixel data. This matches askterra, which
  never sends raster data to the model.
- Answered by reading the source: askterra does not use MCP or tool calling today. Which
  integration route to take (in-process callables, recommended, or a hosted MCP server) and
  whether to add the `terraScenes` view are for the askterra owners to decide.
