// Query on a tile layer: the color under the click, from the tile the map
// shows, and the legend class nearest that color.
import { test } from "node:test";
import assert from "node:assert/strict";
import { tileAt, nearestClass, queryTile, NEAREST_MAX } from "../../../src/georest/view/static/js/engine/tilequery.js";
import { pluginFor } from "../../../src/georest/view/static/js/plugins/index.js";

test("the tile and pixel under a point", () => {
  // (0, 0) at zoom 1 is the corner shared by four tiles: the one to its southeast
  assert.deepEqual(tileAt(0, 0, 1, 20), { z: 1, x: 1, y: 1, px: 0, py: 0 });
  // zoom is capped at the layer's maxZoom, and rounded
  assert.equal(tileAt(-105, 40, 14.6, 12).z, 12);
  assert.equal(tileAt(-105, 40, 9.4, 20).z, 9);
  const t = tileAt(-105.08, 40.58, 12, 20);
  assert.ok(t.px >= 0 && t.px < 256 && t.py >= 0 && t.py < 256);
});

test("the nearest legend class, within a small color distance", () => {
  const legend = { Water: "0000ff", Forest: "#228b22", Urban: "ff0000" };
  assert.equal(nearestClass([0, 0, 250], legend), "Water");
  assert.equal(nearestClass([40, 140, 40], legend), "Forest");
  // too far from every class: no name, rather than a wrong one
  assert.equal(nearestClass([128, 128, 128], legend), null);
  assert.equal(nearestClass([0, 0, 255 - NEAREST_MAX - 1], legend), null);
  assert.equal(nearestClass([0, 0, 255], null), null);
});

const target = (viz) => ({ type: "tileMapService", item: (c, z) => `https://t/${z}/${c.x}/${c.y}.png`, viz: { maxZoom: 16, ...viz } });
const layer = { id: "layer-1", name: "Hydro" };
const point = { lng: -105.08, lat: 40.58 };

test("a pixel with a legend reads as its class, with its swatch and hex", async () => {
  const asked = [];
  const rs = await queryTile(target({ classLegendDict: { Water: "0000ff" } }), layer, point, { zoom: 12 }, {
    loadPixel: async (url, px, py) => (asked.push([url, px, py]), [3, 2, 250, 255]),
  });
  assert.equal(asked.length, 1);
  assert.match(asked[0][0], /^https:\/\/t\/12\/\d+\/\d+\.png$/);
  assert.equal(rs.length, 1);
  const r = rs[0];
  assert.equal(r.kind, "value");
  assert.equal(r.title, "Hydro");
  const b = r.data.bands[0];
  assert.equal(b.label, "Water");
  assert.equal(b.color, "#0302fa");
  assert.equal(b.display, "#0302fa");
});

test("a pixel with no legend reads as its hex and swatch", async () => {
  const [r] = await queryTile(target({}), layer, point, { zoom: 12 }, { loadPixel: async () => [16, 32, 48, 255] });
  assert.deepEqual([r.data.bands[0].label, r.data.bands[0].color, r.data.bands[0].display], [null, "#102030", "#102030"]);
});

test("a transparent pixel is no data", async () => {
  const [r] = await queryTile(target({}), layer, point, { zoom: 12 }, { loadPixel: async () => [0, 0, 0, 0] });
  assert.deepEqual(r.data.bands, []);
  assert.equal(r.meta, "No data");
});

test("an unreadable tile says so", async () => {
  const [r] = await queryTile(target({}), layer, point, { zoom: 12 }, { loadPixel: async () => { throw new TypeError("Failed to fetch"); } });
  assert.match(r.data.error, /colors could not be read/);
});

test("tile layers can be queried", () => {
  assert.equal(typeof pluginFor("tile").query, "function");
});

test("the layer engine gives a tile layer a Query target", async () => {
  const { NOT_QUERYABLE } = await import("../../../src/georest/view/static/js/engine/layers.js");
  assert.equal(NOT_QUERYABLE.indexOf("tileMapService"), -1);
});
