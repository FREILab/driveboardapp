"""Run with: pytest freilab/test_overlay.py  (needs webtest, like backend/tests).
Separately from backend/tests: the overlay patches the backend globally."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))

import jobimport  # noqa: E402
from jobimport import issues, template  # noqa: E402


@pytest.fixture(scope="module")
def client():
    import bottle
    import webtest

    import config
    import run  # noqa: F401  (wraps config.load)

    config.load(None)
    config.conf["users"] = {"test": "test"}  # library routes sit behind basic auth
    app = webtest.TestApp(bottle.default_app())
    app.authorization = ("Basic", ("test", "test"))
    return app


def test_app_html_has_overlay_and_upstream_is_untouched(client):
    body = client.get("/").text
    assert "/freilab.js" in body and 'id="material_modal"' in body
    assert 'id="run_btn"' in body  # upstream's button, same id


def test_overlay_files_win_and_upstream_files_still_served(client):
    assert "freilab_run_job" in client.get("/freilab.js").text
    assert client.get("/img/material/mdf_3.png").content_type == "image/png"
    assert client.get("/app.js").status_int == 200  # upstream
    assert client.get("/css/style.css").status_int == 200  # upstream


def test_library_lists_and_loads_our_jobs_next_to_upstream_ones(client):
    files = json.loads(client.get("/listing_library").text)["files"]
    assert "Test_6Linien" in files and "lines" in files  # ours and upstream's
    assert client.get("/get_library/Test_6Linien").status_int == 200
    assert client.get("/get_library/lines").status_int == 200
    assert client.get("/load_library/Test_6Linien").status_int == 200
    assert client.get("/get_library/gibtsnicht", expect_errors=True).status_int == 400


def _lib(name):
    with open(os.path.join(HERE, "library", name + ".dba")) as fp:
        return json.load(fp)


def _render(name, params=None):
    with issues.collecting() as found:
        job = jobimport.convert(_lib(name)["template"]["svg"], optimize=False, params=params)
    assert found.issues() == []  # fonts, colours, tags all clean
    return job


def test_power_squares_defaults_match_todays_job():
    job = _render("Test_PowerQuadrate")
    assert [(p["feedrate"], p["intensity"]) for p in job["passes"]] == \
        [(2000, 15)] + [(f, 100) for f in (2000, 1900, 1800, 1700, 1600, 1500)]


def test_power_squares_follow_feed_range():
    job = _render("Test_PowerQuadrate", {"vmin": 1000, "vmax": 1500})
    assert [p["feedrate"] for p in job["passes"][1:]] == [1500, 1400, 1300, 1200, 1100, 1000]
    svg = template.render(_lib("Test_PowerQuadrate")["template"]["svg"], {"vmin": 1000, "vmax": 1500})
    assert svg.count(">1400<") == 2  # inside and below the square


def test_six_lines_follow_feed_and_power():
    job = _render("Test_6Linien", {"vmin": 1000, "vmax": 1500, "p": 80})
    assert [(p["feedrate"], p["intensity"]) for p in job["passes"]] == \
        [(f, 80) for f in (1500, 1400, 1300, 1200, 1100, 1000)]
    first = job["defs"][job["items"][job["passes"][0]["items"][0]]["def"]]["data"][0]
    assert first == [[0, -2.5], [0, 2.5]]  # (0,0) still on the top edge


def _bbox(job, color):
    pts = [p for it in job["items"] if it.get("color") == color
           for path in job["defs"][it["def"]]["data"] for p in path]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.parametrize("d", [3, 9, 20])
def test_fix_focus_strip_is_d_high_and_label_fits(d):
    job = _render("Parts_FixFocus", {"d": d})
    strip = [path for it in job["items"] if it.get("color") == "#000000"
             for path in job["defs"][it["def"]]["data"]
             if min(p[0] for p in path) > 35]  # right of the disc
    ys = [p[1] for path in strip for p in path]
    assert max(ys) - min(ys) == pytest.approx(d, abs=0.05)
    x0, y0, x1, y1 = _bbox(job, "#e00000")
    assert min(ys) < y0 and y1 < max(ys)  # label inside the strip


def test_fix_focus_label_says_fix_focus():
    svg = template.render(_lib("Parts_FixFocus")["template"]["svg"], {"d": 12})
    assert "Fix Focus 12 mm" in svg


def test_focus_files_renamed(client):
    files = json.loads(client.get("/listing_library").text)["files"]
    assert "Parts_FixFocus" in files and "Parts_FocusStairs_3-30mm" in files
    assert "Parts_Fokus9mm" not in files and "Parts_Fokushilfe_3-30mm" not in files
