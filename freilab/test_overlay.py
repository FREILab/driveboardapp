"""Run with: pytest freilab/test_overlay.py  (needs webtest, like backend/tests)"""

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
    return webtest.TestApp(bottle.default_app())


def test_app_html_has_overlay_and_upstream_is_untouched(client):
    body = client.get("/").text
    assert "/freilab.js" in body and 'id="material_modal"' in body
    assert 'id="run_btn"' in body  # upstream's button, same id


def test_overlay_files_win_and_upstream_files_still_served(client):
    assert "freilab_run_job" in client.get("/freilab.js").text
    assert client.get("/img/material/mdf_3.png").content_type == "image/png"
    assert client.get("/app.js").status_int == 200  # upstream
    assert client.get("/css/style.css").status_int == 200  # upstream
