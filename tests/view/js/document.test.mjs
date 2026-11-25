// The map document (js/document.js): plain data applied through the Map API.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../../../src/georest/view/static/js/state.js";
import { createMapApi } from "../../../src/georest/view/static/js/api.js";
import { applyDocument, documentUrl } from "../../../src/georest/view/static/js/document.js";

function setup() {
  const store = createStore();
  const api = createMapApi(store, { setCenter: (...a) => calls.push(["setCenter", ...a]) });
  const calls = [];
  const notes = [];
  return { store, api, calls, notes, notify: (m) => notes.push(m) };
}

const DOC = {
  geoview: 1,
  title: "A map",
  layers: [
    { id: "layer-1", type: "geojson", name: "Shapes", visible: true, opacity: 0.6, viz: { strokeColor: "ff0000" }, source: { geojson: { type: "FeatureCollection", features: [] } } },
    { id: "layer-2", type: "xyz", name: "Topo", visible: false, viz: {}, source: { url: "https://t/{z}/{x}/{y}.png", maxZoom: 15 } },
    { id: "layer-3", type: "esri.map", name: "Flood", visible: true, viz: {}, source: { url: "https://x/arcgis/rest/services/a/MapServer", options: { layers: "show:28" } } },
    { id: "layer-4", type: "ee.image", name: "SRTM", visible: true, viz: { min: 0, max: 1 }, source: { serialized: { result: "0", values: {} } } },
  ],
  view: { center: [-105, 40], zoom: 9 },
  commands: [["turnOnInspector"], ["setQueryPrecision", 2, 0.5]],
};

test("a document's layers, view and commands go through the Map API", () => {
  const s = setup();
  const out = applyDocument({ api: s.api }, DOC, { notify: s.notify });
  const ls = s.store.get().layers;
  assert.deepEqual(ls.map((l) => [l.name, l.kind, l.visible]), [["Shapes", "geojson", true], ["Topo", "tile", false], ["Flood", "esri.map", true], ["SRTM", "ee", true]]);
  assert.equal(ls[0].opacity, 0.6);
  assert.equal(ls[2].viz.esri.layers, "show:28");
  assert.equal(ls[2].viz.esriKind, "esri.map");
  assert.equal(typeof ls[1].item, "function");
  assert.equal(ls[1].item({ x: 3, y: 4 }, 5), "https://t/5/3/4.png");
  assert.equal(ls[3].viz.serialized, true);
  assert.deepEqual(s.calls, [["setCenter", -105, 40, 9]]);
  assert.equal(s.store.get().tool, "query");
  assert.deepEqual(s.store.get().query.precision, { chartPrecision: 2, chartDecimalProportion: 0.5 });
  assert.equal(s.store.get().title, "A map");
  assert.deepEqual(s.notes, []);
  assert.equal(out.layers, 4);
});

test("a command not on the list is skipped and said once", () => {
  const s = setup();
  applyDocument({ api: s.api }, { ...DOC, layers: [], commands: [["eval", "1"], ["eval", "2"], ["turnOnInspector"]] }, { notify: s.notify });
  assert.deepEqual(s.notes, ["This map asked for eval, which geoView does not run."]);
  assert.equal(s.store.get().tool, "query");
});

test("an unknown layer type is skipped; the others load", () => {
  const s = setup();
  const out = applyDocument({ api: s.api }, { ...DOC, layers: [{ id: "x", type: "script", source: {} }, DOC.layers[0]], commands: [] }, { notify: s.notify });
  assert.equal(s.store.get().layers.length, 1);
  assert.equal(out.skipped, 1);
  assert.match(s.notes[0], /script/);
});

test("Earth Engine settings: the auth proxy and the workload tag", () => {
  const s = setup();
  let proxy = null;
  applyDocument({ api: s.api, setAuthProxy: (u) => (proxy = u) }, { ...DOC, layers: [], commands: [], ee: { authProxyAPIURL: "/ee-api/t/x", workloadTag: "geeviz__t" } }, { notify: s.notify });
  assert.equal(proxy, "/ee-api/t/x");
  assert.equal(s.store.get().eeWorkloadTag, "geeviz__t");
});

test("a document is fetched only from the page's own origin", () => {
  const o = "http://127.0.0.1:8765";
  assert.equal(documentUrl("?doc=/maps/a.json", o), "http://127.0.0.1:8765/maps/a.json");
  assert.equal(documentUrl("?doc=https://evil.example/x.json", o), null);
  assert.equal(documentUrl("?doc=//evil.example/x.json", o), null);
  assert.equal(documentUrl("?other=1", o), null);
  // a path relative to the page (geeViz behind a path prefix, as on Workbench)
  assert.equal(documentUrl("?doc=../geeView/src/gee/gee-run/map-a.json", "https://host/proxy/8001/geoview/geoview.html?doc=x"), "https://host/proxy/8001/geeView/src/gee/gee-run/map-a.json");
  assert.equal(documentUrl("?doc=../../../../evil", "https://host/geoview/geoview.html"), "https://host/evil");
});

test("a chart-only ArcGIS layer: charted, not drawn, listed or queried", async () => {
  const { chartJobs } = await import("../../../src/georest/view/static/js/engine/areachart.js");
  const { queryableLayers } = await import("../../../src/georest/view/static/js/engine/query.js");
  const { esriPlugin } = await import("../../../src/georest/view/static/js/plugins/esri.js");
  const s = setup();
  applyDocument({ api: s.api }, { geoview: 1, layers: [{ id: "c", type: "esri.image", name: "Classes", visible: true, viz: { canAreaChart: true, chartOnly: true }, source: { url: "https://x/arcgis/rest/services/a/ImageServer" } }] }, { notify: s.notify });
  const st = s.store.get();
  const l = st.layers[0];
  assert.equal(l.chartOnly, true);
  assert.deepEqual(st.order, [], "no row in the layer list");
  assert.deepEqual(chartJobs(st).map((j) => j.layer.name), ["Classes"]);
  assert.deepEqual(queryableLayers(st.layers), []);
  assert.equal(esriPlugin.start(l, { addTimelapse: () => assert.fail("not a timelapse") }), true, "never drawn");
});

test("a GeoJSON layer can come from a URL, loaded by the viewer", () => {
  const s = setup();
  applyDocument({ api: s.api }, { geoview: 1, layers: [{ id: "layer-1", type: "geojson", name: "Parks", visible: true, viz: {}, source: { url: "https://example.com/parks.geojson" } }] }, { notify: s.notify });
  const [l] = s.store.get().layers;
  assert.equal(l.kind, "geojson");
  assert.deepEqual(l.item, { geojsonUrl: "https://example.com/parks.geojson" });
  assert.deepEqual(s.notes, []);
});

test("a document's brand passes its logo name to the viewer", () => {
  const s = setup();
  let brand = null;
  applyDocument({ api: s.api, setBrand: (b) => (brand = b) }, { geoview: 1, brand: { name: "geeViz ", accent: "geoView", logo: "geeviz" }, layers: [] }, { notify: s.notify });
  assert.deepEqual(brand, { name: "geeViz ", accent: "geoView", logo: "geeviz" });
});
