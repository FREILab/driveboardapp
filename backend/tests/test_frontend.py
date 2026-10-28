"""Frontend smoke tests: every static asset the app serves loads with 200.

Exercises the bottle static-file routes against the real frontend tree so a
missing or mis-routed asset (the kind of breakage a refactor causes) is caught.
"""

import glob
import os

import pytest
import web
from config import conf

# `app` fixture is provided by conftest.py.
# Mirror web.py's choice of frontend vs frontend_mill rather than hardcoding.
FRONTEND_DIR = os.path.join(conf["rootdir"], web.frontend_path)


def _asset_routes():
    """(url, filesystem path) for every served JS/CSS asset."""
    routes = []
    for path in glob.glob(os.path.join(FRONTEND_DIR, "*.js")):
        routes.append(("/" + os.path.basename(path), path))
    for path in glob.glob(os.path.join(FRONTEND_DIR, "js", "*.js")):
        routes.append(("/js/" + os.path.basename(path), path))
    for path in glob.glob(os.path.join(FRONTEND_DIR, "css", "*.css")):
        routes.append(("/css/" + os.path.basename(path), path))
    return routes


def test_index_serves_app_html(app):
    resp = app.get("/")
    assert resp.status_int == 200
    body = resp.body.decode("utf-8", "ignore").lower()
    assert "<html" in body or "<!doctype" in body


def test_static_assets_discovered():
    assert _asset_routes(), "expected frontend js/css assets to serve"


@pytest.mark.parametrize("url", [r[0] for r in _asset_routes()])
def test_static_asset_served(app, url):
    resp = app.get(url)
    assert resp.status_int == 200, url


def test_missing_asset_404s(app):
    resp = app.get("/this-asset-does-not-exist.js", expect_errors=True)
    assert resp.status_int == 404


@pytest.mark.parametrize("url", ["/"] + [r[0] for r in _asset_routes()])
def test_static_asset_must_revalidate(app, url):
    # without this a browser guesses a freshness lifetime from the file's age
    # and can run frontend code older than the backend it talks to
    resp = app.get(url)
    assert "no-cache" in resp.headers.get("Cache-Control", ""), url


def test_revalidated_asset_still_answers_304(app):
    # revalidation has to stay cheap, so an unchanged asset returns no body
    resp = app.get("/jobhandler.js")
    for header in ("If-None-Match", "If-Modified-Since"):
        value = resp.headers["Etag"] if header == "If-None-Match" else resp.headers["Last-Modified"]
        again = app.get("/jobhandler.js", headers={header: value}, status="*")
        assert again.status_int == 304, header
        assert "no-cache" in again.headers.get("Cache-Control", ""), header


# ---------------------------------------------------------------------------
# Mill mode: frontend_mill serves, falling back to frontend for shared assets
# ---------------------------------------------------------------------------


def _read(*parts):
    with open(os.path.join(conf["rootdir"], *parts), "rb") as fp:
        return fp.read()


@pytest.fixture
def mill_app(app, monkeypatch):
    monkeypatch.setattr(web, "frontend_path", "frontend_mill")
    return app


def test_mill_mode_serves_mill_index(mill_app):
    assert mill_app.get("/").body == _read("frontend_mill", "app.html")


def test_mill_mode_falls_back_to_shared_asset(mill_app):
    # img/ and the bootstrap css only exist in the laser frontend
    assert not os.path.exists(os.path.join(conf["rootdir"], "frontend_mill", "img"))
    resp = mill_app.get("/img/close.gif")
    assert resp.body == _read("frontend", "img", "close.gif")
    assert "no-cache" in resp.headers["Cache-Control"]
    assert mill_app.get("/css/bootstrap.min.css").body == _read(
        "frontend", "css", "bootstrap.min.css"
    )


def test_mill_local_file_wins_over_fallback(mill_app):
    # same path in both trees, different content: the mill copy must be served
    mill, laser = _read("frontend_mill", "css", "style.css"), _read("frontend", "css", "style.css")
    assert mill != laser
    assert mill_app.get("/css/style.css").body == mill
    assert mill_app.get("/js/FileSaver.js").body == _read("frontend_mill", "js", "FileSaver.js")


def test_mill_only_file_is_served(mill_app):
    assert not os.path.exists(os.path.join(conf["rootdir"], "frontend", "js", "three.min.js"))
    assert mill_app.get("/js/three.min.js").body == _read("frontend_mill", "js", "three.min.js")


def test_mill_top_level_files_never_fall_back(mill_app):
    # tools.js is a laser-only top-level script, it must not leak into mill mode
    assert os.path.isfile(os.path.join(conf["rootdir"], "frontend", "tools.js"))
    assert mill_app.get("/tools.js", expect_errors=True).status_int == 404


def test_mill_missing_asset_404s_after_fallback(mill_app):
    assert mill_app.get("/js/nope.js", expect_errors=True).status_int == 404


def test_laser_mode_does_not_see_mill_only_files(app):
    assert app.get("/js/three.min.js", expect_errors=True).status_int == 404


def test_favicon_served_in_both_modes(app, monkeypatch):
    expected = _read("frontend", "img", "favicon.ico")
    assert app.get("/favicon.ico").body == expected
    monkeypatch.setattr(web, "frontend_path", "frontend_mill")
    assert app.get("/favicon.ico").body == expected  # via the fallback


@pytest.mark.parametrize("mode", ["frontend", "frontend_mill"])
@pytest.mark.parametrize(
    "url",
    ["/js/../../backend/web.py", "/css/../../backend/web.py", "/js/%2e%2e/%2e%2e/backend/web.py"],
)
def test_path_traversal_is_refused(app, monkeypatch, mode, url):
    monkeypatch.setattr(web, "frontend_path", mode)
    resp = app.get(url, expect_errors=True)
    assert resp.status_int != 200
    assert b"bottle.default_app" not in resp.body


def test_path_traversal_refused_by_handler_itself():
    # webtest/WSGI may normalise the URL, so also hit the handler directly
    resp = web._frontend_file("js/../../backend/web.py")
    assert resp.status_code == 403
