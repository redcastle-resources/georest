# geoView Step 3: georest.view, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Delta viewer moves into georest as `georest.view` (geoView): its files ship with georest, it reads a JSON map document, georest gets its own `Map` with geeViz's method names and a local server whose `/proxy` holds ArcGIS tokens, and geeViz's Delta skin writes the document and serves georest's files.

**Architecture:** `georest/src/georest/view/static/` holds the viewer (today's `geeViz/geeView/delta/` plus the logos and `wind-particles.js` it borrows from `geeView/src/`). The page takes a map document (plain JSON: layers, view, allow-listed commands) either from `?doc=<same-origin path>` or inlined in an exported page, and applies it through the existing Map API, so every layer kind from steps 1 and 2 works unchanged. `georest.view` adds the document schema, a standard-library server (static files, documents, a guarded `/proxy`), and `Map`/`mapper`. geeViz builds the same document from its layer list for the Delta skin, mounts georest's files at `/geoview`, and stops shipping its own copy. The original skin is untouched.

**Tech Stack:** Python 3.9+ standard library (`http.server`, `ipaddress`, `urllib`), vanilla ES modules, node:test, pytest, Playwright (geeViz live suite), FastAPI (geeViz eeAuth proxy and geeViz_agent mounts).

**Spec:** `C:\RCR\georest\docs\superpowers\specs\2026-11-24-geoview-design.md`, release step 3: "Move the viewer into georest as `georest.view`, add the map document and georest's `Map` and server; geeViz writes the document and serves georest's files. georest 0.6.0; geeViz requires `georest>=0.6,<0.7`." Steps 1 and 2 (`geeViz/docs/superpowers/plans/2026-11-24-geoview-step1-ee-optional.md`, `...step2-esri-plugins.md`) built what moves.

## Global Constraints

- Repos: georest (`C:\RCR\georest`, branch `main`), geeViz (`C:\RCR\geeVizBuilder\geeViz`, `master`), geeVizBuilder root (`setup.py` pin), geeViz_agent (`master`, one static mount). Stage explicit paths only; never `git add -A`. Push nothing, publish nothing (PyPI, georest.io) without the user.
- Both venvs link `site-packages/georest` to `C:\RCR\georest\src\georest` (junctions, checked 2026-11-25), so geeViz sees `georest.view` at once.
- `georest.view` uses the standard library only; IPython is imported only inside the function that shows a notebook frame. georest gains no Earth Engine dependency (spec anti-goal).
- ArcGIS tokens never reach the page: not in the document, not in an exported page, not in a URL the page builds (spec).
- `/proxy` guard (spec): http and https only, public addresses only (every address the host resolves to, and every redirect target), ArcGIS REST paths only (`/rest/services/` or `/sharing/rest/` in the path).
- Notebook pages load geoView's files from `https://cdn.jsdelivr.net/gh/redcastle-resources/georest@v<version>/src/georest/view/static/` (spec, user decision 2026-11-24). Running locally, `Map.view()` uses the local server.
- Google Maps key: the current key ships as the default; `GOOGLE_MAPS_API_KEY` or `Map.setMapsKey(key)` overrides it (user decision). geeViz keeps honoring `GOOGLE_MAPS_BROWSER_KEY` too.
- The page keeps `window.__delta` (geeViz's live suite drives it) and keeps `compat.js` with the global `Map` API, so an exported page carrying an inline run script (and `refresh_exported_html`) still works.
- Preserve each file's line endings when moving (copy bytes; do not normalize). US English; no em dashes; no truncated outputs.
- Mutation-check every test that guards new behavior.
- Commands: georest's Python tests run with the workspace venv, `C:\RCR\geeVizBuilder\venv\Scripts\python.exe -m pytest tests -q`, from `C:\RCR\georest`; JS units `node --test tests/view/js/*.test.mjs` from `C:\RCR\georest`; geeViz as before from the workspace root (live: `geeViz/tests/delta`, needs the 8889 proxy, restarted after Task 6).

## Review Focus

1. A document from somewhere else (a saved map, a `?doc=` pointing at another site, a command name not on the list): it is not fetched from another origin, an unknown command or layer type is skipped with one notice, and nothing in it is run as code. Task 2 tests.
2. `/proxy` asked for a host that resolves to a private address (127.0.0.1, 10/8, 169.254.169.254, `::1`), or for a public URL that redirects to one, or with user info in the URL, or for a non-ArcGIS path: refused with 403 and a reason, no request made to the private address. Task 3 tests.
3. A second `Map.view()` in the same Python session, or another program already on the port: the server is reused, or the next free port is taken; never a crash. Task 3 and Task 5 tests.
4. A GeoJSON property or layer name containing `</script>` in an exported page: the inline document is escaped and the page still loads its layers. Task 5 test.
5. An exported page with a secured layer opened with no proxy behind it: the layer row says it must be viewed from Python or the agent; the other layers draw. Task 4 test.

---

### Task 1: The viewer's files in georest

**Files:**
- Create: `georest/src/georest/view/__init__.py` (stub: `STATIC_DIR`), `georest/src/georest/view/static/` (from geeViz at HEAD: `geeView/delta/js/**` to `static/js/`, `geeView/delta/css/*` to `static/css/`, `geeView/delta.html` to `static/geoview.html`, `geeView/src/assets/images/geeviz-logo-light.png` and `-dark.png` and `geeViz-logo-light.png` to `static/images/`, `geeView/src/js/wind-particles.js` to `static/js/vendor/wind-particles.js`), `georest/tests/view/js/` (from `geeView/delta/tests/`, imports rewritten to `../../../src/georest/view/static/js/...`, fixtures with them), `georest/tests/view/test_static.py`, `georest/tools/copy_viewer.py` (the one-time copy, kept for provenance)
- Modify: `georest/pyproject.toml` (wheel includes `src/georest/view/static/**`; sdist already ships `/tests`), `georest/tests/test_packaging.py` (view imports only the standard library)
- Test: `tests/view/test_static.py`, `tests/view/js/*.test.mjs`

**Interfaces:**
- Produces: `georest.view.STATIC_DIR: str`. Page `static/geoview.html` references only `./js/`, `./css/`, `./images/`, `./js/vendor/` and pinned CDN URLs; the run-loader block is gone; the Maps key sits in the script URL as today (`key=AIza...`) so a builder can substitute it; the shell logo path in `js/ui/shell.js` becomes `./images/geeviz-logo-`.
- [ ] **Step 1: Write the failing tests.** `test_static.py`: `STATIC_DIR` exists; every relative `src=`/`href=` in `geoview.html` and every relative `import` in `static/js/**/*.js` resolves to a file under `STATIC_DIR`; no file under `static/` mentions `./src/` or `gee-run`; the built wheel (`python -m build --wheel` into a temp dir) lists `georest/view/static/geoview.html` and `georest/view/static/js/main.js`. `test_packaging.py`: `georest.view` imports no third-party module (same AST rule as `georest.esri`).
- [ ] **Step 2: Run** `pytest tests/view tests/test_packaging.py -q`. Expected: FAIL (no `georest.view`).
- [ ] **Step 3: Implement** the copy with `tools/copy_viewer.py` (byte copy from a given geeViz checkout, then the path rewrites above), the stub `__init__.py`, and the packaging config.
- [ ] **Step 4: Run** `pytest tests/view tests/test_packaging.py -q` and `node --test tests/view/js/*.test.mjs`. Expected: all pass; the JS count equals geeViz's (362 at step 2's end).
- [ ] **Step 5: Commit** in georest: "georest.view: geoView's files, moved from geeViz's Delta viewer".

### Task 2: The map document

**Files:**
- Create: `georest/src/georest/view/_document.py`, `georest/src/georest/view/static/js/document.js`
- Modify: `static/js/main.js` (boot: inline document, else `?doc=`, else an inline run script as today), `static/geoview.html` (an empty `<script type="application/json" id="geoview-document"></script>` slot)
- Test: `georest/tests/view/test_document.py`, `georest/tests/view/js/document.test.mjs`

**Interfaces:**
- Produces (Python): `LAYER_TYPES = ("ee.image", "ee.imageCollection", "ee.vectorImage", "ee.vector", "ee.timelapse", "ee.select", "ee.areaChart", "geojson", "xyz", "esri.feature", "esri.map", "esri.tile", "esri.image", "esri.timelapse")`; `COMMANDS`: a frozenset of the Map API methods a document may call (compat.js `MAP_METHODS` minus the layer adders, plus `setGlobal`, `setProperty`, `mapSetZoom`, `synchronousCenterObject`, `populateChartLayerSelect`); `new_document(title="") -> dict` (`{"geoview": 1, "title", "brand": None, "layers": [], "view": None, "commands": [], "ee": None}`); `add_layer(doc, type, source, viz=None, name=None, visible=True, opacity=None) -> dict` (assigns `id` "layer-<n>"); `validate(doc) -> dict` (raises `ValueError` naming the field for an unknown type, command or version); `to_json(doc) -> str` (compact, `</` written as `<\/` so it can be inlined in a page).
- Produces (JS): `applyDocument(api, doc, {notify}) -> {layers, skipped}` in `document.js`: per type, `ee.*` to `addSerializedLayer` / `addSerializedTimeLapse` / `addSerializedSelectLayer` / `areaChart.addSerializedLayer` with `source.serialized`; `geojson` to `addLayer(source.geojson, {...viz, layerType: "geoJSONVector"})` (or fetched from `source.url`, same origin or CORS); `xyz` to `addLayer((c, z) => template, {...viz, layerType: "tileMapService", maxZoom})`; `esri.*` to `addEsriLayer(source.url, {...viz, esriKind: type, esri: source.options, timelapse: type === "esri.timelapse"})`. Then `view` (`setCenter` or `synchronousCenterObject` of the bounds), `commands` in order (only names in the allow list; others skipped), `title`, `brand.name` in the shell. `documentUrl(search, origin) -> string | null`: the `doc` parameter only when it resolves to the page's own origin.
- [ ] **Step 1: Write the failing tests.**
  - `test_document.py`: `validate` accepts a document with one layer of each type; rejects `{"geoview": 2}`, a layer type `"script"`, and a command `["eval", "x"]` with `ValueError` naming them; `to_json` of a GeoJSON whose property is `"</script><script>alert(1)</script>"` contains no `</script`, and `json.loads` of it round-trips the property.
  - `document.test.mjs` (with the existing fake store and `createMapApi`): a document with geojson, xyz, esri.map and ee.image layers makes layers of kinds `geojson`, `tile`, `esri.map`, `ee` in order with their names, visibility and opacity; `commands` `[["turnOnInspector"], ["setQueryPrecision", 2, 0.5]]` set `tool` and `query.precision`; `[["eval", "1"]]` is skipped and `notify` is called once with "This map asked for eval, which geoView does not run."; an unknown layer type is skipped with a notice and the others still load; `documentUrl("?doc=/maps/a.json", "http://127.0.0.1:8765")` is that URL, `documentUrl("?doc=https://evil.example/x.json", ...)` and `"?doc=//evil.example/x"` are null.
- [ ] **Step 2: Run** `pytest tests/view/test_document.py -q` and `node --test tests/view/js/document.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** `_document.py`, `document.js`, and the boot order in `main.js` (an inline document wins; a `doc` URL is fetched with `credentials: "same-origin"`; a document that fails to load is one notice).
- [ ] **Step 4: Run** both suites and `node --test tests/view/js/*.test.mjs`. Expected: all pass.
- [ ] **Step 5: Commit** "georest.view: the map document".

### Task 3: The local server and its proxy

**Files:**
- Create: `georest/src/georest/view/_server.py`
- Test: `georest/tests/view/test_server.py`

**Interfaces:**
- Produces: `class ViewServer` with `start(host="127.0.0.1", port=0) -> str` (base URL; port 0 or a taken port picks a free one), `url_for(doc: dict) -> str` (stores the document in memory under a random token and returns `<base>/geoview/geoview.html?doc=/maps/<token>.json`), `secure(layer_key: str, token: str) -> None` (keeps a token server-side), `stop()`. `server() -> ViewServer`: one per Python process, started on first use and reused. Routes: `GET /geoview/<path>` (STATIC_DIR, `Cache-Control: no-store`), `GET /maps/<token>.json`, `GET|POST /proxy?url=<ArcGIS URL>[&layer=<key>]`.
- Produces: `check_proxy_url(url, resolve=socket.getaddrinfo) -> None` (raises `ProxyRefused(reason)`), `forward(url, method, body, headers, token=None, opener=None) -> (status, headers, bytes)`: adds `token` as a query parameter (GET) or form field (POST), follows at most 3 redirects itself, re-checking each target, and passes through `Content-Type` and `Access-Control-Allow-Origin: *` only. Both are module-level so geeViz's proxy can reuse them.
- [ ] **Step 1: Write the failing tests** (no network; `resolve` and `opener` injected):
  - `check_proxy_url` refuses `ftp://x/rest/services/a`, `https://user:pw@x/rest/services/a`, `https://x/admin`, and hosts resolving to `127.0.0.1`, `10.1.2.3`, `169.254.169.254`, `::1`, `fd00::1`, or to one public and one private address; accepts `https://services.arcgis.com/x/arcgis/rest/services/a/FeatureServer/0/query` resolving to a public address and `https://www.arcgis.com/sharing/rest/search`.
  - `forward` with a fake opener: a redirect to `http://127.0.0.1/rest/services/x` is refused (`ProxyRefused`) and the opener never asked for it; a token given for a layer is in the forwarded request's query, and the response handed back has no `Set-Cookie`.
  - A real `ViewServer` on port 0: `GET /geoview/geoview.html` is 200 `text/html`; `url_for(doc)` then `GET` of that document returns its JSON; `GET /proxy?url=http://127.0.0.1:1/rest/services/x` is 403 with the reason in the body; `GET /geoview/../../_server.py` is 404; two `server()` calls return the same instance; starting a second `ViewServer` on the first one's port gives a different port.
- [ ] **Step 2: Run** `pytest tests/view/test_server.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with `http.server.ThreadingHTTPServer` on a daemon thread; tokens map `layer_key -> token` in memory only.
- [ ] **Step 4: Run** `pytest tests/view -q`. Expected: all pass.
- [ ] **Step 5: Commit** "georest.view: local server with a guarded ArcGIS proxy".

### Task 4: Secured layers drawn through the proxy

**Files:**
- Modify: `static/js/esri/rest.js` (a request through `proxied(url, esriOpts)`), `static/js/esri/draw.js` and `static/js/esri/features.js` and `static/js/esri/identify.js` and `static/js/esri/charts.js` (every URL they build for a layer goes through it), `static/js/plugins/esri.js` (a secured layer with no proxy fails with the message below)
- Test: `georest/tests/view/js/esri_proxy.test.mjs`

**Interfaces:**
- Consumes: Task 3's `/proxy?url=&layer=`.
- Produces: `viz.esri.secured: string` (the layer key; set by the Python side, never a token) and the page-level `proxy` base from the document (`doc.proxy`, `"/proxy"` when served by `ViewServer`, absent in exports and CDN pages). `proxied(url, esriOpts, proxyBase) -> string`: `${proxyBase}?url=<encoded>&layer=<key>` for a secured layer, else `url`.
- [ ] **Step 1: Write the failing tests:** with `secured: "k1"` and proxy `/proxy`, the export URL, a feature tile query and an identify all go to `/proxy?url=...&layer=k1` and none contains `token=`; a POST (long geometry) goes to `/proxy` with the form body; without a proxy, drawing the secured layer fails the layer with "This layer is secured. View it from Python (Map.view()) or the geeViz agent, which hold its token." and an unsecured layer on the same map draws.
- [ ] **Step 2: Run** `node --test tests/view/js/esri_proxy.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the routing and the message.
- [ ] **Step 4: Run** `node --test tests/view/js/*.test.mjs`. Expected: all pass.
- [ ] **Step 5: Commit** "georest.view: secured ArcGIS layers through the proxy".

### Task 5: georest's `Map`

**Files:**
- Create: `georest/src/georest/view/_map.py` (the `Map` class), `georest/src/georest/view/_page.py` (`page_html(doc, asset_base, maps_key)`)
- Modify: `georest/src/georest/view/__init__.py` (`Map` instance, `mapper`, `view`, `export_html`), `georest/src/georest/__init__.py` (lazy `view` in `_LAZY`)
- Test: `georest/tests/view/test_map.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `class mapper` (alias `Map` for the class is not exported; `georest.view.Map` is a module-level `mapper()` instance, as geeViz's). Methods and arguments as geeViz's, per the spec table:
  - `addLayer(obj, viz=None, name=None, visible=True, opacity=None)`: `obj` a GeoJSON dict, a list of Features, a georest result with `__geo_interface__` or a `to_frame`/GeoDataFrame-like table with a `geometry` column (converted with `__geo_interface__`), or an ArcGIS URL string (routed to the Esri method by `.../ImageServer`, `.../MapServer[/n]`, `.../FeatureServer[/n]`).
  - `addTileLayer(url_template, name="Tile Layer", visible=True, opacity=1.0, max_zoom=20, legend=None)`, `addDynamicMapService(service_url, name=..., visible=True, layers="", ...)` (an `esri.map` layer).
  - `addEsriImageService`, `addEsriMapService`, `addEsriFeatureService`, `addEsriService` with geeViz's arguments (Task 4 of step 2), `token=` registered with `server().secure()` under a fresh key, `time=` as in geeViz.
  - `addTimeLapse(url_or_layers, viz=None, name=None, visible=True)`: an ArcGIS URL is an `esri.timelapse` layer; a list of layers raises `NotImplementedError("A timelapse from a list of layers is not supported yet; give a time-enabled ArcGIS service URL.")`.
  - `addSelectLayer(obj, viz=None, name=None)` raises `NotImplementedError("addSelectLayer for GeoJSON and ArcGIS layers is not supported yet; add the layer with addLayer and chart a drawn or uploaded area.")`: select layers outside Earth Engine need viewer work this step does not do (left to a later step, below).
  - `addAreaChartLayer(url, viz=None, name=None)` (an image or feature service charted, not drawn).
  - `setCenter(lng, lat, zoom=None)`, `setZoom(zoom)`, `centerObject(obj, zoom=None)` (GeoJSON, `[w, s, e, n]` or a layer name), `clearMap()`, `clearMapLayers()`, `clearMapCommands()`, `setTitle(t)`, `setMapTitle(t)`, `setQueryWindowMode(m)`, `setQueryToInfoWindow()`, `setQueryToSidePane()`, `setQueryBoxColor(c)`, `setQueryPrecision(chartPrecision=3, chartDecimalProportion=0.25)`, `turnOnInspector()`, `turnOnUserDefinedAreaCharting()`, `turnOnSelectionAreaCharting()`, `turnOnAutoAreaCharting()`, `setCanReorderLayers(b)`, `turnOffAllLayers()`, `turnOnAllLayers()`, `setMapsKey(key)`.
  - Earth Engine only, each raising `NotImplementedError("<name> needs Earth Engine: use geeViz's Map (geeViz.geeView.Map).")`: `addWindLayer`, `addWindTimeLapse`, `setQueryCRS`, `setQueryScale`, `setQueryTransform`, and `centerObject` given an object with a `getInfo` method.
  - `document() -> dict` (validated), `view(open_browser=None, open_iframe=None, iframe_height=525) -> str` (the page URL; in Jupyter an `IFrame`, else the browser), `export_html(filename=None, asset_base=None, title=None) -> str` (the page; `asset_base` defaults to the jsDelivr URL for this version; written to `filename` when given). The Maps key: `setMapsKey` value, else `GOOGLE_MAPS_API_KEY`, else the shipped default.
- [ ] **Step 1: Write the failing tests:** each add method makes the document layer the table says (type, source, viz) and the document validates; `addLayer("https://.../FeatureServer/0")` is `esri.feature`; `addEsriFeatureService(url, token="SECRET")` puts no `SECRET` anywhere in `to_json(Map.document())` or `export_html()` and marks `viz.esri.secured`; `setMapsKey("AIzaTEST")` and the env var each appear in `export_html()` instead of the default; `export_html()` loads `https://cdn.jsdelivr.net/gh/redcastle-resources/georest@v0.6.0/src/georest/view/static/js/main.js` when `__version__` is 0.6.0 and has the inline document; a GeoJSON property `"</script>"` exported leaves exactly one `</script>` per script tag; each Earth Engine method raises with "geeViz"; `view(open_browser=False, open_iframe=False)` twice gives two URLs on one server and each document is served.
- [ ] **Step 2: Run** `pytest tests/view/test_map.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `_map.py` and `_page.py` (`page_html` puts `<base href=asset_base/>`, the key and the inline document into `geoview.html`).
- [ ] **Step 4: Run** `pytest tests -q -m "not live"` in georest. Expected: all pass.
- [ ] **Step 5: Commit** "georest.view: Map, mapper, view and export_html".

### Task 6: geeViz writes the document and serves georest's files

**Files:**
- Modify: `geeViz/geeView.py` (`_build_document()`; Delta `view()` writes `map-<token>.json` in `geeView/src/gee/gee-run/` and opens `/geoview/geoview.html?doc=/geeView/src/gee/gee-run/map-<token>.json`; `export_html` and `_export_page` for Delta use georest's `page_html` with `geoview_base = asset_base + "/geoview"`; `_GeeVizRequestHandler` serves `/geoview/` from `georest.view.STATIC_DIR`), `geeViz/viewerPages.py` (Delta page from georest; `refresh_exported_html` keeps working on pages that carry a run script), `geeViz/eeAuth/server.py` (mount `/geoview` from `georest.view.STATIC_DIR`, no-store), `geeViz/tests/test_delta_skin.py` (the five tests rewritten: the original skin's run script is unchanged byte for byte; the Delta document has the same layers, names and order as the original's run script; the Delta page's tags are georest's), `geeViz/tests/delta/conftest.py` and any test reading `/geeView/delta.html`
- Delete: `geeViz/geeView/delta.html`, `geeViz/geeView/delta/` (moved to georest in Task 1)
- Test: `geeViz/tests/test_delta_document.py` (new), the live suite `geeViz/tests/delta`

**Interfaces:**
- Consumes: Task 2 (`new_document`, `add_layer`, `validate`, `to_json`), Task 5 (`page_html`), `georest.view.STATIC_DIR`.
- Produces: `mapper._build_document() -> dict`: each `idDict` becomes a layer (`addSerializedLayer` to `ee.image`/`ee.imageCollection`/`ee.vectorImage`/`ee.vector` by the viz `layerType`, `addSerializedTimeLapse` to `ee.timelapse`, `addSerializedSelectLayer` to `ee.select`, `areaChart.addSerializedLayer` to `ee.areaChart`, `_is_tile_url` to `xyz`, `_is_dynamic_esri` to `esri.map`, `_is_esri` to its kind, a GeoJSON dict to `geojson`), with the workload tag in the viz as today; each `mapCommandList` string becomes a command by `_command_to_data(cmd) -> list` (`Map.<m>(<json args>)`, `map.setZoom(n)` to `["mapSetZoom", n]`, `<global> = <json>` to `["setGlobal", name, value]`, `Map.<prop> = <json>` to `["setProperty", prop, value]`, `synchronousCenterObject(<json>)`), raising `ValueError` naming any command it cannot read; `doc["ee"] = {"authProxyAPIURL": ..., "workloadTag": ...}` from what the run script prefix sets today. The exported Delta page is georest's `page_html` plus geeViz's sign-in script (the `urlParams` block with the token, time, project and auth proxy placeholders, as `_export_page` writes it today), inserted before `js/main.js`, so the agent's placeholder substitution keeps working.
- [ ] **Step 1: Write the failing tests** (`test_delta_document.py`): a Delta mapper with an ee.Image, a timelapse, a select layer, an area-chart layer, a tile layer, an Esri layer and a GeoJSON dict, plus `setCenter`, `turnOnInspector`, `setQueryPrecision`, `setTitle`, `setYLabelMaxLength`, `setCanReorderLayers(False)`: `_build_document()` validates with georest's `validate`, has the seven layers in order with their types, and the commands as data; every command method geeViz has (enumerated from the class: methods whose source appends to `mapCommandList`) produces a command `_command_to_data` reads; `view()` (with the proxy stubbed) writes `map-<token>.json` and returns a `/geoview/geoview.html?doc=` URL; `export_html` for Delta contains the inline document and `asset_base + "/geoview/js/main.js"`; the original skin's `export_html` is unchanged against a saved copy made before the change.
- [ ] **Step 2: Run** `venv/Scripts/python.exe -m pytest geeViz/tests/test_delta_document.py geeViz/tests/test_delta_skin.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, delete geeViz's Delta copy, and restart the 8889 proxy (per the workspace note) so it mounts `/geoview`.
- [ ] **Step 4: Run** the geeViz non-live suite and the full live suite `geeViz/tests/delta` (background, about 25 minutes). Expected: all pass except tests already failing before this step for other reasons (esriLib's 11, the version stamps, data drift), each named in the ledger.
- [ ] **Step 5: Commit** in geeViz: "Delta: geoView from georest; the Delta skin writes a map document".

### Task 7: The agent serves georest's files for exported pages

**Files:**
- Modify: `geeViz_agent/run_ui.py` (mount `georest.view.STATIC_DIR` at `/geeView/static/geoview`, before the `/geeView/static` mount, with the same revalidating static class)
- Test: `geeViz_agent/tests/test_geoview_mount.py`

**Interfaces:**
- Consumes: Task 6's `geoview_base = asset_base + "/geoview"`.
- [ ] **Step 1: Write the failing test:** with the app's test client, `GET /geeView/static/geoview/geoview.html` is 200 and `GET /geeView/static/geoview/js/main.js` is `text/javascript`; an exported Delta page built with `asset_base="/geeView/static"` references only URLs the app serves (each relative asset fetched returns 200).
- [ ] **Step 2: Run** `cd geeViz_agent && python -m pytest tests/test_geoview_mount.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the mount.
- [ ] **Step 4: Run** the agent suite. Expected: all pass.
- [ ] **Step 5: Commit** in geeViz_agent: "Serve geoView (georest.view) for exported Delta maps". Do not deploy.

### Task 8: Versions, pins, notes

**Files:**
- Modify: `georest/src/georest/__init__.py` (`__version__ = "0.6.0"`), `georest/CHANGELOG.md`, `georest/README.md` (a "Maps" section: `from georest.view import Map`, one example), `geeVizBuilder/setup.py` and `geeVizBuilder/requirements.txt` (`georest>=0.6,<0.7`), `geeViz/release-notes.md` (the Delta viewer now ships in georest; exported pages need `asset_base + "/geoview"` served)
- Test: none new.
- [ ] **Step 1: Run** the georest suite (`pytest tests -q -m "not live"`, `node --test tests/view/js/*.test.mjs`), the geeViz non-live suite, and open one georest map (`Map.addEsriFeatureService(...); Map.view()`) and one geeViz map with Earth Engine and Esri layers. Expected: suites pass as in Task 6; both maps draw, query and chart.
- [ ] **Step 2: Commit** in each repo: "georest 0.6.0: georest.view", "setup.py: georest>=0.6,<0.7", "Release notes: Delta from georest.view".

## Left to step 4

`addSelectLayer` for GeoJSON and ArcGIS feature layers (select by click, chart the selection), georest's examples and guides moved from folium to `georest.view`, both docs sites (georest.io and geeVizDocs), confirming the Maps key's referrer list in the Google Cloud console, and the agent's release. geeViz's Python `token=` layers keep esriLib's fetch until geeViz's own proxy holds tokens (`forward` from Task 3 is ready for it).
