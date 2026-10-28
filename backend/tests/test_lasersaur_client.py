"""Tests for the example HTTP client (backend/lasersaur.py).

Everything runs against a real stdlib HTTP server on 127.0.0.1 that records
the requests it receives; time.sleep is patched out where the client polls.
"""

import base64
import json
import socket
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lasersaur as ls
import pytest


class _Handler(BaseHTTPRequestHandler):
    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else None
        self.server.requests.append(
            {"method": self.command, "path": self.path, "headers": self.headers, "body": body}
        )
        resp = self.server.responses.get(self.path, (200, b"{}"))
        if isinstance(resp, list):  # sequence of responses, last one sticks
            resp = resp.pop(0) if len(resp) > 1 else resp[0]
        status, payload = resp
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _handle

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.requests = []
    srv.responses = {}
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    t.join()


@pytest.fixture
def client(server):
    return ls.Lasersaur(host="127.0.0.1", port=server.server_address[1])


@pytest.fixture
def nosleep(monkeypatch):
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)


def paths(server):
    return [r["path"] for r in server.requests]


def json_resp(obj):
    return (200, json.dumps(obj).encode())


### _request


def test_get_request_has_no_body_and_basic_auth(client, server):
    client._request("/homing")
    (req,) = server.requests
    assert req["method"] == "GET"
    assert req["path"] == "/homing"
    assert req["body"] is None
    expected = "Basic " + base64.b64encode(b"laser:laser").decode()
    assert req["headers"]["Authorization"] == expected


def test_custom_credentials_are_encoded(server):
    c = ls.Lasersaur(host="127.0.0.1", port=server.server_address[1], user="bob", pass_="p:w d")
    c._request("/x")
    auth = server.requests[0]["headers"]["Authorization"]
    assert base64.b64decode(auth.split()[1]) == b"bob:p:w d"


def test_post_is_form_urlencoded(client, server):
    client._request("/load", postdict={"a": "x y&z", "b": "é"})
    (req,) = server.requests
    assert req["method"] == "POST"
    assert req["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(req["body"]) == {"a": ["x y&z"], "b": ["é"]}


def test_ret_true_returns_parsed_json_else_none(client, server):
    server.responses["/config"] = json_resp({"a": [1, 2]})
    assert client._request("/config", ret=True) == {"a": [1, 2]}
    assert client._request("/config") is None


def test_http_400_prints_pre_text_and_reraises(client, server, capsys):
    server.responses["/bad"] = (400, b"<html><pre>bad thing</pre></html>")
    with pytest.raises(urllib.error.HTTPError) as exc:
        client._request("/bad")
    assert exc.value.code == 400
    assert capsys.readouterr().out == "WEB ERROR: bad thing\n"


def test_http_500_raises_without_web_error_print(client, server, capsys):
    server.responses["/boom"] = (500, b"<pre>oops</pre>")
    with pytest.raises(urllib.error.HTTPError) as exc:
        client._request("/boom")
    assert exc.value.code == 500
    assert capsys.readouterr().out == ""


def test_unreachable_host_raises_urlerror():
    with socket.socket() as sock:  # grab a free port, then close it: nothing listens
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(urllib.error.URLError):
        ls.Lasersaur(host="127.0.0.1", port=port)._request("/status")


### simple commands: URL formatting


@pytest.mark.parametrize(
    "call, path",
    [
        (lambda c: c.homing(), "/homing"),
        (lambda c: c.feedrate(6000), "/feedrate/6000.00"),
        (lambda c: c.feedrate(1234.5678), "/feedrate/1234.57"),
        (lambda c: c.intensity(55), "/intensity/55.00"),
        (lambda c: c.intensity(0.005), "/intensity/0.01"),
        (lambda c: c.relative(), "/relative"),
        (lambda c: c.absolute(), "/absolute"),
        (lambda c: c.move(1, 2), "/move/1.0000/2.0000/0.0000"),
        (lambda c: c.move(1.23456, -2.5, 3), "/move/1.2346/-2.5000/3.0000"),
        (lambda c: c.air_on(), "/air_on"),
        (lambda c: c.air_off(), "/air_off"),
        (lambda c: c.aux_on(), "/aux_on"),
        (lambda c: c.aux_off(), "/aux_off"),
        (lambda c: c.pulse(), "/pulse"),
        (lambda c: c.offset(10, 20.12345), "/offset/10.0000/20.1234/0.0000"),
        (lambda c: c.offset(1, 2, 3), "/offset/1.0000/2.0000/3.0000"),
        (lambda c: c.clear_offset(), "/clear_offset"),
        (lambda c: c.star("a"), "/star/a"),
        (lambda c: c.unstar("a"), "/unstar/a"),
        (lambda c: c.remove("a"), "/remove/a"),
        (lambda c: c.clear(), "/clear"),
        (lambda c: c.pause(), "/pause"),
        (lambda c: c.unpause(), "/unpause"),
        (lambda c: c.stop(), "/stop"),
        (lambda c: c.unstop(), "/unstop"),
        (lambda c: c.build(), "/build"),
        (lambda c: c.build("fw"), "/build/fw"),
        (lambda c: c.flash(), "/flash"),
        (lambda c: c.flash("fw"), "/flash/fw"),
        (lambda c: c.reset(), "/reset"),
    ],
)
def test_command_requests_expected_path(client, server, call, path):
    assert call(client) is None
    assert paths(server) == [path]
    assert server.requests[0]["method"] == "GET"


def test_negative_feedrate_formatting(client, server):
    client.feedrate(-1)
    assert paths(server) == ["/feedrate/-1.00"]


### queries returning JSON


def test_config_and_status_return_json(client, server):
    server.responses["/config"] = json_resp({"feedrate": 2000})
    server.responses["/status"] = json_resp({"ready": True})
    assert client.config() == {"feedrate": 2000}
    assert client.status() == {"ready": True}


@pytest.mark.parametrize(
    "kind, path",
    [(None, "/listing"), ("starred", "/listing/starred"), ("unstarred", "/listing/unstarred")],
)
def test_listing_kinds(client, server, kind, path):
    server.responses[path] = json_resp(["j1", "j2"])
    assert client.listing(kind) == ["j1", "j2"]
    assert paths(server) == [path]


def test_listing_unknown_kind_is_an_error(client):
    with pytest.raises(ValueError, match="bogus"):
        client.listing("bogus")


def test_get_and_library_helpers(client, server):
    for p in ("/get/j", "/listing_library", "/get_library/lib", "/load_library/lib"):
        server.responses[p] = json_resp({"p": p})
    assert client.get("j") == {"p": "/get/j"}
    assert client.listing_library() == {"p": "/listing_library"}
    assert client.get_library("lib") == {"p": "/get_library/lib"}
    assert client.load_library("lib") == {"p": "/load_library/lib"}
    assert paths(server) == ["/get/j", "/listing_library", "/get_library/lib", "/load_library/lib"]


### load


def _load_request(server):
    (req,) = server.requests
    assert req["method"] == "POST" and req["path"] == "/load"
    form = urllib.parse.parse_qs(req["body"])
    assert list(form) == ["load_request"]
    return json.loads(form["load_request"][0])


def test_load_dict_is_serialised_to_string(client, server):
    server.responses["/load"] = json_resp("myjob")
    assert client.load({"defs": []}, name="myjob") == "myjob"
    assert _load_request(server) == {"job": '{"defs": []}', "name": "myjob", "optimize": True}


def test_load_bytes_decoded_and_str_passed_through(client, server):
    client.load(b"<svg/>", optimize=False)
    assert _load_request(server) == {"job": "<svg/>", "name": "job", "optimize": False}
    server.requests.clear()
    client.load("G0 X1")
    assert _load_request(server)["job"] == "G0 X1"


def test_load_path_wraps_polyline(client, server):
    path = [[0, 0, 0], [10, 5, 0]]
    client.load_path(path, 1500, 40)
    job = json.loads(_load_request(server)["job"])
    assert job["defs"] == [{"kind": "path", "data": [path]}]
    assert job["items"] == [{"def": 0}]
    assert job["passes"] == [{"items": [0], "feedrate": 1500, "intensity": 40}]


def test_load_image_embeds_base64_png(client, server, tmp_path):
    img = tmp_path / "i.png"
    img.write_bytes(b"\x89PNGdata")
    client.load_image(str(img), (1, 2), (30, 40), feedrate=100, intensity=7)
    job = json.loads(_load_request(server)["job"])
    d = job["defs"][0]
    assert d["kind"] == "image" and base64.b64decode(d["data"]) == b"\x89PNGdata"
    assert d["pos"] == [1, 2] and d["size"] == [30, 40]
    assert job["items"] == [{"def": 0}]
    assert job["passes"] == [{"items": [0], "feedrate": 100, "intensity": 7}]


@pytest.mark.parametrize("which", ["path", "image"])
def test_load_path_and_image_produce_current_dba_layout(client, server, tmp_path, which):
    if which == "path":
        client.load_path([[0, 0, 0], [1, 1, 0]], 1000, 10)
    else:
        img = tmp_path / "i.png"
        img.write_bytes(b"x")
        client.load_image(str(img), (0, 0), (1, 1))
    job = json.loads(_load_request(server)["job"])
    assert "defs" in job and "items" in job


### local files (need jobimport)

DBA = {
    "head": {},
    "passes": [{"items": [0], "feedrate": 1000, "intensity": 10}],
    "items": [{"def": 0, "color": "#000000"}],
    "defs": [{"kind": "path", "data": [[[0, 0, 0], [10, 0, 0]]]}],
}


@pytest.fixture
def dbafile(tmp_path):
    p = tmp_path / "square.dba"
    p.write_text(json.dumps(DBA))
    return str(p)


def test_open_file_returns_parsed_dba(client, dbafile):
    job = client.open_file(dbafile)
    assert job["defs"][0]["kind"] == "path"
    assert client.open_file(dbafile, optimize=False, tolerance=0.5)["defs"]


def test_convert_file_writes_conv_dba(client, dbafile, capsys):
    client.convert_file(dbafile)
    out = dbafile.replace("square.dba", "square.conv.dba")
    with open(out) as fp:
        assert json.load(fp)["defs"][0]["kind"] == "path"
    assert "job file written to" in capsys.readouterr().out


def test_load_file_uses_basename_and_skips_server_optimize(client, server, dbafile):
    server.responses["/load"] = json_resp("square")
    assert client.load_file(dbafile) == "square"
    req = _load_request(server)
    assert req["name"] == "square" and req["optimize"] is False
    assert json.loads(req["job"])["defs"]


def test_run_file_loads_then_runs(client, server, dbafile, monkeypatch):
    ran = []
    monkeypatch.setattr(client, "run", lambda name, progress=True: ran.append((name, progress)))
    server.responses["/load"] = json_resp("square")
    assert client.run_file(dbafile, progress=False) == "square"
    assert ran == [("square", False)]


def test_run_file_local_retargets_to_localhost_4444(client, dbafile, monkeypatch):
    monkeypatch.setattr(client, "load", lambda *a, **k: "n")
    monkeypatch.setattr(client, "run", lambda *a, **k: None)
    client.run_file(dbafile, local=True)
    assert (client.host, client.port) == ("127.0.0.1", 4444)


def test_run_file_applies_feedrate_to_dba(client, dbafile, monkeypatch):
    sent = {}
    monkeypatch.setattr(client, "load", lambda job, **k: sent.update(job) or "n")
    monkeypatch.setattr(client, "run", lambda *a, **k: None)
    client.run_file(dbafile, feedrate=1234, intensity=5, progress=False)
    assert sent["passes"][0]["feedrate"] == 1234 and sent["passes"][0]["intensity"] == 5


def test_run_file_job_without_passes_gets_default_pass(client, monkeypatch):
    job = {"items": [{"def": 0}, {"def": 1}], "defs": []}
    monkeypatch.setattr(client, "open_file", lambda *a, **k: job)
    sent = {}
    monkeypatch.setattr(client, "load", lambda j, **k: sent.update(j) or "n")
    monkeypatch.setattr(client, "run", lambda *a, **k: None)
    client.run_file("/x/y.svg", intensity=9)
    assert sent["passes"] == [{"items": [0, 1], "feedrate": 4000, "intensity": 9}]
    job.pop("passes")
    client.run_file("/x/y.svg", feedrate=500)
    assert sent["passes"][0]["feedrate"] == 500


### job execution


def test_run_without_progress_only_requests(client, server):
    client.run("job1", progress=False)
    assert paths(server) == ["/run/job1"]


def test_run_polls_status_until_ready(client, server, nosleep, capsys):
    server.responses["/status"] = [
        json_resp({"ready": False, "progress": 0.1}),
        json_resp({"ready": False, "progress": 0.5}),
        json_resp({"ready": True, "progress": 1.0}),
    ]
    client.run("job1")
    assert paths(server) == ["/run/job1", "/status", "/status", "/status"]
    out = capsys.readouterr().out
    assert "Done!" in out and out.count("\b.") >= 15


def test_cancel_pauses_waits_stops_and_homes_origin(client, server, nosleep):
    server.responses["/status"] = [json_resp({"ready": False}), json_resp({"ready": True})]
    client.cancel()
    assert paths(server) == [
        "/pause",
        "/status",
        "/status",
        "/stop",
        "/unstop",
        "/move/0.0000/0.0000/0.0000",
    ]


### module-level API


def test_module_aliases_are_bound_to_singleton():
    assert ls.move.__self__ is ls.lasersaur
    assert ls.run.__func__ is ls.Lasersaur.run
    assert ls.lasersaur.host == "lasersaur.local" and ls.lasersaur.port == 80


def test_local_points_singleton_at_localhost(monkeypatch):
    monkeypatch.setattr(ls.lasersaur, "host", "elsewhere")
    monkeypatch.setattr(ls.lasersaur, "port", 1)
    ls.local()
    assert (ls.lasersaur.host, ls.lasersaur.port) == ("127.0.0.1", 4444)


def test_module_functions_hit_the_singletons_server(server, monkeypatch):
    monkeypatch.setattr(ls.lasersaur, "host", "127.0.0.1")
    monkeypatch.setattr(ls.lasersaur, "port", server.server_address[1])
    server.responses["/status"] = json_resp({"ready": True})
    ls.move(1, 2)
    assert ls.status() == {"ready": True}
    assert paths(server) == ["/move/1.0000/2.0000/0.0000", "/status"]
