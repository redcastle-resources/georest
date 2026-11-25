// Query on a tile layer (an XYZ template): the color of the pixel under the
// click, read from the tile the map shows at its zoom (capped at the layer's
// maxZoom), and the classLegendDict class whose color is nearest, when one is
// close enough. Reading a tile's pixels needs the tile server to allow it
// (CORS); most public tile servers do.

// Euclidean RGB distance under which a pixel is a legend class: absorbs
// resampling and JPEG noise, well under the gap between distinct classes.
export const NEAREST_MAX = 24;

const hex2 = (v) => v.toString(16).padStart(2, "0");
const toHex = (rgb) => "#" + hex2(rgb[0]) + hex2(rgb[1]) + hex2(rgb[2]);

function parseHex(h) {
  const s = String(h).trim().replace(/^#/, "");
  const full = s.length === 3 ? s.split("").map((c) => c + c).join("") : s.slice(0, 6);
  if (!/^[0-9a-f]{6}$/i.test(full)) return null;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
}

// The tile at a zoom holding a point, and the point's pixel in it.
export function tileAt(lng, lat, zoom, maxZoom) {
  const cap = Number.isFinite(Number(maxZoom)) ? Number(maxZoom) : 20;
  const z = Math.max(0, Math.min(cap, Math.round(zoom)));
  const n = Math.pow(2, z);
  const s = Math.min(Math.max(Math.sin((lat * Math.PI) / 180), -0.9999), 0.9999);
  const X = ((lng + 180) / 360) * n;
  const Y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n;
  const x = Math.floor(X);
  const y = Math.min(n - 1, Math.max(0, Math.floor(Y)));
  return { z, x: ((x % n) + n) % n, y, px: Math.min(255, Math.floor((X - x) * 256)), py: Math.min(255, Math.floor((Y - Math.floor(Y)) * 256)) };
}

// The legend class nearest a color, or null when none is within NEAREST_MAX.
export function nearestClass(rgb, legend) {
  if (!legend || typeof legend !== "object") return null;
  let best = null;
  let bestD = Infinity;
  Object.keys(legend).forEach((name) => {
    const c = parseHex(legend[name]);
    if (!c) return;
    const d = Math.hypot(rgb[0] - c[0], rgb[1] - c[1], rgb[2] - c[2]);
    if (d < bestD) {
      bestD = d;
      best = name;
    }
  });
  return bestD <= NEAREST_MAX ? best : null;
}

// [r, g, b, a] of one pixel of a tile image, fetched with CORS.
export async function loadPixel(url, px, py) {
  const res = await fetch(url, { mode: "cors" });
  if (!res.ok) throw new Error("tile " + res.status);
  const bmp = await createImageBitmap(await res.blob());
  const w = bmp.width;
  const h = bmp.height;
  const c = typeof OffscreenCanvas === "function" ? new OffscreenCanvas(w, h) : Object.assign(document.createElement("canvas"), { width: w, height: h });
  const ctx = c.getContext("2d");
  ctx.drawImage(bmp, 0, 0);
  // a 512 px tile holds the same area as a 256 px one
  return Array.from(ctx.getImageData(Math.floor((px * w) / 256), Math.floor((py * h) / 256), 1, 1).data);
}

// The Query tool's result for a tile layer: one value card.
export async function queryTile(target, layer, point, qctx, deps = {}) {
  const viz = target.viz || {};
  const t = tileAt(point.lng, point.lat, qctx.zoom, viz.maxZoom);
  const url = target.item({ x: t.x, y: t.y }, t.z);
  const card = (bands, extra) => ({
    id: layer.id + "-v",
    layerId: layer.id,
    title: layer.name,
    meta: bands.length ? "Color" : "No data",
    kind: "value",
    data: { point, bands, ...extra },
    actions: ["copy"],
  });
  let px;
  try {
    px = await (deps.loadPixel || loadPixel)(url, t.px, t.py);
  } catch (e) {
    return [card([], { error: "This layer's colors could not be read: its tile server does not allow it (" + String((e && e.message) || e) + ")." })];
  }
  if (!px || px[3] === 0) return [card([])];
  const hex = toHex(px);
  return [card([{ band: "Color", value: hex, display: hex, label: nearestClass(px, viz.classLegendDict), color: hex }])];
}
