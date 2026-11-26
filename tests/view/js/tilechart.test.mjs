// Chart area on XYZ tile layers (js/engine/tilechart.js): the tiles over an
// area, the area's mask in tile pixels, the ground area of a pixel, a color
// read back to a value along the palette ramp, colors grouped lightly, and
// class transitions between periods. Synthetic pixels; no network.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MAX_TILES,
  ringsOf,
  bboxOf,
  tileCover,
  polygonMask,
  tileMask,
  pixelArea,
  parsePalette,
  rampValue,
  quantize,
  keyHex,
  modeOf,
  classifier,
  periodModes,
  countTransitions,
  topKeys,
  groupColors,
  yearOf,
} from "../../../src/georest/view/static/js/engine/tilechart.js";

const MPP0 = 156543.03392804097;

test("the tiles covering an area, at the map zoom capped by the layer's maxZoom", () => {
  // a small box near Fort Collins at zoom 10: one or a few tiles
  const c = tileCover([-105.1, 40.5, -105.0, 40.6], 10.4, 20);
  assert.equal(c.z, 10);
  assert.ok(c.tiles.length >= 1 && c.tiles.length <= 4, String(c.tiles.length));
  c.tiles.forEach((t) => assert.ok(Number.isInteger(t.x) && Number.isInteger(t.y)));
  // capped by maxZoom
  assert.equal(tileCover([-105.1, 40.5, -105.0, 40.6], 14, 8).z, 8);
});

test("the zoom steps down until at most 64 tiles cover the area", () => {
  // all of Colorado at zoom 12 is thousands of tiles
  const c = tileCover([-109.05, 37, -102.04, 41], 12, 20);
  assert.ok(c.tiles.length <= MAX_TILES, String(c.tiles.length));
  assert.equal(MAX_TILES, 64);
  // one zoom finer would be more than 64
  const finer = tileCover([-109.05, 37, -102.04, 41], c.z + 1, c.z + 1, Infinity);
  assert.ok(finer.tiles.length > MAX_TILES);
  // a box listing: every (x, y) once
  assert.equal(new Set(c.tiles.map((t) => `${t.x}/${t.y}`)).size, c.tiles.length);
});

test("an area's rings, from any GeoJSON shape, and their bounds", () => {
  const sq = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]];
  assert.equal(ringsOf({ type: "Polygon", coordinates: sq }).length, 1);
  assert.equal(ringsOf({ type: "Feature", geometry: { type: "MultiPolygon", coordinates: [sq, sq] } }).length, 2);
  assert.equal(ringsOf({ type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: sq } }] }).length, 1);
  assert.equal(ringsOf({ type: "Point", coordinates: [0, 0] }).length, 0);
  assert.deepEqual(bboxOf(ringsOf({ type: "Polygon", coordinates: [[[-2, 3], [4, 3], [4, 9], [-2, 3]]] })), [-2, 3, 4, 9]);
});

test("a polygon mask in pixel space: pixel centers inside, holes left out (evenodd)", () => {
  // a 4 x 4 box from (2, 2) to (6, 6) in an 8 x 8 image: 16 pixels
  const box = [[2, 2], [6, 2], [6, 6], [2, 6], [2, 2]];
  const m = polygonMask([box], 8, 8);
  assert.equal(m.length, 64);
  assert.equal(m.reduce((a, b) => a + b, 0), 16);
  assert.equal(m[2 * 8 + 2], 1);
  assert.equal(m[1 * 8 + 2], 0);
  assert.equal(m[6 * 8 + 6], 0);
  // a 2 x 2 hole in the middle
  const hole = [[3, 3], [5, 3], [5, 5], [3, 5], [3, 3]];
  const mh = polygonMask([box, hole], 8, 8);
  assert.equal(mh.reduce((a, b) => a + b, 0), 12);
  assert.equal(mh[3 * 8 + 3], 0);
  // a triangle: about half of its box
  const tri = polygonMask([[[0, 0], [100, 0], [0, 100], [0, 0]]], 100, 100);
  const n = tri.reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(n - 5000) < 100, String(n));
});

test("an area's mask on one tile, from longitude and latitude", () => {
  // the whole world's tile at zoom 0: the western hemisphere's northern half
  const ring = [[-180, 0], [0, 0], [0, 85], [-180, 85], [-180, 0]];
  const m = tileMask([ring], 0, 0, 0, 256, 256);
  const n = m.reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(n - 128 * 128) < 300, String(n));
  assert.equal(m[10 * 256 + 10], 1);
  assert.equal(m[200 * 256 + 10], 0);
  assert.equal(m[10 * 256 + 200], 0);
  // the same area on a 512 px tile is four times the pixels
  const m2 = tileMask([ring], 0, 0, 0, 512, 512);
  assert.ok(Math.abs(m2.reduce((a, b) => a + b, 0) - 4 * n) < 1200);
  // a tile it does not touch is empty
  assert.equal(tileMask([ring], 1, 1, 1, 256, 256).reduce((a, b) => a + b, 0), 0);
});

test("a pixel's ground area from Web Mercator at its row's latitude", () => {
  // zoom 0, the row at the equator: one pixel is MPP0 meters on a side
  const eq = pixelArea(0, 0, 128, 256);
  assert.ok(Math.abs(eq / (MPP0 * MPP0) - 1) < 1e-3, String(eq / (MPP0 * MPP0)));
  // halved for each zoom (a quarter of the area)
  assert.ok(Math.abs(pixelArea(1, 1, 0, 256) / (eq / 4) - 1) < 1e-2);
  // at 60 degrees a pixel is a quarter of the equator's: find the row there
  const z = 10;
  const n = 2 ** z;
  const yWorld = (0.5 - Math.log(Math.tan(Math.PI / 4 + (60 * Math.PI) / 360)) / (2 * Math.PI)) * n;
  const ty = Math.floor(yWorld);
  const row = Math.floor((yWorld - ty) * 256);
  const a60 = pixelArea(z, ty, row, 256);
  const aEq = (MPP0 / n) ** 2;
  assert.ok(Math.abs(a60 / aEq - 0.25) < 0.01, String(a60 / aEq));
  // a 512 px tile's pixel is a quarter of a 256 px tile's
  assert.ok(Math.abs(pixelArea(z, ty, row * 2, 512) / a60 - 0.25) < 0.01);
});

test("a color read back to a value along the palette ramp", () => {
  const ramp = parsePalette(["000000", "#ffffff"]);
  assert.deepEqual(ramp, [[0, 0, 0], [255, 255, 255]]);
  assert.ok(Math.abs(rampValue([128, 128, 128], ramp, 0, 100) - 50.2) < 0.1);
  assert.equal(rampValue([0, 0, 0], ramp, 0, 100), 0);
  assert.equal(rampValue([255, 255, 255], ramp, 0, 100), 100);
  // far from the ramp: not a value
  assert.equal(rampValue([255, 0, 0], ramp, 0, 100), null);
  // three stops: red, yellow, green over -1..1
  const r3 = parsePalette("ff0000,ffff00,00ff00");
  assert.equal(rampValue([255, 255, 0], r3, -1, 1), 0);
  assert.ok(Math.abs(rampValue([128, 255, 0], r3, -1, 1) - 0.498) < 0.01);
  assert.ok(Math.abs(rampValue([255, 64, 0], r3, -1, 1) - -0.749) < 0.01);
  // a little noise off the ramp still reads
  assert.ok(Math.abs(rampValue([250, 70, 6], r3, -1, 1) - -0.725) < 0.03);
  // a three-letter color
  assert.deepEqual(parsePalette(["f00"]), [[255, 0, 0]]);
});

test("colors grouped lightly: JPEG noise lands in the same group", () => {
  assert.equal(quantize([255, 0, 0]), quantize([252, 3, 2]));
  assert.notEqual(quantize([255, 0, 0]), quantize([200, 0, 0]));
  assert.equal(keyHex(quantize([255, 0, 0])), "#ff0000");
  assert.equal(keyHex(quantize([0, 0, 0])), "#000000");
  assert.match(keyHex(quantize([17, 99, 201])), /^#[0-9a-f]{6}$/);
});

test("the chart mode follows what the layer has", () => {
  assert.equal(modeOf({ classLegendDict: { A: "ff0000" } }), "thematic");
  assert.equal(modeOf({ palette: ["000000", "ffffff"], min: 0, max: 1 }), "continuous");
  assert.equal(modeOf({ palette: "000000,ffffff", min: 0 }), "colors");
  assert.equal(modeOf({}), "colors");
  assert.equal(modeOf(null), "colors");
});

test("a classifier per mode", () => {
  const th = classifier({ classLegendDict: { Water: "0000ff", Forest: "228b22" } });
  assert.equal(th.mode, "thematic");
  assert.deepEqual(th.names, ["Water", "Forest"]);
  assert.deepEqual(th.colors, ["#0000ff", "#228b22"]);
  assert.equal(th.classify(0, 0, 250), 0);
  assert.equal(th.classify(34, 139, 34), 1);
  assert.equal(th.classify(128, 128, 128), null);
  const co = classifier({ palette: ["000000", "ffffff"], min: 0, max: 10 });
  assert.equal(co.mode, "continuous");
  assert.equal(co.classify(255, 255, 255), 10);
  assert.equal(co.classify(255, 0, 0), null);
  // no legend and no ramp: each pixel's own color, grouped afterward
  const cl = classifier({});
  assert.equal(cl.mode, "colors");
  assert.equal(cl.classify(252, 3, 2), (252 << 16) | (3 << 8) | 2);
});

test("colors grouped by their light quantizing, each group named by its most common color", () => {
  const exact = new Map([
    [(255 << 16) | 0, 10],
    [(252 << 16) | (3 << 8) | 2, 4],
    [(0 << 16) | (0 << 8) | 255, 7],
  ]);
  const g = groupColors(exact);
  assert.equal(g.size, 2);
  const red = g.get(quantize([255, 0, 0]));
  assert.deepEqual(red, { area: 14, color: "#ff0000" });
  assert.deepEqual(g.get(quantize([0, 0, 255])), { area: 7, color: "#0000ff" });
});

test("the largest groups kept, the rest summed as Other", () => {
  const { keys, other } = topKeys({ a: 10, b: 50, c: 5, d: 30 }, 2);
  assert.deepEqual(keys, ["b", "d"]);
  assert.equal(other, 15);
  assert.deepEqual(topKeys({ a: 1 }, 5), { keys: ["a"], other: 0 });
});

test("a period's class per pixel is its most common class across the period's frames", () => {
  const f1 = Int16Array.from([0, 1, 2, -1]);
  const f2 = Int16Array.from([0, 2, 2, -1]);
  const f3 = Int16Array.from([1, 2, -1, -1]);
  assert.deepEqual([...periodModes([f1])], [0, 1, 2, -1]);
  assert.deepEqual([...periodModes([f1, f2, f3])], [0, 2, 2, -1]);
});

test("class transitions: area by from * 10000 + 9900 + to, pixels with no class left out", () => {
  const a = Int16Array.from([0, 0, 1, 1, -1]);
  const b = Int16Array.from([0, 1, 1, -1, 0]);
  const areas = Float64Array.from([1, 2, 3, 4, 5]);
  assert.deepEqual(countTransitions(a, b, areas), { 9900: 1, 9901: 2, 19901: 3 });
  // with class values from a base: (a + base) * 10000 + 9900 + (b + base)
  assert.deepEqual(countTransitions(a, b, areas, {}, 10), { 109910: 1, 109911: 2, 119911: 3 });
});

test("a frame label's year", () => {
  assert.equal(yearOf("2001"), 2001);
  assert.equal(yearOf("2010-01-01"), 2010);
  assert.equal(yearOf("Jan 2024"), 2024);
  assert.equal(yearOf("spring"), null);
});
