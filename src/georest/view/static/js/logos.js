// The viewer's logo: georest's, unless the map document names another one it
// ships (geeViz names its own). Only names listed here are used, so a
// document cannot point the logo at an arbitrary file.
//
// The georest files are named for the gear's color (the dark gear sits on
// the light theme); the geeViz files are named for the theme they sit on.
const LOGOS = {
  georest: { light: "./images/georest-logo-dark.png", dark: "./images/georest-logo-light.png" },
  geeviz: { light: "./images/geeviz-logo-light.png", dark: "./images/geeviz-logo-dark.png" },
};

export function logoSrc(brand, theme) {
  const set = LOGOS[brand && brand.logo] || LOGOS.georest;
  return theme === "dark" ? set.dark : set.light;
}
