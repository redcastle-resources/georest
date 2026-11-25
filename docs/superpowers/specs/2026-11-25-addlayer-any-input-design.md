# addLayer takes anything; tile layers answer Query

Approved in conversation, 2026-11-25. Applies to `georest.view.Map` and
geeViz's `Map` (both skins' Python side; the viewer behavior is geoView's).

## Intent

One way to put data on a map: `Map.addLayer(thing, viz, name)` works out what
`thing` is, and `Map.addTimeLapse(thing, viz, name)` does the same for
time series. The specific methods (`addEsriFeatureService`, `addTileLayer`,
...) keep working for existing code and for forcing a type. A tile layer
answers the Query tool with the color under the click, and the class it
stands for when the layer has a legend.

## What addLayer accepts

| First argument | Becomes |
|---|---|
| ArcGIS REST URL (`.../FeatureServer[/n]`, `.../MapServer[/n]`, `.../ImageServer`) | `esri.feature` / `esri.map` / `esri.image`, as `addEsriService` decides today |
| URL with `{z}`, `{x}` and `{y}` | `xyz` tile layer |
| URL ending `.geojson` / `.json`, or with `f=geojson` | `geojson` loaded by the viewer from the URL |
| GeoJSON text (a string starting with `{` or `[`) | `geojson` |
| GeoJSON dict, Features list, `__geo_interface__`, table with geometry | `geojson` (unchanged) |
| georest portal search result (dict with `url`) | as its URL |
| Earth Engine asset id (geeViz only) | `ee.data.getAsset` gives its type: Image, ImageCollection, Table/FeatureCollection, added as that ee object |
| Earth Engine object (geeViz only) | unchanged |

Anything else raises `ValueError` (strings) or `TypeError`, listing what is
accepted. In georest an asset id raises, saying it needs geeViz.

`addTimeLapse` accepts an `ee.ImageCollection`, an image collection asset id
(geeViz), or a time-enabled ArcGIS service URL. Docstrings and docs tell
users to use `addTimeLapse`, not `addLayer`, for a time-enabled image
service they want as a time lapse.

## viz parameters

- `classLegendDict` (`{name: hex}`) is the legend for every layer type;
  `addTileLayer(legend=)` stays as an alias.
- `reducer` on an ArcGIS image service: how overlapping rasters in the time
  window (or the service) combine. Accepts `"first" | "last" | "min" | "max" |
  "mean" | "sum" | "blend"` or an `ee.Reducer` with one of those names
  (`ee.Reducer.max()`), and becomes `mosaicRule
  {"mosaicMethod": "esriMosaicNone", "mosaicOperation": "MT_MAX"}`. An
  explicit `mosaicRule` wins. Other reducers (median, ...) raise ValueError
  naming the ones ArcGIS supports. Verified live: Landsat/MS ImageServer
  returns different images for MT_FIRST, MT_MAX, MT_MEAN over one window.
- ArcGIS options already read from viz (`layers`, `renderingRule`,
  `mosaicRule`, `bandIds`, `outFields`) plus `where`, `maxFeatures`, `time`
  for addLayer. `token=` stays a keyword argument, never a viz key.

## Tile Query

The viewer reads the pixel under the click from the tile at the map's zoom
(capped at the layer's maxZoom), fetched with CORS. Result is a `value` card:
swatch + hex; with `classLegendDict`, the nearest legend color within RGB
distance 24 gives the class name (swatch, name, "value #hex"). Transparent
pixel: "No data at this point". Unreadable tile (no CORS, failed fetch):
the card says the layer's colors could not be read.

## Calls made (mine)

- GeoJSON by URL is fetched by the viewer, not by Python: no Python network
  call at addLayer, and data stays current.
- Nearest-color threshold 24 (Euclidean RGB): absorbs resampling and JPEG
  noise, smaller than the gap between distinct legend colors in practice.
