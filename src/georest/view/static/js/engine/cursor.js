// The one owner of the map's draggable cursor. It is derived from the store,
// as the original's toolCursorLookup: a crosshair while Distance or Area is
// active or Chart area draws its area, a pointer while it selects features,
// and the question mark ("help") while Query is on, "progress" while a query
// runs. No other module sets draggableCursor, so tools cannot undo each other.
// Double-click zoom is derived the same way (createDoubleClickZoom).

export function cursorFor(state) {
  const t = state.tool;
  if (t === "distance" || t === "area") return "crosshair";
  if (t === "chart" && state.areaFrom === "draw") return "crosshair";
  if (t === "chart" && state.areaFrom === "select") return "pointer";
  if (t === "query") return state.querying ? "progress" : "help";
  return null;
}

export function createCursor(gmap, store) {
  let now;
  const apply = () => {
    const next = cursorFor(store.get());
    if (next === now) return next;
    now = next;
    gmap.setOptions({ draggableCursor: next });
    return next;
  };
  store.on("change:tool", apply);
  store.on("change:areaFrom", apply);
  store.on("change:querying", apply);
  apply();
  return { apply, current: () => now };
}

// Double-click zooms the map, except while a tool finishes a shape with it
// (Distance, Area, Chart area's Draw): there it ends the line or polygon.
export function doubleClickZooms(state) {
  const t = state.tool;
  if (t === "distance" || t === "area") return false;
  if (t === "chart" && state.areaFrom === "draw") return false;
  return true;
}

export function createDoubleClickZoom(gmap, store) {
  let now;
  const apply = () => {
    const next = doubleClickZooms(store.get());
    if (next === now) return next;
    now = next;
    gmap.setOptions({ disableDoubleClickZoom: !next });
    return next;
  };
  store.on("change:tool", apply);
  store.on("change:areaFrom", apply);
  apply();
  return { apply, current: () => now };
}
