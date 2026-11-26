// XYZ tile services (Map.addTileLayer) and dynamic map services
// (Map.addDynamicMapService: one export image per map tile), and tile time
// lapses (one XYZ template per frame). A tile layer answers Query with the
// color under the click (engine/tilequery.js) and Chart area from its own
// tiles' pixels (engine/tilechart.js).
import { queryTile } from "../engine/tilequery.js";
import { tileChart, yearOf } from "../engine/tilechart.js";
import { tileFrameLoader, framesOf, overzoom } from "../engine/tileframes.js";

export const tilesPlugin = {
  kinds: ["tile", "dynamic", "tile.timelapse"],
  needsEE: false,
  // a tile time lapse plays frame by frame (engine/timelapse.js)
  start(layer, ctx) {
    if (layer.kind !== "tile.timelapse") return false;
    ctx.addTimelapse(layer, tileFrameLoader);
    return true;
  },
  query(target, layer, point, qctx) {
    if (target.type !== "tileMapService" || typeof target.item !== "function") return Promise.resolve([]);
    return queryTile(target, layer, point, qctx);
  },
  // Chart area: class areas (classLegendDict), the mean (palette, min and
  // max) or area by color, from the tiles over the area; over time and as
  // class transitions for a time lapse.
  chart(layer, area, cctx) {
    if (layer.kind === "dynamic") return Promise.reject(new Error(`${layer.name} cannot be charted.`));
    return tileChart(layer, area, cctx);
  },
  // a time lapse's years, from its frame labels, for the Sankey periods
  async years(layer) {
    if (layer.kind !== "tile.timelapse") return null;
    const ys = framesOf(layer).map((f) => yearOf(f.label));
    if (!ys.length || ys.some((y) => y === null)) return null;
    return [...new Set(ys)].sort((a, b) => a - b);
  },
  draw(r, item, ctx) {
    if (r.layerType === "tileMapService") {
      const maxZoom = Number.isFinite(Number(r.viz.maxZoom)) ? Number(r.viz.maxZoom) : 15;
      // past maxZoom the server has no tiles: its maxZoom tiles, scaled up
      r.overlay = ctx.tileOverlay(r, overzoom((coord, zoom) => item(coord, zoom), maxZoom));
      // LOCAL PATCH tile legend v3 is carried by the row: the legend is the row's own, shown whenever the layer is on.
      ctx.setLoading(r, false);
      ctx.sync();
      return;
    }
    // one export image per map tile, in the tile stack like any layer (the
    // original drew one image per view above the tiles); below a service's
    // minZoom there is no image
    const svcs = r.item;
    r.overlay = ctx.tileOverlay(r, (coord, zoom) => {
      const svc = zoom > svcs[1].minZoom ? svcs[1] : svcs[0];
      return ctx.groundOverlayUrl(svc.baseURL, svc.minZoom, svc.ending, zoom, ctx.tileBounds(coord.x, coord.y, zoom), 256, 256);
    });
    r.tileType = true;
    ctx.setLoading(r, false);
    ctx.sync();
  },
};
