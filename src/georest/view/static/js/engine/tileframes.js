// Tile time lapses: one XYZ template per frame (Map.addTimeLapse with tile
// templates), played by engine/timelapse.js through its frame loader
// contract {prepare(), years(), frame(i), queryTarget()}. Chart area reads
// the same templates (engine/tilechart.js).
import { legendFor } from "./viz.js";

// A template's tile URL: {z}, {x} and {y} filled in (every occurrence).
export const templateUrl = (tmpl) => (c, z) => String(tmpl).split("{z}").join(z).split("{x}").join(c.x).split("{y}").join(c.y);

// Past maxZoom a tile server has no tiles: the map tile shows its piece of
// the maxZoom tile holding it, scaled up (engine/tiles.js block tiles).
export function overzoom(urlFor, maxZoom) {
  const mz = Number(maxZoom);
  if (maxZoom === undefined || maxZoom === null || !Number.isFinite(mz)) return urlFor;
  return (c, z) => {
    if (z <= mz) return urlFor(c, z);
    const k = 2 ** (z - mz);
    const px = Math.floor(c.x / k);
    const py = Math.floor(c.y / k);
    return { url: urlFor({ x: px, y: py }, mz), block: { size: 256 * k, x: (c.x - px * k) * 256, y: (c.y - py * k) * 256 } };
  };
}

// A tile layer's frames: [{label, urlFor(coord, zoom)}]. A time lapse's item
// is [{label, url}]; a single tile layer is one frame with no label.
export function framesOf(layer) {
  const item = layer && layer.item;
  if (Array.isArray(item)) {
    return item.map((f) => ({ label: String(f.label), urlFor: typeof f.url === "function" ? f.url : templateUrl(f.url) }));
  }
  if (typeof item === "function") return [{ label: null, urlFor: item }];
  if (typeof item === "string") return [{ label: null, urlFor: templateUrl(item) }];
  return [];
}

export function tileFrameLoader(engine, layer) {
  const frames = framesOf(layer);
  const viz = { ...(layer.viz || {}), layerType: "tileMapService" };
  const maxZoom = Number.isFinite(Number(viz.maxZoom)) ? Number(viz.maxZoom) : 15;
  // the frame shown now, for the Query tool
  const shown = () => {
    const l = engine.store && engine.store.get().layers.find((x) => x.id === layer.id);
    const i = l && l.timelapse && Number.isInteger(l.timelapse.frame) ? l.timelapse.frame : 0;
    return frames[Math.min(Math.max(i, 0), frames.length - 1)];
  };
  return {
    // a tile server is quick, but a long time lapse is many tiles: the frame
    // shown and the next two are on the map, as an ArcGIS time lapse's
    prefetch: 2,
    async prepare() {
      return { legend: legendFor(viz, layer.name), viz };
    },
    async years() {
      if (!frames.length) throw new Error(`${layer.name} has no frames to play.`);
      return frames.map((f) => f.label);
    },
    // drawn past maxZoom from the maxZoom tiles, as a tile layer
    async frame(i) {
      return overzoom(frames[i].urlFor, maxZoom);
    },
    // the Query tool reads the frame on the map
    async queryTarget() {
      if (!frames.length) return null;
      return { type: "tileMapService", item: (c, z) => shown().urlFor(c, z), viz };
    },
  };
}
