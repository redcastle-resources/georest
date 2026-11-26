// Chart area on tile layers end to end, with synthetic tiles (no network):
// class areas, a mean, colors with no legend, a time lapse over time, a
// Sankey between periods, and a tile server that refuses CORS. Plus the tile
// time lapse's frame loader and how a document, the Map API and the plug-in
// registry carry one.
import { test } from "node:test";
import assert from "node:assert/strict";
import { tileChart, pixelArea } from "../../../src/georest/view/static/js/engine/tilechart.js";
import { tileFrameLoader, framesOf, templateUrl } from "../../../src/georest/view/static/js/engine/tileframes.js";
import { chartParams } from "../../../src/georest/view/static/js/engine/areachart.js";
import { pluginFor } from "../../../src/georest/view/static/js/plugins/index.js";
import { isTimelapseKind } from "../../../src/georest/view/static/js/util.js";
import { createStore } from "../../../src/georest/view/static/js/state.js";
import { createMapApi } from "../../../src/georest/view/static/js/api.js";
import { applyDocument } from "../../../src/georest/view/static/js/document.js";

const Z = 8;
const TX = 53;
const TY = 97;
// a tile's corner in degrees
const lngOf = (x, z) => (x / 2 ** z) * 360 - 180;
const latOf = (y, z) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / 2 ** z))) * 180) / Math.PI;
// the whole of tile (TX, TY), as an area
const W = lngOf(TX, Z), E = lngOf(TX + 1, Z), N = latOf(TY, Z), S = latOf(TY + 1, Z);
const TILE_AREA = { geojson: { type: "Polygon", coordinates: [[[W, S], [E, S], [E, N], [W, N], [W, S]]] } };
const CTX = { units: "metric", showAs: "area", areaLabel: "Tile", areaKey: "k", precision: null };

// a 256 x 256 tile: fill(col, row) -> [r, g, b, a]
function tile(fill) {
  const data = new Uint8ClampedArray(256 * 256 * 4);
  for (let row = 0; row < 256; row++)
    for (let col = 0; col < 256; col++) {
      const px = fill(col, row);
      data.set(px, (row * 256 + col) * 4);
    }
  return { data, width: 256, height: 256 };
}
// the ground area of the tile's left or right half, in hectares
const halfHa = () => {
  let a = 0;
  for (let row = 0; row < 256; row++) a += pixelArea(Z, TY, row, 256) * 128;
  return a / 10000;
};
const near = (a, b, tol = 0.01) => Math.abs(a - b) <= Math.abs(b) * tol;

const LEGEND = { Forest: "228b22", Water: "0000ff" };
const tileLayer = (viz) => ({ id: "t", name: "Cover", kind: "tile", item: templateUrl("https://t/{z}/{x}/{y}.png"), viz: { maxZoom: Z, canAreaChart: true, ...viz } });

test("a thematic tile layer charts class areas over the area", async () => {
  const asked = [];
  const loadTile = async (url) => {
    asked.push(url);
    // forest on the left, water on the right, a strip of gray (no class) at the top
    return tile((c, r) => (r < 16 ? [128, 128, 128, 255] : c < 128 ? [34, 139, 34, 255] : [0, 0, 255, 255]));
  };
  const layer = tileLayer({ classLegendDict: LEGEND });
  const entry = await tileChart(layer, TILE_AREA, { zoom: 12, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile });
  assert.ok(asked.includes(`https://t/${Z}/${TX}/${TY}.png`), asked.join(" "));
  assert.equal(asked.length, 1, "zoom capped at maxZoom: one tile");
  const [r] = entry.results(CTX);
  assert.equal(r.kind, "chart");
  assert.deepEqual(r.data.columns, ["Forest", "Water"], "in the legend's order");
  assert.equal(r.data.colors[r.data.columns.indexOf("Water")], "#0000ff");
  const forest = r.data.rows[0].values[r.data.columns.indexOf("Forest")];
  // 240 of 256 rows are forest on the left half
  let a = 0;
  for (let row = 16; row < 256; row++) a += pixelArea(Z, TY, row, 256) * 128;
  assert.ok(near(forest, a / 10000, 0.01), `${forest} vs ${a / 10000}`);
  // the gray strip matches no class: left out, and said so
  assert.match(r.meta, /4,096 pixels matching no class left out/);
  assert.match(r.meta, /\d+ m/);
  // as a percentage, of the classed pixels
  const [p] = entry.results({ ...CTX, showAs: "pct" });
  const vals = p.data.rows[0].values;
  assert.ok(near(vals[0] + vals[1], 100, 0.001));
});

test("a continuous tile layer charts the area's mean, read back along the palette", async () => {
  // black on the left (0), white on the right (10): the mean is about 5
  const loadTile = async () => tile((c) => (c < 128 ? [0, 0, 0, 255] : [255, 255, 255, 255]));
  const layer = tileLayer({ palette: ["000000", "ffffff"], min: 0, max: 10 });
  const entry = await tileChart(layer, TILE_AREA, { zoom: 8, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile });
  const [r] = entry.results(CTX);
  assert.deepEqual(r.data.columns, ["Mean"]);
  assert.equal(r.data.yLabel, "Mean");
  assert.ok(Math.abs(r.data.rows[0].values[0] - 5) < 0.01, String(r.data.rows[0].values[0]));
  assert.doesNotMatch(r.meta, /left out/);
});

test("transparent pixels are no data: left out of the mean and the areas", async () => {
  const loadTile = async () => tile((c) => (c < 128 ? [0, 0, 0, 0] : [255, 255, 255, 255]));
  const layer = tileLayer({ palette: ["000000", "ffffff"], min: 0, max: 10 });
  const [r] = (await tileChart(layer, TILE_AREA, { zoom: 8, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile })).results(CTX);
  assert.ok(Math.abs(r.data.rows[0].values[0] - 10) < 1e-9, String(r.data.rows[0].values[0]));
});

test("a tile layer with no legend charts area by color: the largest colors, the rest as Other", async () => {
  // 14 vertical bands of distinct colors, wider to the left; JPEG-like noise on the first
  const colors = Array.from({ length: 14 }, (_, i) => [i * 18, 255 - i * 18, (i * 37) % 256]);
  const edges = [];
  let x = 0;
  for (let i = 0; i < 14; i++) edges.push((x += 32 - i * 2));
  const loadTile = async () =>
    tile((c, r) => {
      const i = edges.findIndex((e) => c < e);
      const k = i === -1 ? 13 : i;
      const px = colors[k].slice();
      if (k === 0 && (c + r) % 3 === 0) px[0] = Math.min(255, px[0] + 2);
      return [...px, 255];
    });
  const layer = tileLayer({});
  const [r] = (await tileChart(layer, TILE_AREA, { zoom: 8, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile })).results(CTX);
  assert.equal(r.data.columns.length, 13, r.data.columns.join(","));
  assert.equal(r.data.columns[r.data.columns.length - 1], "Other");
  assert.ok(r.data.columns.slice(0, 12).every((c) => /^#[0-9a-f]{6}$/.test(c)));
  // each color is its own swatch; the noisy band is one group named by its most common color
  const first = "#" + colors[0].map((v) => v.toString(16).padStart(2, "0")).join("");
  assert.ok(r.data.columns.includes(first), r.data.columns.join(","));
  assert.equal(r.data.colors[r.data.columns.indexOf(first)], first);
});

test("a server that refuses CORS gives an error naming the layer", async () => {
  const loadTile = async () => {
    const e = new Error("Failed to fetch");
    e.cors = true;
    throw e;
  };
  const layer = tileLayer({ classLegendDict: LEGEND });
  await assert.rejects(tileChart(layer, TILE_AREA, { zoom: 8, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile }), /Cover cannot be charted: its tile server does not let the browser read its pixels \(CORS\)/);
});

test("an area with no pixels says so instead of an empty chart", async () => {
  const loadTile = async () => null;
  const layer = tileLayer({ classLegendDict: LEGEND });
  await assert.rejects(tileChart(layer, TILE_AREA, { zoom: 8, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile }), /Cover has no pixels in this area/);
});

// a time lapse: forest turns to water from the right, a quarter of the tile per frame
const FRAMES = [
  { label: "2001", url: "https://t/2001/{z}/{y}/{x}.png" },
  { label: "2002", url: "https://t/2002/{z}/{y}/{x}.png" },
  { label: "2003", url: "https://t/2003/{z}/{y}/{x}.png" },
];
const lapseTile = async (url) => {
  const year = Number(/\/(\d{4})\//.exec(url)[1]);
  const waterFrom = 256 - (year - 2000) * 64;
  return tile((c) => (c >= waterFrom ? [0, 0, 255, 255] : [34, 139, 34, 255]));
};
const lapse = (extra = {}) => ({ id: "tl", name: "Lapse", kind: "tile.timelapse", item: FRAMES, viz: { maxZoom: Z, canAreaChart: true, classLegendDict: LEGEND, areaChartParams: { line: true, sankey: true }, ...extra } });

test("a tile time lapse charts class areas over time", async () => {
  const layer = lapse();
  const entry = await tileChart(layer, TILE_AREA, { zoom: 8, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile: lapseTile });
  const [r] = entry.results(CTX);
  assert.equal(r.data.isCollection, true);
  assert.deepEqual(r.data.rows.map((x) => x.x), ["2001", "2002", "2003"]);
  const w = r.data.columns.indexOf("Water");
  const water = r.data.rows.map((x) => x.values[w]);
  assert.ok(near(water[0], halfHa() / 2) && near(water[1], halfHa()) && near(water[2], halfHa() * 1.5), water.join(","));
  assert.match(r.title, /over time/);
});

test("a continuous time lapse charts its mean over time, by date", async () => {
  const frames = [{ label: "2024-01", url: "https://t/2024-01/{z}/{y}/{x}.png" }, { label: "2024-02", url: "https://t/2024-02/{z}/{y}/{x}.png" }];
  const loadTile = async (url) => (url.includes("2024-01") ? tile(() => [0, 0, 0, 255]) : tile(() => [255, 255, 255, 255]));
  const layer = { id: "n", name: "NDVI", kind: "tile.timelapse", item: frames, viz: { maxZoom: Z, palette: "000000,ffffff", min: 0, max: 1 } };
  const [r] = (await tileChart(layer, TILE_AREA, { zoom: 8, kind: "line", params: chartParams(layer), retryDelay: 0 }, { loadTile })).results(CTX);
  assert.deepEqual(r.data.rows.map((x) => x.values[0]), [0, 1]);
  assert.equal(r.data.xLabel, "Date");
});

test("a tile time lapse's class transitions between periods, for a Sankey", async () => {
  const layer = lapse();
  const asked = [];
  const loadTile = (url) => (asked.push(url), lapseTile(url));
  const entry = await tileChart(layer, TILE_AREA, { zoom: 8, kind: "sankey", params: chartParams(layer), periods: [[2001, 2001], [2003, 2003]], retryDelay: 0 }, { loadTile });
  // only the frames the periods use
  assert.ok(!asked.some((u) => u.includes("/2002/")), asked.join(" "));
  const [s] = entry.results(CTX);
  assert.equal(s.kind, "sankey");
  const link = (from, to) => s.data.links.find((l) => l.source_name === `2001 ${from}` && l.target_name === `2003 ${to}`);
  // a quarter stayed water, a half turned from forest to water, a quarter stayed forest
  assert.ok(near(link("Water", "Water").value, halfHa() / 2), JSON.stringify(s.data.links));
  assert.ok(near(link("Forest", "Water").value, halfHa()));
  assert.ok(near(link("Forest", "Forest").value, halfHa() / 2));
  assert.equal(link("Water", "Forest"), undefined);
});

test("a period with no frame, or a layer with no legend, cannot make a Sankey", async () => {
  const layer = lapse();
  await assert.rejects(tileChart(layer, TILE_AREA, { zoom: 8, kind: "sankey", params: chartParams(layer), periods: [[1990, 1990], [2003, 2003]], retryDelay: 0 }, { loadTile: lapseTile }), /Lapse has no frame for 1990/);
  const plain = lapse({ classLegendDict: undefined });
  await assert.rejects(tileChart(plain, TILE_AREA, { zoom: 8, kind: "sankey", params: chartParams(plain), periods: [[2001, 2001], [2003, 2003]], retryDelay: 0 }, { loadTile: lapseTile }), /not a thematic time lapse/);
});

test("the tile frame loader: frames, their tiles, a legend, and Query on the frame shown", async () => {
  const store = createStore();
  const layer = lapse();
  store.update((s) => ({ ...s, layers: [{ ...layer, timelapse: { frame: 2 } }] }), "t");
  const ld = tileFrameLoader({ store }, layer);
  const p = await ld.prepare();
  assert.equal(p.legend.type, "classes");
  assert.deepEqual(await ld.years(), ["2001", "2002", "2003"]);
  const f0 = await ld.frame(0);
  assert.equal(f0({ x: 3, y: 5 }, 7), "https://t/2001/7/5/3.png");
  const t = await ld.queryTarget();
  assert.equal(t.type, "tileMapService");
  assert.equal(t.item({ x: 3, y: 5 }, 7), "https://t/2003/7/5/3.png");
  // a continuous layer's legend is its ramp
  const ramp = await tileFrameLoader({ store }, lapse({ classLegendDict: undefined, palette: ["000000", "ffffff"], min: 0, max: 1 })).prepare();
  assert.equal(ramp.legend.type, "ramp");
});

test("frames of a tile layer: a template, a function, or a time lapse's list", () => {
  assert.equal(framesOf({ item: "https://a/{z}/{x}/{y}" })[0].urlFor({ x: 1, y: 2 }, 3), "https://a/3/1/2");
  assert.equal(framesOf({ item: FRAMES }).length, 3);
  assert.equal(framesOf({ item: FRAMES })[1].label, "2002");
  assert.equal(framesOf({ item: null }).length, 0);
});

test("the tile plug-in charts tile layers and plays tile time lapses", async () => {
  assert.ok(isTimelapseKind("tile.timelapse"));
  const p = pluginFor("tile.timelapse");
  assert.equal(p, pluginFor("tile"));
  assert.equal(typeof p.chart, "function");
  const started = [];
  assert.equal(p.start(lapse(), { addTimelapse: (l, loader) => started.push([l.id, loader]) }), true);
  assert.equal(started[0][1], tileFrameLoader);
  assert.equal(p.start(tileLayer({}), { addTimelapse: () => started.push(1) }), false);
  assert.deepEqual(await p.years(lapse()), [2001, 2002, 2003]);
  assert.equal(await p.years({ ...lapse(), item: [{ label: "spring", url: "x" }] }), null);
});

test("a document's xyz.timelapse is a tile time lapse in the Map API", () => {
  const store = createStore();
  const api = createMapApi(store, {});
  const doc = {
    geoview: 1,
    layers: [{ id: "a", type: "xyz.timelapse", name: "Land cover", source: { frames: FRAMES, maxZoom: 8 }, viz: { classLegendDict: LEGEND, canAreaChart: true }, visible: true }],
    commands: [],
  };
  const out = applyDocument({ api }, doc);
  assert.deepEqual(out, { layers: 1, skipped: 0 });
  const l = store.get().layers[0];
  assert.equal(l.kind, "tile.timelapse");
  assert.equal(l.viz.maxZoom, 8);
  assert.equal(l.chartable, true);
  assert.equal(l.visible, false, "time lapses start off");
  assert.deepEqual(l.item, FRAMES);
});

test("past its maxZoom a tile layer shows the matching piece of its maxZoom tile, scaled up", async () => {
  const { overzoom } = await import("../../../src/georest/view/static/js/engine/tileframes.js");
  const f = overzoom(templateUrl("https://t/{z}/{x}/{y}.png"), 8);
  assert.equal(f({ x: 53, y: 97 }, 8), "https://t/8/53/97.png");
  assert.equal(f({ x: 1, y: 2 }, 3), "https://t/3/1/2.png");
  // zoom 10 is 4 x 4 tiles per zoom 8 tile: tile (213, 390) is piece (1, 2) of (53, 97)
  assert.deepEqual(f({ x: 213, y: 390 }, 10), { url: "https://t/8/53/97.png", block: { size: 1024, x: 256, y: 512 } });
  // no maxZoom: as given
  assert.equal(overzoom(templateUrl("https://t/{z}/{x}/{y}.png"), undefined)({ x: 1, y: 1 }, 12), "https://t/12/1/1.png");
});
