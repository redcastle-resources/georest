// Chart area on XYZ tile layers (and tile time lapses): the layer's own
// tiles over the area are read in the browser (CORS), the area is drawn into
// each tile's pixels as a mask, and every pixel inside it with any opacity
// counts by its ground area (Web Mercator, at its row's latitude). What a
// pixel means depends on what the layer says about its colors:
//   thematic   (classLegendDict): the nearest legend class, as Query names it;
//              pixels near no class are left out (the subtitle says how many).
//   continuous (palette, min and max): the color read back to a value along
//              the palette ramp; the chart is the area's mean.
//   colors     (neither): area by color, colors grouped lightly to absorb
//              JPEG and resampling noise, the largest kept, the rest "Other".
// A time lapse charts each frame (a line over time) and, for a thematic
// layer, class transitions between periods for a Sankey, by pairing the
// pixels at the same place in each period's frames (same tiles, same zoom).
// Answers are shaped as Earth Engine's reductions, so geoView's own chart
// builders (engine/areachart.js) format them: units, percentages, class names
// and colors.
import { NEAREST_MAX } from "./tilequery.js";
import { lineResult, sankeyResult } from "./areachart.js";
import { SPLIT_STR } from "./charts.js";
import { formatPeriod, transitionBandNames } from "./sankey.js";
import { framesOf } from "./tileframes.js";

// Tiles read for one frame at most: the zoom steps down until the area fits.
export const MAX_TILES = 64;
// Euclidean RGB distance under which a color is on the palette ramp.
export const RAMP_MAX = NEAREST_MAX;
// The light grouping of colors with no legend: channels to multiples of 8.
export const QUANT_STEP = 8;
// Colors with no legend: the largest kept, the rest summed as "Other".
export const TOP_COLORS = 12;
export const OTHER_COLOR = "#9e9e9e";
// Web Mercator meters per pixel of a 256 px tile at zoom 0, at the equator
const MPP0 = 156543.03392804097;
const BAND = "value";
const MEAN = "Mean";
// Class values: 10, 11, ... 99. Histogram keys sort as strings, so values of
// one digit count keep the legend's order; under 100, they fit the Sankey's
// from * 10000 + 9900 + to.
export const CLASS_BASE = 10;
export const MAX_CLASSES = 90;
const classValues = (n) => Array.from({ length: n }, (_, i) => CLASS_BASE + i);

const clampLat = (lat) => Math.max(-85.05112878, Math.min(85.05112878, lat));
// longitude and latitude to tile coordinates (fractional) at a zoom
function worldXY(lng, lat, z) {
  const n = 2 ** z;
  const s = Math.sin((clampLat(lat) * Math.PI) / 180);
  return [((lng + 180) / 360) * n, (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n];
}

// Every ring of an area (Polygon, MultiPolygon, Feature, FeatureCollection,
// GeometryCollection) as [[lng, lat], ...].
export function ringsOf(gj) {
  const rings = [];
  const walk = (o) => {
    if (!o) return;
    if (o.type === "FeatureCollection") (o.features || []).forEach(walk);
    else if (o.type === "Feature") walk(o.geometry);
    else if (o.type === "GeometryCollection") (o.geometries || []).forEach(walk);
    else if (o.type === "Polygon") (o.coordinates || []).forEach((r) => rings.push(r));
    else if (o.type === "MultiPolygon") (o.coordinates || []).forEach((p) => p.forEach((r) => rings.push(r)));
  };
  walk(gj);
  return rings.filter((r) => Array.isArray(r) && r.length >= 3);
}

export function bboxOf(rings) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  rings.forEach((r) =>
    r.forEach(([x, y]) => {
      w = Math.min(w, x);
      e = Math.max(e, x);
      s = Math.min(s, y);
      n = Math.max(n, y);
    })
  );
  return [w, s, e, n];
}

// The tiles covering a box at the map zoom capped by maxZoom, stepping the
// zoom down until at most maxTiles cover it. {z, tiles: [{x, y}]}.
export function tileCover(bbox, zoom, maxZoom, maxTiles = MAX_TILES) {
  const cap = Number.isFinite(Number(maxZoom)) ? Number(maxZoom) : 20;
  let z = Math.max(0, Math.min(cap, Math.round(Number(zoom) || 0)));
  const [w, s, e, nth] = bbox;
  for (;;) {
    const n = 2 ** z;
    const clamp = (v) => Math.max(0, Math.min(n - 1, Math.floor(v)));
    const [x0, y0] = worldXY(w, nth, z);
    const [x1, y1] = worldXY(e, s, z);
    const xa = clamp(x0), xb = clamp(x1), ya = clamp(y0), yb = clamp(y1);
    const count = (xb - xa + 1) * (yb - ya + 1);
    if (count <= maxTiles || z === 0) {
      const tiles = [];
      for (let y = ya; y <= yb; y++) for (let x = xa; x <= xb; x++) tiles.push({ x, y });
      return { z, tiles };
    }
    z--;
  }
}

// A polygon's mask in pixel space (rings in pixel coordinates): 1 where a
// pixel's center is inside under the even-odd rule, so holes are left out.
export function polygonMask(rings, w, h) {
  const m = new Uint8Array(w * h);
  const edges = [];
  rings.forEach((r) => {
    for (let i = 0; i + 1 < r.length; i++) edges.push([r[i][0], r[i][1], r[i + 1][0], r[i + 1][1]]);
    const a = r[r.length - 1];
    const b = r[0];
    if (a[0] !== b[0] || a[1] !== b[1]) edges.push([a[0], a[1], b[0], b[1]]);
  });
  const xs = [];
  for (let j = 0; j < h; j++) {
    const yc = j + 0.5;
    xs.length = 0;
    for (const [x1, y1, x2, y2] of edges) {
      if ((y1 <= yc && yc < y2) || (y2 <= yc && yc < y1)) xs.push(x1 + ((yc - y1) * (x2 - x1)) / (y2 - y1));
    }
    if (xs.length < 2) continue;
    xs.sort((p, q) => p - q);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const from = Math.max(0, Math.ceil(xs[k] - 0.5));
      const to = Math.min(w - 1, Math.ceil(xs[k + 1] - 0.5) - 1);
      for (let i = from; i <= to; i++) m[j * w + i] = 1;
    }
  }
  return m;
}

// An area's mask on tile (x, y) at zoom z, w x h pixels; rings in degrees.
export function tileMask(rings, z, x, y, w, h) {
  const px = rings.map((r) =>
    r.map(([lng, lat]) => {
      const [X, Y] = worldXY(lng, lat, z);
      return [(X - x) * w, (Y - y) * h];
    })
  );
  return polygonMask(px, w, h);
}

// The ground area in square meters of one pixel in row `row` of tile row
// `ty` at zoom z, for a w x h tile (256 px unless told).
export function pixelArea(z, ty, row, h, w = h) {
  const n = 2 ** z;
  const yWorld = ty + (row + 0.5) / h;
  const lat = Math.atan(Math.sinh(Math.PI * (1 - (2 * yWorld) / n)));
  const side = (MPP0 / n) * (256 / w) * Math.cos(lat);
  return side * side;
}

function parseHex(h) {
  const s = String(h).trim().replace(/^#/, "");
  const full = s.length === 3 ? s.split("").map((c) => c + c).join("") : s.slice(0, 6);
  if (!/^[0-9a-f]{6}$/i.test(full)) return null;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
}
const hex2 = (v) => v.toString(16).padStart(2, "0");
const rgbHex = (r, g, b) => "#" + hex2(r) + hex2(g) + hex2(b);

// A palette (a list, or a comma-separated string) as [[r, g, b], ...].
export function parsePalette(palette) {
  const list = typeof palette === "string" ? palette.split(",") : Array.isArray(palette) ? palette : [];
  return list.map(parseHex).filter(Boolean);
}

// A color read back to a value: the nearest point on the piecewise-linear
// ramp through the palette (stops evenly spaced from min to max), or null
// when the color is farther than maxDist from the ramp.
export function rampValue(rgb, ramp, min, max, maxDist = RAMP_MAX) {
  if (!ramp.length) return null;
  if (ramp.length === 1) {
    const c = ramp[0];
    return Math.hypot(rgb[0] - c[0], rgb[1] - c[1], rgb[2] - c[2]) <= maxDist ? min : null;
  }
  let best = Infinity;
  let at = 0;
  for (let i = 0; i + 1 < ramp.length; i++) {
    const a = ramp[i];
    const b = ramp[i + 1];
    const d0 = b[0] - a[0], d1 = b[1] - a[1], d2 = b[2] - a[2];
    const len2 = d0 * d0 + d1 * d1 + d2 * d2;
    const p0 = rgb[0] - a[0], p1 = rgb[1] - a[1], p2 = rgb[2] - a[2];
    const t = len2 ? Math.max(0, Math.min(1, (p0 * d0 + p1 * d1 + p2 * d2) / len2)) : 0;
    const dist = Math.hypot(p0 - t * d0, p1 - t * d1, p2 - t * d2);
    if (dist < best) {
      best = dist;
      at = i + t;
    }
  }
  if (best > maxDist) return null;
  return min + (at * (max - min)) / (ramp.length - 1);
}

// A color's light group: each channel to the nearest multiple of QUANT_STEP.
export function quantize(rgb) {
  const q = (c) => Math.round(c / QUANT_STEP);
  return (q(rgb[0]) << 12) | (q(rgb[1]) << 6) | q(rgb[2]);
}
export function keyHex(key) {
  const c = (v) => Math.min(255, v * QUANT_STEP);
  return rgbHex(c((key >> 12) & 63), c((key >> 6) & 63), c(key & 63));
}

// Exact colors (r << 16 | g << 8 | b -> area) grouped by quantize: each group
// {area, color} named by its most common exact color.
export function groupColors(exact) {
  const out = new Map();
  const top = new Map();
  exact.forEach((area, c) => {
    const rgb = [(c >> 16) & 255, (c >> 8) & 255, c & 255];
    const k = quantize(rgb);
    const g = out.get(k) || { area: 0, color: null };
    g.area += area;
    if (!top.has(k) || area > top.get(k)) {
      top.set(k, area);
      g.color = rgbHex(rgb[0], rgb[1], rgb[2]);
    }
    out.set(k, g);
  });
  return out;
}

// The n largest keys of {key: area}, largest first, and the rest's sum.
export function topKeys(areas, n) {
  const keys = Object.keys(areas).sort((a, b) => areas[b] - areas[a]);
  const kept = keys.slice(0, n);
  const other = keys.slice(n).reduce((s, k) => s + areas[k], 0);
  return { keys: kept, other };
}

const nonEmpty = (d) => d && typeof d === "object" && Object.keys(d).length > 0;
const given = (v) => v !== undefined && v !== null && v !== "";

// How a tile layer's colors chart: "thematic", "continuous" or "colors".
export function modeOf(viz) {
  if (!viz) return "colors";
  if (nonEmpty(viz.classLegendDict)) return "thematic";
  if (parsePalette(viz.palette).length && given(viz.min) && given(viz.max) && Number.isFinite(Number(viz.min)) && Number.isFinite(Number(viz.max))) return "continuous";
  return "colors";
}

// What one pixel's color means: classify(r, g, b) -> a class index
// (thematic), a value (continuous), the exact color (colors), or null.
// Answers are kept by color: a tile has few distinct colors.
export function classifier(viz) {
  const mode = modeOf(viz);
  const memo = new Map();
  const cached = (fn) => (r, g, b) => {
    const c = (r << 16) | (g << 8) | b;
    if (memo.has(c)) return memo.get(c);
    const v = fn(r, g, b);
    memo.set(c, v);
    return v;
  };
  if (mode === "thematic") {
    const names = [];
    const colors = [];
    const rgbs = [];
    Object.keys(viz.classLegendDict).forEach((name) => {
      const c = parseHex(viz.classLegendDict[name]);
      if (!c) return;
      names.push(name);
      colors.push(rgbHex(c[0], c[1], c[2]));
      rgbs.push(c);
    });
    const classify = cached((r, g, b) => {
      let best = null;
      let bestD = Infinity;
      rgbs.forEach((c, i) => {
        const d = Math.hypot(r - c[0], g - c[1], b - c[2]);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      return bestD <= NEAREST_MAX ? best : null;
    });
    return { mode, names, colors, classify };
  }
  if (mode === "continuous") {
    const ramp = parsePalette(viz.palette);
    const min = Number(viz.min);
    const max = Number(viz.max);
    return { mode, classify: cached((r, g, b) => rampValue([r, g, b], ramp, min, max)) };
  }
  return { mode, classify: (r, g, b) => (r << 16) | (g << 8) | b };
}

// The class of each pixel over a period: its most common class across the
// period's frames (ties: the lowest class, as Earth Engine's mode), -1 none.
export function periodModes(frames) {
  if (frames.length === 1) return frames[0];
  const n = frames[0].length;
  const out = new Int16Array(n);
  for (let p = 0; p < n; p++) {
    let best = -1;
    let bestN = 0;
    for (let i = 0; i < frames.length; i++) {
      const c = frames[i][p];
      if (c < 0) continue;
      let k = 0;
      for (let j = 0; j < frames.length; j++) if (frames[j][p] === c) k++;
      if (k > bestN || (k === bestN && c < best)) {
        best = c;
        bestN = k;
      }
    }
    out[p] = best;
  }
  return out;
}

// Area by class transition, keyed as engine/sankey.js decodes them:
// from * 10000 + 9900 + to. A pixel with no class in either is left out.
// base: added to each class index to make its class value.
export function countTransitions(a, b, areas, into = {}, base = 0) {
  for (let p = 0; p < a.length; p++) {
    if (a[p] < 0 || b[p] < 0) continue;
    const k = (a[p] + base) * 10000 + 9900 + (b[p] + base);
    into[k] = (into[k] || 0) + areas[p];
  }
  return into;
}

// A frame label's year (its first four digits in a row), or null.
export function yearOf(label) {
  const m = /(\d{4})/.exec(String(label));
  return m ? Number(m[1]) : null;
}

// ---- reading tiles ----

// fn over items, at most n at a time.
async function mapLimit(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i], i);
    }
  };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  return out;
}

// A tile's pixels, fetched with CORS: {data (RGBA), width, height}, or null
// for a tile the server does not have (404, 204). A server that refuses
// (CORS) or cannot be reached throws with err.cors set.
export async function loadTile(url) {
  let res;
  try {
    res = await fetch(url, { mode: "cors" });
  } catch (e) {
    const err = new Error(String((e && e.message) || e));
    err.cors = true;
    throw err;
  }
  if (res.status === 404 || res.status === 204) return null;
  if (!res.ok) throw new Error("tile " + res.status);
  const bmp = await createImageBitmap(await res.blob());
  const w = bmp.width;
  const h = bmp.height;
  const c = typeof OffscreenCanvas === "function" ? new OffscreenCanvas(w, h) : Object.assign(document.createElement("canvas"), { width: w, height: h });
  const ctx = c.getContext("2d");
  ctx.drawImage(bmp, 0, 0);
  return { data: ctx.getImageData(0, 0, w, h).data, width: w, height: h };
}

// The masked pixels of one tile at one size: their indices and areas.
function maskedPixels(rings, z, t, w, h) {
  const m = tileMask(rings, z, t.x, t.y, w, h);
  let n = 0;
  for (let i = 0; i < m.length; i++) n += m[i];
  const idx = new Int32Array(n);
  const area = new Float64Array(n);
  let k = 0;
  for (let row = 0; row < h; row++) {
    const a = pixelArea(z, t.y, row, h, w);
    for (let col = 0; col < w; col++) {
      if (!m[row * w + col]) continue;
      idx[k] = row * w + col;
      area[k++] = a;
    }
  }
  return { idx, area };
}

const plural = (n, one, many) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// tileChart(layer, area, {zoom, kind, params, periods, retryDelay}, deps) ->
// {results(ctx)}. deps.loadTile(url) replaces loadTile (tests).
export async function tileChart(layer, area, cctx, deps = {}) {
  const viz = layer.viz || {};
  const name = layer.name;
  const load = deps.loadTile || loadTile;
  const delay = cctx.retryDelay === undefined ? 500 : cctx.retryDelay;
  const cls = classifier(viz);
  const timed = layer.kind === "tile.timelapse";
  const frames = framesOf(layer);
  if (!frames.length) throw new Error(`${name} has no tiles to chart.`);
  const rings = ringsOf(area && area.geojson);
  if (!rings.length) throw new Error("This area has no outline to chart tile layers over.");
  const bbox = bboxOf(rings);
  const maxZoom = Number.isFinite(Number(viz.maxZoom)) ? Number(viz.maxZoom) : 15;
  const { z, tiles: all } = tileCover(bbox, cctx.zoom === undefined || cctx.zoom === null ? 10 : cctx.zoom, maxZoom);
  // tiles the area touches (checked at 256 px), each with its masks by size
  const tiles = all
    .map((t) => ({ ...t, masks: new Map([["256x256", maskedPixels(rings, z, t, 256, 256)]]) }))
    .filter((t) => t.masks.get("256x256").idx.length > 0);
  const maskOf = (t, w, h) => {
    const k = `${w}x${h}`;
    if (!t.masks.has(k)) t.masks.set(k, maskedPixels(rings, z, t, w, h));
    return t.masks.get(k);
  };
  let tileWidth = 256;
  const read = async (url) => {
    const once = () => load(url);
    try {
      try {
        return await once();
      } catch (e) {
        if (e && e.cors) throw e;
        await new Promise((r) => setTimeout(r, delay));
        return await once();
      }
    } catch (e) {
      if (e && e.cors) throw new Error(`${name} cannot be charted: its tile server does not let the browser read its pixels (CORS), or could not be reached.`);
      throw new Error(`${name} cannot be charted: a tile could not be read (${(e && e.message) || e}).`);
    }
  };
  const lat = (bbox[1] + bbox[3]) / 2;
  const ground = () => (MPP0 / 2 ** z) * (256 / tileWidth) * Math.cos((lat * Math.PI) / 180);
  const urlOf = (f, t) => f.urlFor({ x: t.x, y: t.y }, z);

  if (cctx.kind === "sankey") return sankeyChart();

  // ---- class areas, means or colors, per frame ----
  const tallies = frames.map(() => ({ classes: {}, sum: 0, weight: 0, colors: new Map(), unmatched: 0, pixels: 0 }));
  const jobs = [];
  frames.forEach((f, fi) => tiles.forEach((t) => jobs.push([fi, t])));
  await mapLimit(jobs, 8, async ([fi, t]) => {
    const img = await read(urlOf(frames[fi], t));
    if (!img) return;
    tileWidth = img.width;
    const { idx, area: areas } = maskOf(t, img.width, img.height);
    const d = img.data;
    const tl = tallies[fi];
    for (let k = 0; k < idx.length; k++) {
      const o = idx[k] * 4;
      if (d[o + 3] === 0) continue;
      tl.pixels++;
      const v = cls.classify(d[o], d[o + 1], d[o + 2]);
      if (v === null) {
        tl.unmatched++;
        continue;
      }
      const a = areas[k];
      if (cls.mode === "thematic") tl.classes[v] = (tl.classes[v] || 0) + a;
      else if (cls.mode === "continuous") {
        tl.sum += v * a;
        tl.weight += a;
      } else tl.colors.set(v, (tl.colors.get(v) || 0) + a);
    }
  });
  if (!tallies.some((t) => t.pixels)) throw new Error(`${name} has no pixels in this area.`);
  const labels = timed ? frames.map((f) => f.label) : null;
  const keyOf = (fi, band) => (timed ? `${labels[fi]}${SPLIT_STR}${band}` : band);
  const unmatched = tallies.reduce((s, t) => s + t.unmatched, 0);
  const nominal = Math.round(ground());
  const params = { ...cctx.params };
  if (timed && !params.xAxisLabel && !labels.every((l) => /^\d{4}$/.test(String(l)))) params.xAxisLabel = "Date";
  const note = (r, what) => {
    if (unmatched) r.meta = `${r.meta}, ${plural(unmatched, "pixel", "pixels")} ${what} left out`;
    return r;
  };

  if (cls.mode === "continuous") {
    const raw = {};
    tallies.forEach((t, fi) => (raw[keyOf(fi, MEAN)] = t.weight ? t.sum / t.weight : null));
    const entry = { raw, objInfo: { objType: timed ? "ImageCollection" : "Image", bandNames: [MEAN] }, labels, isHistogram: false, reducerLabel: MEAN };
    return { results: (ctx) => [note(lineResult(layer, params, entry, { ...ctx, nominalScale: nominal }), "off the color ramp")] };
  }

  let classInfo;
  const raw = {};
  if (cls.mode === "thematic") {
    if (cls.names.length > MAX_CLASSES) throw new Error(`${name} has more than ${MAX_CLASSES} classes, too many to chart.`);
    classInfo = { [BAND]: { classValues: classValues(cls.names.length), classNames: cls.names, classPalette: cls.colors } };
    tallies.forEach((t, fi) => {
      const h = {};
      Object.keys(t.classes).forEach((i) => (h[CLASS_BASE + Number(i)] = t.classes[i]));
      raw[keyOf(fi, BAND)] = h;
    });
  } else {
    // the largest colors over the whole chart, the same in every frame
    const groups = tallies.map((t) => groupColors(t.colors));
    const totals = {};
    const names = new Map();
    groups.forEach((g) =>
      g.forEach((v, k) => {
        totals[k] = (totals[k] || 0) + v.area;
        if (!names.has(k) || v.area > names.get(k).area) names.set(k, v);
      })
    );
    const { keys, other } = topKeys(totals, TOP_COLORS);
    const classNames = keys.map((k) => names.get(Number(k)).color);
    const classPalette = classNames.slice();
    if (other > 0) {
      classNames.push("Other");
      classPalette.push(OTHER_COLOR);
    }
    classInfo = { [BAND]: { classValues: classValues(classNames.length), classNames, classPalette } };
    const at = new Map(keys.map((k, i) => [Number(k), i]));
    groups.forEach((g, fi) => {
      const h = {};
      g.forEach((v, k) => {
        const i = CLASS_BASE + (at.has(k) ? at.get(k) : keys.length);
        h[i] = (h[i] || 0) + v.area;
      });
      raw[keyOf(fi, BAND)] = h;
    });
  }
  // areas in square meters: a scale of 1 m makes the unit conversions exact
  const entry = { raw, objInfo: { objType: timed ? "ImageCollection" : "Image", bandNames: [BAND], classInfo }, labels, isHistogram: true, scale: 1 };
  return { results: (ctx) => [note(lineResult(layer, params, entry, { ...ctx, nominalScale: nominal }), "matching no class")] };

  // ---- class transitions between periods ----
  async function sankeyChart() {
    if (!timed || cls.mode !== "thematic") throw new Error(`${name} is not a thematic time lapse (a classLegendDict on tile frames), so it has no class transitions to chart.`);
    if (cls.names.length > MAX_CLASSES) throw new Error(`${name} has more than ${MAX_CLASSES} classes, too many for a Sankey.`);
    const periods = cctx.periods || [];
    if (periods.length < 2) throw new Error("A Sankey needs at least two periods.");
    const byPeriod = periods.map((p) => {
      const [a, b] = Array.isArray(p) ? [p[0], p[p.length - 1]] : [p, p];
      const fs = frames.map((f, i) => [yearOf(f.label), i]).filter(([y]) => y !== null && y >= a && y <= b).map(([, i]) => i);
      if (!fs.length) throw new Error(`${name} has no frame for ${formatPeriod(p)}.`);
      return fs;
    });
    const needed = [...new Set(byPeriod.flat())];
    const names = transitionBandNames(periods);
    const raw = {};
    names.forEach((n) => (raw[n] = {}));
    await mapLimit(tiles, 4, async (t) => {
      const imgs = new Map();
      await Promise.all(needed.map(async (fi) => imgs.set(fi, await read(urlOf(frames[fi], t)))));
      const first = [...imgs.values()].find(Boolean);
      if (!first) return;
      tileWidth = first.width;
      const { idx, area: areas } = maskOf(t, first.width, first.height);
      const classes = new Map();
      needed.forEach((fi) => {
        const img = imgs.get(fi);
        const out = new Int16Array(idx.length).fill(-1);
        if (img && img.width === first.width && img.height === first.height) {
          const d = img.data;
          for (let k = 0; k < idx.length; k++) {
            const o = idx[k] * 4;
            if (d[o + 3] === 0) continue;
            const v = cls.classify(d[o], d[o + 1], d[o + 2]);
            if (v !== null) out[k] = v;
          }
        }
        classes.set(fi, out);
      });
      const modes = byPeriod.map((fs) => periodModes(fs.map((fi) => classes.get(fi))));
      for (let i = 0; i + 1 < modes.length; i++) countTransitions(modes[i], modes[i + 1], areas, raw[names[i]], CLASS_BASE);
    });
    if (!names.some((n) => Object.keys(raw[n]).length)) throw new Error(`${name} has no classed pixels in this area.`);
    const classInfo = { [BAND]: { classValues: classValues(cls.names.length), classNames: cls.names, classPalette: cls.colors } };
    const entry = { raw, objInfo: { objType: "ImageCollection", bandNames: [BAND], classInfo }, periods, band: BAND, scale: 1 };
    const nominal = Math.round(ground());
    return { results: (ctx) => [sankeyResult(layer, cctx.params, entry, { ...ctx, nominalScale: nominal })] };
  }
}
