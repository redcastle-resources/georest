// Secured ArcGIS layers: every request for them goes through geoView's
// /proxy, which holds the token; the page never builds a URL with one.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeFetch, fixture, response } from "./esrifake.mjs";
import { esriRequest, registerSecured, setProxyBase, SECURED_MESSAGE, resetSecured } from "../../../src/georest/view/static/js/esri/rest.js";
import { exportTileUrl, tileUrl } from "../../../src/georest/view/static/js/esri/draw.js";
import { createFeatureSource } from "../../../src/georest/view/static/js/esri/features.js";
import { identifyMap } from "../../../src/georest/view/static/js/esri/identify.js";
import { esriPlugin } from "../../../src/georest/view/static/js/plugins/esri.js";

const NFHL = fixture("nfhl_info").request.url;
const STATES = fixture("states_info").request.url;
const SECURE = "https://secure.example.com/arcgis/rest/services/private/MapServer";

const inner = (u) => decodeURIComponent(new URL(u, "http://127.0.0.1:8765").searchParams.get("url"));
const layerOf = (u) => new URL(u, "http://127.0.0.1:8765").searchParams.get("layer");

test("a secured service's requests go to /proxy with its layer key, never a token", async () => {
  resetSecured();
  setProxyBase("/proxy");
  registerSecured(STATES, "k1");
  const fetch = fakeFetch([[(r) => r.base.endsWith("/proxy"), "states_query_z4"]]);
  const src = createFeatureSource(STATES, { ...response("states_info"), advancedQueryCapabilities: { supportsPagination: false } }, {}, { fetch });
  await src.partsFor({ x: 3, y: 6 }, 4);
  const u = fetch.calls[0].url;
  assert.ok(u.startsWith("/proxy?"), u);
  assert.equal(layerOf(u), "k1");
  assert.ok(inner(u).startsWith(STATES + "/query?"), inner(u));
  assert.ok(!u.includes("token"), u);
  // identify, and the export images of a map service
  registerSecured(NFHL, "k2");
  const f2 = fakeFetch([[(r) => r.base.endsWith("/proxy"), "nfhl_identify"]]);
  await identifyMap(NFHL, { lng: -105.068, lat: 40.59 }, { bounds: { west: -105.08, south: 40.58, east: -105.056, north: 40.596 }, width: 800, height: 600 }, {}, { fetch: f2, info: response("nfhl_info"), layer: { id: "l", name: "F" } });
  assert.ok(inner(f2.calls[0].url).startsWith(NFHL + "/identify?"));
  const tile = exportTileUrl(NFHL, "esri.map", {}, null)({ x: 0, y: 0 }, 1);
  assert.ok(tile.startsWith("/proxy?") && inner(tile).startsWith(NFHL + "/export?") && layerOf(tile) === "k2", tile);
  assert.ok(tileUrl(NFHL)({ x: 1, y: 2 }, 3).startsWith("/proxy?"));
  // an unsecured service is asked directly
  const open = "https://open.example.com/arcgis/rest/services/a/MapServer";
  assert.ok(exportTileUrl(open, "esri.map", {}, null)({ x: 0, y: 0 }, 1).startsWith(open + "/export?"));
});

test("a long request for a secured service is a POST to /proxy with the form body", async () => {
  resetSecured();
  setProxyBase("/proxy");
  registerSecured(STATES, "k1");
  const fetch = fakeFetch([[() => true, { ok: 1 }]]);
  const ring = Array.from({ length: 400 }, (_, i) => [-105 + i / 1000, 40]);
  await esriRequest(STATES + "/query", { geometry: { rings: [ring] } }, { fetch });
  const c = fetch.calls[0];
  assert.equal(c.method, "POST");
  assert.ok(c.url.startsWith("/proxy?") && inner(c.url) === STATES + "/query" && layerOf(c.url) === "k1");
  assert.equal(JSON.parse(c.params.geometry).rings[0].length, 400);
});

test("with no proxy behind the page a secured layer says how to see it; others draw", async () => {
  resetSecured();
  setProxyBase(null);
  const fails = [];
  const tiles = [];
  const ctx = {
    fetch: fakeFetch([[NFHL, "nfhl_info"]]),
    tileOverlay: (r, f) => (tiles.push(r.id), {}),
    setKind: (r, k) => (r.kind = k),
    setLoading: () => {},
    sync: () => {},
    fail: (r, e) => fails.push([r.id, String(e && e.message ? e.message : e)]),
  };
  await esriPlugin.draw({ id: "sec", kind: "esri.map", viz: { esri: { secured: "k9" } }, item: SECURE }, SECURE, ctx);
  await esriPlugin.draw({ id: "open", kind: "esri.map", viz: {}, item: NFHL }, NFHL, ctx);
  assert.deepEqual(fails, [["sec", SECURED_MESSAGE]]);
  assert.equal(SECURED_MESSAGE, "This layer is secured. View it from Python (Map.view()) or the geeViz agent, which hold its token.");
  assert.deepEqual(tiles, ["open"]);
});
