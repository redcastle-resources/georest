// The viewer's logo (js/logos.js): georest's unless a map document names another.
import { test } from "node:test";
import assert from "node:assert/strict";
import { logoSrc } from "../../../src/georest/view/static/js/logos.js";

test("a map with no brand shows the georest logo for the theme", () => {
  // the georest files are named for the gear's color: the dark gear sits on the light theme
  assert.equal(logoSrc(null, "light"), "./images/georest-logo-dark.png");
  assert.equal(logoSrc(null, "dark"), "./images/georest-logo-light.png");
});

test("a document that names the geeViz logo gets it", () => {
  assert.equal(logoSrc({ name: "geeViz ", accent: "Delta", logo: "geeviz" }, "light"), "./images/geeviz-logo-light.png");
  assert.equal(logoSrc({ name: "geeViz ", accent: "Delta", logo: "geeviz" }, "dark"), "./images/geeviz-logo-dark.png");
});

test("an unknown logo name falls back to georest, never to an arbitrary path", () => {
  assert.equal(logoSrc({ logo: "../../evil" }, "light"), "./images/georest-logo-dark.png");
});
