# geoView: one map viewer for georest and geeViz

Status: draft for approval, 2026-11-24.

## Intent

geeViz's Delta viewer becomes **geoView**, georest's map viewer. georest users
get the same viewer for ArcGIS, USGS and other REST data, with no Earth
Engine at all. geeViz keeps Earth Engine maps and uses geoView as its viewer,
since georest is already a geeViz dependency. One viewer, maintained once.

Why now: georest's guides draw maps with folium, which knows nothing of
ArcGIS services beyond a tile URL, and the Delta work (legends, query,
measure, charts) is wasted on non-Earth Engine data while it is wired to
Earth Engine.

## Goals

- **geoView lives in georest** as `georest.view`: the Delta HTML, JS and CSS
  as package data, a Python `Map`, and a small local server.
- **Earth Engine is optional.** A map with no Earth Engine layers starts and
  runs without Earth Engine sign-in or the Earth Engine JS library.
- **Layer types are plug-ins** with four parts each: draw, query (at a
  point), legend, and optionally chart (over an area). Earth Engine is one
  plug-in; Esri services, GeoJSON and XYZ tiles are others.
- **ArcGIS services behave the way the ArcGIS JS API makes them behave,**
  emulated in our own code (no ArcGIS JS API):

  | Service | Draw | Query | Legend | Chart area |
  |---|---|---|---|---|
  | Feature layer (FeatureServer, or a MapServer feature layer) | Fetched in the browser for the view as it moves, generalized for the zoom (`maxAllowableOffset`), paged (`resultOffset`); drawn on canvas tiles from the layer's `drawingInfo.renderer` (below) | Hit test on the loaded features, with the pixel tolerance Delta now uses | From the renderer | `outStatistics` within the drawn area: counts, sums or areas by a field |
  | Dynamic map service (MapServer, e.g. FEMA NFHL) | `export` per tile | `identify` (tolerance in pixels) | The service's `/legend` | `outStatistics` on its feature layers |
  | Cached tiles (MapServer or ImageServer `/tile`) | Tiles | `identify` on the service | `/legend` | as the service behind the tiles |
  | Image service (ImageServer, e.g. NAIP, LCMS) | `exportImage` per tile, with rendering rules | `identify`: the pixel value; with time, the values over time | `/legend`, with the rendering rule | `computeHistograms` over the area: class areas for a class map (with class names and colors from the service's attribute table or `/legend`), a histogram otherwise; with time, one per time step, as a chart over time and, for a class map, a Sankey between steps |

- **Time-enabled services** (any service whose JSON has `timeInfo`) can be
  timelapse layers: Delta's timelapse controls (frames, slider, play) become
  generic, one frame per time step, each drawn with the service's `time=`
  parameter. Time steps come from `timeInfo` (its extent and interval) or
  the image service's catalog dates.
- **Renderers are ours, drawn on canvas tiles** as GeoJSON is now, so the
  ArcGIS renderer types all render: simple, unique value, class breaks, visual
  variables (color, size, opacity and rotation by a field), heatmap (a
  kernel density surface redrawn as the view moves), dot density (dots
  placed in each polygon by a field), and pie chart; symbols (marker shapes,
  line dashes, fill patterns) as `drawingInfo` describes them. The legend is
  built from the same renderer, as the ArcGIS JS API builds its legend: a
  swatch per class, ramps for visual variables, a color ramp for a heatmap,
  "1 dot = N" for dot density. A renderer type ArcGIS adds later that we do
  not know draws with the layer's default symbol and says so in its legend.

- **GeoJSON layers become queryable** (hit test, as feature layers).
- **georest's `Map` has geeViz's API** wherever the method makes sense without
  Earth Engine, with the same names and arguments (list below), so code and
  habits carry over in both directions.
- **georest's examples use geoView** in place of folium.

## Non-goals and anti-goals

- No ArcGIS JS API, no change of map engine: Google Maps stays.
- geeViz's original viewer stays in geeViz, unchanged, as `setSkin("original")`.
- Wind particles stay Earth Engine only. (Timelapse and area charts are not:
  time-enabled services and `computeHistograms` give other data both.)
- Anti-goal: georest gaining an Earth Engine Python dependency. The Earth
  Engine plug-in is JavaScript the viewer loads only for a map that has Earth
  Engine layers; the Python that makes those layers stays in geeViz.
- Anti-goal: a viewer change that needs geeViz and georest released in
  lockstep for a plain bug fix. geeViz depends on a georest range, as now.

## Approaches considered

1. **In georest** (chosen, the user's suggestion): geeViz already depends on
   georest, so no new package or version pin; georest users get the viewer
   with no Earth Engine. Costs: georest grows from a light REST library into
   one that ships about 1 MB of viewer assets, and viewer fixes are georest
   releases plus a geeViz pin bump when geeViz needs them.
2. **A third package, geoview,** that both depend on. Cleanest boundaries,
   but one more package to publish and pin, and PyPI publishing is already
   the slow part.
3. **Leave it in geeViz** and have georest optionally depend on geeViz.
   Rejected: it reverses the dependency and pulls Earth Engine into georest.

## The decided parts

### Layout

```
georest/src/georest/view/
  __init__.py        Map, mapper (geeViz's names), view(), export_html()
  _document.py       the map document (below)
  _server.py         local server: static files + /proxy
  static/            the viewer: geoview.html, js/, css/, images/
```

geeViz's `geeView/delta/` moves here. geeViz's `geeView/` keeps the original
viewer and its `src/`. The two images Delta takes from `src/` (logos) and
`src/js/wind-particles.js` move with it; wind belongs to the Earth Engine
plug-in.

### The map document

Today geeViz writes a JavaScript program (`runGeeViz-<token>.js`) that calls
`Map.addLayer(...)` in the page. geoView reads a **JSON map document**
instead: `{title, layers: [{id, name, type, source, viz, visible, opacity}],
view: {center, zoom} or {bounds}, query: {...}, tools: {...}}`.

- `type` names the plug-in: `ee.image`, `ee.imageCollection`,
  `ee.vectorImage`, `ee.vector`, `ee.timelapse`, `geojson`, `xyz`,
  `esri.feature`, `esri.map`, `esri.tile`, `esri.image`.
- `source` is plug-in data: a URL and options for Esri and XYZ; GeoJSON
  inline or by URL; for Earth Engine, the serialized object geeViz writes
  today.
- A document is plain data: it can be saved, embedded in an exported page,
  diffed, and read without running code.

geeViz's `Map` writes this document for the geoView skin; it keeps writing
`runGeeViz.js` for the original skin.

### georest's `Map`

Same names and arguments as geeViz's `Map` where they apply:

| geeViz method | georest | Notes |
|---|---|---|
| `addLayer(obj, viz, name, visible, opacity)` | yes | `obj`: GeoJSON (dict, or any georest result: a FeatureCollection, a list of Features), a georest `to_frame` GeoDataFrame-like table with geometry, or a service URL (routed to the right Esri type) |
| `addTileLayer`, `addDynamicMapService` | yes | as now |
| `addEsriImageService`, `addEsriMapService`, `addEsriFeatureService`, `addEsriService` | yes | drawn in the browser from the service; Python fetches nothing. `time=` (a date, range or `"all"`) makes a time-enabled service a timelapse layer |
| `addTimeLapse` | yes, for time-enabled services | an Esri service, or a list of layers (one per frame) |
| `addSelectLayer` | yes | GeoJSON or a feature layer |
| `setCenter`, `setZoom`, `centerObject` | yes | `centerObject` takes GeoJSON, a bbox, or a layer |
| `clearMap`, `clearMapLayers`, `clearMapCommands` | yes | |
| `view`, `export_html`, `setTitle`, `setMapTitle` | yes | |
| `setQueryWindowMode`, `setQueryToInfoWindow`, `setQueryToSidePane`, `setQueryBoxColor`, `setQueryPrecision`, `turnOnInspector` | yes | |
| `turnOnUserDefinedAreaCharting`, `turnOnSelectionAreaCharting` | yes | charts from the Esri plug-ins |
| `setCanReorderLayers`, `turnOffAllLayers`, `turnOnAllLayers` | yes | |
| `addAreaChartLayer` | yes, for image and feature services | charts the service without drawing it, as geeViz's does for Earth Engine |
| `addWindLayer`, `addWindTimeLapse`, `setQueryCRS/Scale/Transform`, `centerObject(ee...)` | no | Earth Engine only: raise `NotImplementedError` naming geeViz |

`georest.view.Map` is a module-level instance, as `geeViz.geeView.Map` is;
`georest.view.mapper()` makes another.

### Serving

- `Map.view()` starts (or reuses) a local server on `127.0.0.1` that serves
  the viewer and one map document per view, and opens the page; in Jupyter
  it shows an inline frame, as geeViz does.
- `/proxy?url=` forwards GET/POST to ArcGIS REST endpoints for servers that
  do not allow cross-site requests. Guarded as geeViz's SSRF guard is: http
  and https only, public addresses only, ArcGIS REST paths
  (`/rest/services/`, `/sharing/rest/`) only.
- geeViz's existing server (the eeAuth proxy on 8889) serves geoView's static
  files from the installed georest and keeps its Earth Engine API route.

### Earth Engine as a plug-in

- The viewer loads the Earth Engine JS library and signs in only when the
  document has an `ee.*` layer. Without one, Earth Engine sign-in failing
  does not stop the map.
- The Earth Engine plug-in is today's Delta code (draw, query, legend, area
  charts, Sankey, timelapse, wind), behind the plug-in contract.

### Release order

1. Make Earth Engine optional and draw the plug-in boundary, inside geeViz's
   Delta (no move yet). Delta's tests keep passing.
2. Add the Esri, GeoJSON and XYZ plug-ins, table row by table row, each with
   offline tests on recorded responses and live tests; then renderers,
   time-enabled timelapse, and area charts from `computeHistograms` and
   `outStatistics`.
3. Move the viewer into georest as `georest.view`, add the map document and
   georest's `Map` and server; geeViz writes the document and serves
   georest's files. georest 0.6.0; geeViz requires `georest>=0.6,<0.7`.
4. Convert georest's examples and guides from folium; update both docs sites.

### Hosted pages and secured services (my calls, from the builder check)

- **The agent app** (geeViz_agent, test and production) serves geoView's
  static files at the path it uses now (`/geeView/static`), from the
  installed georest, and gets the same guarded `/proxy` route. georest's
  `export_html` keeps geeViz's arguments (`asset_base` and the
  placeholders). geeViz_agent is a release step of its own, after step 3.
- **ArcGIS tokens never reach the page.** A layer added with `token=` is
  drawn through `/proxy`, which holds the token server-side and adds it to
  the forwarded request; the map document records only that the layer is
  secured. Exports and saved maps therefore carry no token. A page with a
  secured layer and no proxy behind it says the layer needs to be viewed
  from Python or the agent.
- **geeViz's Esri methods become georest's.** `Map.addEsri*` in geeViz call
  georest's `Map` methods of the same name, so there is one implementation,
  drawn in the browser, and Python fetches nothing. `max_features`,
  `simplify` and `max_layer_mb` stay and apply to what the viewer fetches (a
  cap on features, generalization, and a size cap). Only the original skin,
  which cannot draw Esri services itself, keeps esriLib's Python fetch.

## Left to the builder

- The plug-in contract's exact JS signatures, inside the four parts above.
- How feature layers cache and drop features as the view moves.

### Decided by the user (2026-11-24)

- **Maps on georest.io are live, with files from a CDN.** In a notebook (and
  so in its saved output), a map is a small self-contained page: the map
  document inline, geoView's files from jsDelivr at that georest release
  (`gh/redcastle-resources/georest@v<version>/src/georest/view/static/`).
  Esri services that block cross-site requests cannot draw there, so the
  guides use services that allow them. Running locally, `Map.view()` still
  uses the local server and its `/proxy`.
- **Google Maps key: a default, overridable.** The current key ships as the
  default; a user sets their own with the `GOOGLE_MAPS_API_KEY` environment
  variable or `Map.setMapsKey(key)`. The shipped key's referrer restrictions
  must allow localhost, georest.io and the agent's domains (to confirm in
  the Google Cloud console before release).
