// The Query tool's gestures on the map.
//
// While Query is on, the pointer over a visible vector drawn in the browser
// (GeoJSON, ArcGIS feature layers) highlights the feature under it and shows
// a one-line tooltip with its name. Hover asks the network nothing: it hit
// tests the parts already drawn (engine/hittest.js), once per animation
// frame. Image, tile and Earth Engine layers have nothing to hover. Devices
// without a hovering pointer (phones, tablets) get no hover; a tap queries.
import { hitTest } from "./hittest.js";
import { isChartOnly } from "../util.js";

const NAME_KEYS = ["name", "title", "label"];
const present = (v) => v !== undefined && v !== null && String(v).trim() !== "";

// The feature's name for the tooltip: a name, NAME, title or label field (any
// case), else the layer's display field, else the layer name.
export function hoverName(props, { displayField, layerName } = {}) {
  const p = props || {};
  const keys = Object.keys(p);
  for (const want of NAME_KEYS) {
    const exact = keys.find((k) => k === want && present(p[k]));
    const k = exact || keys.find((x) => x.toLowerCase() === want && present(p[x]));
    if (k) return String(p[k]);
  }
  if (displayField && present(p[displayField])) return String(p[displayField]);
  return layerName ? String(layerName) : "";
}

// Layers hover can name: visible vectors drawn in the browser.
const HOVER_KINDS = ["geojson", "esri.feature"];
export const isHoverable = (layer) => !!layer && !!layer.visible && HOVER_KINDS.indexOf(layer.kind) > -1 && !isChartOnly(layer);

// topHit([{layer, parts, displayField}] top layer first, {lng, lat}, zoom)
// -> {layer, key, name, feature} of the top feature under the pointer, or null.
export function topHit(sources, at, zoom) {
  for (const s of sources || []) {
    if (!s || !s.parts || !s.parts.length) continue;
    const hits = hitTest(s.parts, at, zoom);
    if (!hits.length) continue;
    const p = hits[0];
    return {
      layer: s.layer,
      key: s.layer.id + ":" + p.fid,
      name: hoverName(p.props, { displayField: s.displayField, layerName: s.layer.name }),
      feature: { type: "Feature", properties: {}, geometry: p.geom },
    };
  }
  return null;
}

function orderedLayers(s) {
  const byId = new Map(s.layers.map((l) => [l.id, l]));
  const listed = s.order.filter((id) => byId.has(id)).map((id) => byId.get(id));
  return listed.concat(s.layers.filter((l) => s.order.indexOf(l.id) === -1));
}

// The map side of hover. opts: canHover(), picking(), colors() -> {stroke}.
export function createHover(engine, store, opts = {}) {
  const g = opts.google || window.google.maps;
  const gmap = engine.gmap;
  const canHover = opts.canHover || (() => true);
  const picking = opts.picking || (() => false);
  const colors = () => (opts.colors ? opts.colors() : { stroke: "#ff834c" });
  const raf = opts.requestFrame || ((fn) => requestAnimationFrame(fn));

  const lit = new g.Data({ map: gmap });
  const restyle = () => {
    const c = colors().stroke;
    lit.setStyle({
      strokeColor: c,
      strokeWeight: 3,
      fillColor: c,
      fillOpacity: 0.12,
      clickable: false,
      zIndex: 3100,
      icon: { path: g.SymbolPath.CIRCLE, scale: 7, fillColor: c, fillOpacity: 0.35, strokeColor: c, strokeWeight: 2 },
    });
  };
  restyle();

  const tip = document.createElement("div");
  tip.className = "dz-hover-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);

  let litKey = null;
  let litFeatures = [];
  let last = null;
  let queued = false;

  function hide() {
    last = null;
    tip.hidden = true;
    if (litKey !== null) {
      litFeatures.forEach((f) => lit.remove(f));
      litFeatures = [];
      litKey = null;
    }
  }

  function sources(zoom) {
    const out = [];
    orderedLayers(store.get()).forEach((layer) => {
      if (!isHoverable(layer)) return;
      const d = engine.layers && engine.layers.drawnParts ? engine.layers.drawnParts(layer.id, zoom) : null;
      if (d && d.parts.length) out.push({ layer, parts: d.parts, displayField: d.displayField });
    });
    return out;
  }

  function frame() {
    queued = false;
    if (!last) return;
    const { point, x, y } = last;
    const zoom = gmap.getZoom();
    const hit = topHit(sources(zoom), point, zoom);
    if (!hit) return hide();
    if (hit.key !== litKey) {
      litFeatures.forEach((f) => lit.remove(f));
      litFeatures = lit.addGeoJson(hit.feature);
      litKey = hit.key;
    }
    tip.textContent = hit.name;
    tip.hidden = !hit.name;
    tip.style.left = x + 14 + "px";
    tip.style.top = y + 16 + "px";
  }

  gmap.addListener("mousemove", (ev) => {
    if (!ev.latLng || store.get().tool !== "query" || !canHover() || picking()) {
      if (litKey !== null || !tip.hidden) hide();
      return;
    }
    const d = ev.domEvent || {};
    if (d.pointerType === "touch") return;
    last = { point: { lng: ev.latLng.lng(), lat: ev.latLng.lat() }, x: d.clientX || 0, y: d.clientY || 0 };
    if (!queued) {
      queued = true;
      raf(frame);
    }
  });
  gmap.addListener("mouseout", hide);
  gmap.addListener("dragstart", hide);
  store.on("change:tool", hide);

  return { hide, restyle, state: () => ({ key: litKey, text: tip.hidden ? null : tip.textContent }) };
}
