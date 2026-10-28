"""Run with: pytest freilab/test_overlay.py  (needs webtest, like backend/tests).
Separately from backend/tests: the overlay patches the backend globally."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))


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
