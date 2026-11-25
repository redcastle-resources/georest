"""geoView's files ship with georest (georest.view.STATIC_DIR): every file the
page and its modules refer to is there, and nothing points back at geeViz."""
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

import pytest

from georest.view import STATIC_DIR

STATIC = pathlib.Path(STATIC_DIR)


def strip_js_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//", 1)[0] if not line.lstrip().startswith("'") else line for line in text.splitlines())


def test_stripper_drops_comments():
    assert "x" not in strip_js_comments("// x\n/* x */\ny")


def test_static_dir_has_the_page():
    assert (STATIC / "geoview.html").is_file()
    assert (STATIC / "js" / "main.js").is_file()


def test_every_page_reference_resolves():
    html = (STATIC / "geoview.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(\./[^"?#]+)"', html)
    assert refs
    for ref in refs:
        assert (STATIC / ref[2:]).is_file(), ref


def test_every_module_import_resolves():
    for js in STATIC.rglob("*.js"):
        if "vendor" in js.parts:
            continue
        text = strip_js_comments(js.read_text(encoding="utf-8"))
        for ref in re.findall(r'(?:from|import)\s*\(?\s*"(\.{1,2}/[^"]+)"', text):
            assert (js.parent / ref).resolve().is_file(), (js, ref)


def test_nothing_points_back_at_geeviz():
    for f in list(STATIC.rglob("*.js")) + list(STATIC.rglob("*.css")) + [STATIC / "geoview.html"]:
        text = f.read_text(encoding="utf-8")
        code = strip_js_comments(text) if f.suffix == ".js" else re.sub(r"<!--.*?-->", "", text, flags=re.S)
        for bad in ("./src/", "gee-run/", "./delta/"):
            assert bad not in code, (f, bad)


def test_the_logos_and_wind_are_there():
    for name in ("images/georest-logo-light.png", "images/georest-logo-dark.png",
                 "images/geeviz-logo-light.png", "images/geeviz-logo-dark.png", "js/vendor/wind-particles.js"):
        assert (STATIC / name).is_file(), name


def test_the_wheel_ships_the_viewer():
    pytest.importorskip("build")
    root = pathlib.Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as out:
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", out, str(root)], check=True, capture_output=True)
        wheel = next(pathlib.Path(out).glob("georest-*.whl"))
        names = zipfile.ZipFile(wheel).namelist()
    assert "georest/view/static/geoview.html" in names
    assert "georest/view/static/js/main.js" in names
    assert not any(n.startswith("georest/view/static/tests") for n in names)


def test_the_tab_icon_is_georest():
    html = (STATIC / "geoview.html").read_text(encoding="utf-8")
    assert '<link rel="icon" href="./images/georest-logo-dark.png" />' in html
