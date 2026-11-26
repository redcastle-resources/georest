// The Query tool's gestures: a single click queries (the first click of a
// double-click does not), and, while Query is on, hovering a vector drawn in
// the browser names the feature under the pointer.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createClickGate, hoverName, topHit, isHoverable, CLICK_DELAY_MS } from "../../../src/georest/view/static/js/engine/hover.js";
import { flatten } from "../../../src/georest/view/static/js/engine/vectortiles.js";
import { hintFor } from "../../../src/georest/view/static/js/ui/tools.js";

// A clock the gate's timers run on.
function fakeClock() {
  let now = 0;
  let next = 1;
  const timers = new Map();
  return {
    setTimer: (fn, ms) => {
      const id = next++;
      timers.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    advance(ms) {
      now += ms;
      for (const [id, t] of [...timers]) {
        if (t.at <= now) {
          timers.delete(id);
          t.fn();
        }
      }
    },
    pending: () => timers.size,
  };
}

test("a single click queries once the double-click window has passed", () => {
  const clock = fakeClock();
  const got = [];
  const gate = createClickGate((ev) => got.push(ev), clock);
  gate.click("a");
  assert.deepEqual(got, []);
  clock.advance(CLICK_DELAY_MS - 1);
  assert.deepEqual(got, []);
  clock.advance(1);
  assert.deepEqual(got, ["a"]);
});

test("a double-click zooms and queries nothing: both clicks and the dblclick are dropped", () => {
  const clock = fakeClock();
  const got = [];
  const gate = createClickGate((ev) => got.push(ev), clock);
  gate.click("first");
  clock.advance(120);
  gate.click("second");
  gate.dblclick();
  clock.advance(CLICK_DELAY_MS * 4);
  assert.deepEqual(got, []);
  assert.equal(clock.pending(), 0);
  // the next single click queries again
  gate.click("later");
  clock.advance(CLICK_DELAY_MS);
  assert.deepEqual(got, ["later"]);
});

test("a dblclick alone cancels the pending click; cancel() drops it too", () => {
  const clock = fakeClock();
  const got = [];
  const gate = createClickGate((ev) => got.push(ev), clock);
  gate.click("x");
  gate.dblclick();
  clock.advance(CLICK_DELAY_MS);
  gate.click("y");
  gate.cancel();
  clock.advance(CLICK_DELAY_MS);
  assert.deepEqual(got, []);
});

test("two quick clicks far apart are not a double-click: the later one queries", () => {
  const clock = fakeClock();
  const got = [];
  const near = (a, b) => Math.abs(a.x - b.x) <= 10;
  const gate = createClickGate((ev) => got.push(ev.x), { ...clock, near });
  gate.click({ x: 0 });
  clock.advance(50);
  gate.click({ x: 120 });
  clock.advance(CLICK_DELAY_MS * 2);
  assert.deepEqual(got, [120]);
  // close together, they are a double-click
  gate.click({ x: 0 });
  clock.advance(50);
  gate.click({ x: 4 });
  clock.advance(CLICK_DELAY_MS * 2);
  assert.deepEqual(got, [120]);
});

test("two slow clicks are two queries", () => {
  const clock = fakeClock();
  const got = [];
  const gate = createClickGate((ev) => got.push(ev), clock);
  gate.click(1);
  clock.advance(CLICK_DELAY_MS + 50);
  gate.click(2);
  clock.advance(CLICK_DELAY_MS);
  assert.deepEqual(got, [1, 2]);
});

test("hoverName: name, NAME, title or label, then the display field, then the layer name", () => {
  assert.equal(hoverName({ NAME: "Uinta", other: 1 }, { layerName: "Forests" }), "Uinta");
  assert.equal(hoverName({ name: "a", title: "b" }, {}), "a");
  assert.equal(hoverName({ Title: "Big Fire", label: "x" }, {}), "Big Fire");
  assert.equal(hoverName({ LABEL: "L1" }, {}), "L1");
  assert.equal(hoverName({ FORESTNAME: "Ashley", OBJECTID: 3 }, { displayField: "FORESTNAME", layerName: "Forests" }), "Ashley");
  // empty values do not count
  assert.equal(hoverName({ name: "", NAME: null, ADMIN: "Dixie" }, { displayField: "ADMIN" }), "Dixie");
  assert.equal(hoverName({ OBJECTID: 3 }, { layerName: "Forests" }), "Forests");
  assert.equal(hoverName(null, { layerName: "Forests" }), "Forests");
  assert.equal(hoverName({ name: 42 }, {}), "42");
});

test("isHoverable: visible GeoJSON and ArcGIS feature layers only", () => {
  assert.equal(isHoverable({ kind: "geojson", visible: true }), true);
  assert.equal(isHoverable({ kind: "esri.feature", visible: true }), true);
  assert.equal(isHoverable({ kind: "geojson", visible: false }), false);
  for (const kind of ["ee", "tiles", "esri.map", "esri.tile", "esri.image", "esri.timelapse", "select"]) assert.equal(isHoverable({ kind, visible: true }), false, kind);
  assert.equal(isHoverable({ kind: "geojson", visible: true, chartOnly: true }), false);
});

test("topHit: the top layer's feature under the pointer, with its name", () => {
  const Z = 12;
  const sq = (x0, y0, d) => ({ type: "Polygon", coordinates: [[[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]] });
  const fc = (...features) => ({ type: "FeatureCollection", features });
  const lower = flatten(fc({ type: "Feature", id: 1, geometry: sq(0, 0, 1), properties: { name: "Lower" } }));
  const upper = flatten(fc({ type: "Feature", id: 2, geometry: sq(0.5, 0.5, 1), properties: { OBJECTID: 9 } }));
  const sources = [
    { layer: { id: "u", name: "Upper layer" }, parts: upper },
    { layer: { id: "l", name: "Lower layer" }, parts: lower },
  ];
  const both = topHit(sources, { lng: 0.75, lat: 0.75 }, Z);
  assert.equal(both.layer.id, "u");
  assert.equal(both.name, "Upper layer");
  assert.equal(both.key, "u:2");
  assert.equal(both.feature.type, "Feature");
  const one = topHit(sources, { lng: 0.25, lat: 0.25 }, Z);
  assert.equal(one.name, "Lower");
  assert.equal(topHit(sources, { lng: 5, lat: 5 }, Z), null);
  assert.equal(topHit([], { lng: 0, lat: 0 }, Z), null);
});

test("the Query hint says click, not double-click", () => {
  const hint = hintFor("query", false);
  assert.match(hint, /^Click the map/);
  assert.doesNotMatch(hint, /double/i);
  assert.match(hintFor("query", true), /^Tap the map/);
});

test("nearClicks: map events within 10 screen pixels are one double-click", async () => {
  const { nearClicks } = await import("../../../src/georest/view/static/js/engine/hover.js");
  const ev = (x, y) => ({ domEvent: { clientX: x, clientY: y } });
  assert.equal(nearClicks(ev(100, 100), ev(106, 108)), true);
  assert.equal(nearClicks(ev(100, 100), ev(130, 100)), false);
  // without screen positions, a quick second click counts as a double-click
  assert.equal(nearClicks({}, {}), true);
});
