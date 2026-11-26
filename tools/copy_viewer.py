"""Copy geeViz's Delta viewer into georest as geoView (georest.view).

One-time move for release step 3 of docs/superpowers/specs/2026-11-24-geoview-design.md,
kept for provenance. Bytes are copied as they are (line endings kept); only
the paths that pointed into geeViz's layout are rewritten.

    python tools/copy_viewer.py C:\\RCR\\geeVizBuilder\\geeViz
"""
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
STATIC = HERE / "src" / "georest" / "view" / "static"
TESTS = HERE / "tests" / "view" / "js"


def rewrite(path, pairs):
    data = path.read_bytes()
    for old, new in pairs:
        data = data.replace(old.encode(), new.encode())
    path.write_bytes(data)


def main(geeviz):
    src = pathlib.Path(geeviz) / "geeView"
    delta = src / "delta"
    for d in (STATIC, TESTS):
        if d.exists():
            shutil.rmtree(d)
    shutil.copytree(delta / "js", STATIC / "js")
    shutil.copytree(delta / "css", STATIC / "css")
    (STATIC / "images").mkdir(parents=True)
    for name in ("geeviz-logo-light.png", "geeviz-logo-dark.png"):
        shutil.copy2(src / "src" / "assets" / "images" / name, STATIC / "images" / name)
    shutil.copy2(src / "src" / "js" / "wind-particles.js", STATIC / "js" / "wind-particles.js")

    page = STATIC / "geoview.html"
    shutil.copy2(src / "delta.html", page)
    text = page.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    # the run script tags and the ?run= loader: a document replaces them; an
    # exported page that carries a run script inlines it at the marker
    text = re.sub(
        r"[ \t]*<!-- Per-session run script.*?<!-- /run-loader -->",
        "    <!-- run-script: an exported page carrying a run script inlines it here -->",
        text,
        flags=re.S,
    )
    for old, new in (
        ("./src/assets/images/geeViz-logo-light.png", "./images/geeviz-logo-light.png"),
        ("./src/assets/images/", "./images/"),
        ("./src/js/wind-particles.js", "./js/wind-particles.js"),
        ("./delta/", "./"),
    ):
        text = text.replace(old, new)
    page.write_bytes(text.replace("\r\n", "\n").replace("\n", nl).encode("utf-8"))
    rewrite(STATIC / "js" / "ui" / "shell.js", [("./src/assets/images/geeviz-logo-", "./images/geeviz-logo-")])

    shutil.copytree(delta / "tests", TESTS)
    # the recorded Earth Engine chart answers the chart tests check against
    # (written by geeViz's tests/delta/make_fixtures.py, which needs Earth Engine)
    shutil.copytree(pathlib.Path(geeviz) / "tests" / "delta" / "fixtures", TESTS / "fixtures" / "charts")
    for t in TESTS.glob("*.mjs"):
        rewrite(t, [
            ('"../js/', '"../../../src/georest/view/static/js/'),
            ('"../../../../lcms-viewer/', '"../../../../geeVizBuilder/lcms-viewer/'),
            ('"../../../../venv/', '"../../../../geeVizBuilder/venv/'),
            ('"..", "..", "..", "tests", "delta", "fixtures")', '"fixtures", "charts")'),
            ('"..", "js", "engine")', '"..", "..", "..", "src", "georest", "view", "static", "js", "engine")'),
        ])
    print("copied to", STATIC, "and", TESTS)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\RCR\geeVizBuilder\geeViz")
