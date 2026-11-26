// The map document (georest.view._document): a map as plain data, applied
// through the Map API, so every layer kind the viewer draws works the same
// from a document as from a map script. Nothing in a document runs as code:
// layers are typed data, and commands are names from a fixed list.

// The map commands a document may ask for (georest.view._document.COMMANDS;
// tests/view/test_document.py checks the two lists match).
export const COMMANDS = [
  "centerObject", "setCenter", "setTitle", "setDefaultWorkloadTag",
  "turnOnInspector", "turnOffInspector",
  "turnOnAutoAreaCharting", "turnOffAutoAreaCharting",
  "turnOnUserDefinedAreaCharting", "turnOffUserDefinedAreaCharting",
  "turnOnSelectionAreaCharting", "turnOffSelectionAreaCharting",
  "turnOnTimeSeriesCharting", "turnOffTimeSeriesCharting",
  "hideSelectLayerAreaCharting", "showSelectLayerAreaCharting",
  "setQueryCRS", "setQueryScale", "setQueryTransform", "setQueryPrecision",
  "setQueryDateFormat", "setQueryBoxColor", "showSpinner", "hideSpinner",
  "turnOffAllLayers", "turnOnAllLayers",
  "setGlobal", "setProperty", "mapSetZoom", "synchronousCenterObject",
  "populateChartLayerSelect",
];
const PROPERTIES = ["canReorderLayers", "turnOffLayersWhenTimeLapseIsOn"];

// The document's URL from the page's ?doc= (resolved against the page's own
// address, so a path relative to the page works behind a path prefix), only
// when it is on the page's own origin (a map is never read from another
// site); else null.
export function documentUrl(search, pageUrl) {
  const v = new URLSearchParams(search).get("doc");
  if (!v) return null;
  try {
    const u = new URL(v, pageUrl);
    return u.origin === new URL(pageUrl).origin ? u.href : null;
  } catch (e) {
    return null;
  }
}

const withOpacity = (viz, layer) => (typeof layer.opacity === "number" ? { ...viz, opacity: layer.opacity } : viz);

function addOne(api, layer) {
  const viz = withOpacity({ ...(layer.viz || {}) }, layer);
  const src = layer.source || {};
  const name = layer.name;
  const visible = layer.visible !== false;
  const t = layer.type;
  if (t === "ee.timelapse") return api.addSerializedTimeLapse(src.serialized, viz, name, visible);
  if (t === "ee.select") return api.addSerializedSelectLayer(src.serialized, viz, name, visible);
  if (t === "ee.areaChart") return api.areaChart.addSerializedLayer(src.serialized, viz, name, visible);
  if (t.startsWith("ee.")) return api.addSerializedLayer(src.serialized, viz, name, visible);
  if (t === "geojson") {
    // GeoJSON by URL is loaded by the layer engine when the layer is drawn
    const item = src.geojson || (src.url ? { geojsonUrl: String(src.url) } : null);
    if (!item) throw new Error("A GeoJSON layer needs geojson or url in its source.");
    return api.addLayer(item, { ...viz, layerType: "geoJSONVector" }, name, visible);
  }
  if (t === "xyz") {
    const tmpl = String(src.url);
    const urlFor = (c, z) => tmpl.replace("{x}", c.x).replace("{y}", c.y).replace("{z}", z);
    return api.addLayer(urlFor, { ...viz, layerType: "tileMapService", maxZoom: src.maxZoom === undefined ? viz.maxZoom : src.maxZoom }, name, visible);
  }
  if (t === "xyz.timelapse") {
    if (!Array.isArray(src.frames) || !src.frames.length) throw new Error("A tile time lapse needs frames in its source.");
    return api.addTileTimeLapse(src.frames, { ...viz, maxZoom: src.maxZoom === undefined ? viz.maxZoom : src.maxZoom }, name, visible);
  }
  if (t.startsWith("esri.")) {
    return api.addEsriLayer(src.url, { ...viz, esriKind: t === "esri.timelapse" ? viz.esriKind : t, esri: { ...(src.options || {}), ...(viz.esri || {}) }, timelapse: t === "esri.timelapse" }, name, visible);
  }
  throw new Error(`layer type ${t} is not one geoView draws`);
}

function runCommand(target, name, args) {
  const api = target.api;
  if (name === "setGlobal") return api.setGlobal(args[0], args[1]);
  if (name === "setProperty") {
    if (PROPERTIES.indexOf(args[0]) > -1) api[args[0]] = args[1];
    return;
  }
  if (name === "mapSetZoom") return target.map && target.map.setZoom(args[0]);
  if (name === "populateChartLayerSelect") return api.areaChart.populateChartLayerSelect();
  if (name === "synchronousCenterObject") return (target.synchronousCenterObject || api.synchronousCenterObject)(...args);
  if (typeof api[name] === "function") return api[name](...args);
}

// applyDocument(target, doc, {notify}) -> {layers, skipped}. target: {api
// (createMapApi), map (the Google map, for mapSetZoom),
// synchronousCenterObject, setAuthProxy(url), setBrand({name, accent, logo})}.
export function applyDocument(target, doc, opts = {}) {
  const notify = opts.notify || (() => {});
  const api = target.api;
  let layers = 0;
  let skipped = 0;
  const ee = doc.ee || null;
  if (ee && ee.authProxyAPIURL && target.setAuthProxy) target.setAuthProxy(ee.authProxyAPIURL);
  if (ee && ee.workloadTag !== undefined && ee.workloadTag !== null) api.setDefaultWorkloadTag(ee.workloadTag);
  if (doc.title) api.setTitle(doc.title);
  if (target.setProxy) target.setProxy(doc.proxy || null);
  if (doc.brand && doc.brand.name && target.setBrand) {
    const brand = { name: String(doc.brand.name), accent: doc.brand.accent ? String(doc.brand.accent) : "" };
    if (doc.brand.logo) brand.logo = String(doc.brand.logo);
    target.setBrand(brand);
  }
  (doc.layers || []).forEach((layer) => {
    try {
      addOne(api, layer);
      layers++;
    } catch (e) {
      skipped++;
      notify(`${layer.name || layer.id || "A layer"} was not added: ${e && e.message ? e.message : e}`);
    }
  });
  const view = doc.view;
  if (view && Array.isArray(view.center)) api.setCenter(view.center[0], view.center[1], view.zoom);
  else if (view && Array.isArray(view.bounds)) {
    const [w, s, e, n] = view.bounds;
    runCommand(target, "synchronousCenterObject", [{ type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] }]);
  }
  const refused = new Set();
  (doc.commands || []).forEach((c) => {
    const [name, ...args] = Array.isArray(c) ? c : [];
    if (COMMANDS.indexOf(name) === -1) {
      if (!refused.has(name)) notify(`This map asked for ${name}, which geoView does not run.`);
      refused.add(name);
      return;
    }
    try {
      runCommand(target, name, args);
    } catch (e) {
      notify(`The map command ${name} failed: ${e && e.message ? e.message : e}`);
    }
  });
  return { layers, skipped };
}
