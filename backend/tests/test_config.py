"""Tests for the configuration module (defaults + load/write round-trips)."""

import copy
import importlib.util
import json
import os
import sys
import tempfile
import threading
import types

import pytest


def test_defaults_present():
    import config

    assert config.conf["appname"] == "driveboardapp"
    assert isinstance(config.conf["network_port"], int)
    assert len(config.conf["workspace"]) == 3
    # conf_defaults is a snapshot mirroring the live defaults at import time.
    assert config.conf_defaults["network_port"] == config.conf["network_port"]


def test_userconfigurable_keys_are_real_conf_keys():
    import config

    for key in config.userconfigurable:
        assert key in config.conf, f"{key} documented but missing from conf"


def test_load_creates_default_config_file(isolated_config):
    config = isolated_config
    config.load("")  # empty name -> config.json, auto-created
    expected = os.path.join(config.conf["confdir"], "config.json")
    assert os.path.exists(expected)
    with open(expected) as fp:
        data = json.load(fp)
    # Only user-configurable keys are written out.
    for key in data:
        assert key in config.userconfigurable
    assert "users" not in data  # security-sensitive, never serialized


def test_load_applies_user_overrides(isolated_config):
    config = isolated_config
    path = os.path.join(config.conf["confdir"], "config.testcfg.json")
    with open(path, "w") as fp:
        json.dump({"feedrate": 1234, "grid_mm": 25}, fp)

    config.load("testcfg")
    assert config.conf["feedrate"] == 1234
    assert config.conf["grid_mm"] == 25


def test_load_ignores_non_userconfigurable_keys(isolated_config):
    config = isolated_config
    before_port = config.conf["network_port"]
    path = os.path.join(config.conf["confdir"], "config.evil.json")
    with open(path, "w") as fp:
        # network_port is NOT user-configurable; must be ignored on load.
        json.dump({"network_port": 9999, "feedrate": 4321}, fp)

    config.load("evil")
    assert config.conf["network_port"] == before_port
    assert config.conf["feedrate"] == 4321


def test_write_config_fields_round_trip(isolated_config):
    config = isolated_config
    # load() exits if a named config file is missing, so seed it first.
    path = os.path.join(config.conf["confdir"], "config.rt.json")
    with open(path, "w") as fp:
        json.dump({}, fp)
    config.load("rt")  # establishes configpath
    config.write_config_fields({"feedrate": 777})
    with open(config.configpath) as fp:
        data = json.load(fp)
    assert data["feedrate"] == 777


def test_concurrent_config_updates_are_not_lost(isolated_config):
    config = isolated_config
    path = os.path.join(config.conf["confdir"], "config.concurrent.json")
    with open(path, "w") as fp:
        json.dump({}, fp)
    config.load("concurrent")

    threads = [
        threading.Thread(target=config.write_config_fields, args=({f"field_{i}": i},))
        for i in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with open(path) as fp:
        data = json.load(fp)
    assert data == {f"field_{i}": i for i in range(20)}


def test_run_and_homing_safety_options_are_off_by_default():
    import config

    assert config.conf["power_speed_limit"] == 0
    assert config.conf["homing_reset_offset"] is False
    assert "power_speed_limit" in config.userconfigurable
    assert "homing_reset_offset" in config.userconfigurable


### load(): invalid / odd config files


def _write(config, name, text):
    path = os.path.join(config.conf["confdir"], name)
    with open(path, "w") as fp:
        fp.write(text)
    return path


def test_load_invalid_json_keeps_defaults_and_reports(isolated_config, capsys):
    config = isolated_config
    _write(config, "config.broken.json", "{not json")
    before = dict(config.conf)
    config.load("broken")
    assert config.conf == before
    assert "failed to read config file" in capsys.readouterr().out


def test_load_unknown_named_config_exits(isolated_config, capsys):
    with pytest.raises(SystemExit):
        isolated_config.load("nope")
    assert "invalid config specified" in capsys.readouterr().out


def test_load_sets_configpath(isolated_config):
    config = isolated_config
    config.load("")
    assert config.configpath == os.path.join(config.conf["confdir"], "config.json")


def test_default_config_file_round_trips_through_load(isolated_config):
    config = isolated_config
    config.load("")  # creates config.json with every userconfigurable default
    config.conf["feedrate"] = 1  # perturb, then reload from the file just written
    config.load("")
    assert config.conf["feedrate"] == config.conf_defaults["feedrate"]


def test_load_merges_partial_file_over_existing_values(isolated_config):
    config = isolated_config
    _write(config, "config.p.json", json.dumps({"seekrate": 111}))
    config.load("p")
    assert config.conf["seekrate"] == 111
    assert config.conf["feedrate"] == config.conf_defaults["feedrate"]


def test_load_does_not_validate_value_types(isolated_config):
    # documents current behaviour: values are copied verbatim, no coercion
    config = isolated_config
    _write(config, "config.t.json", json.dumps({"feedrate": "fast", "workspace": None}))
    config.load("t")
    assert config.conf["feedrate"] == "fast" and config.conf["workspace"] is None


@pytest.mark.parametrize("secret", ["users", "serial_port", "firmware", "mill_mode", "confdir"])
def test_load_never_applies_restricted_keys(isolated_config, secret):
    config = isolated_config
    before = copy.deepcopy(config.conf[secret])
    _write(config, "config.r.json", json.dumps({secret: "pwned"}))
    config.load("r")
    assert config.conf[secret] == before


def test_load_non_object_json_is_reported_not_crash(isolated_config, capsys):
    config = isolated_config
    _write(config, "config.num.json", "5")
    config.load("num")
    assert "failed to read config file" in capsys.readouterr().out


### write_config_fields


def test_write_config_fields_updates_memory_and_merges_file(isolated_config):
    config = isolated_config
    _write(config, "config.w.json", json.dumps({"grid_mm": 5, "seekrate": 9}))
    config.load("w")
    config.write_config_fields({"grid_mm": 50, "extra": [1]})
    assert config.conf["grid_mm"] == 50 and config.conf["extra"] == [1]
    with open(config.configpath) as fp:
        assert json.load(fp) == {"grid_mm": 50, "seekrate": 9, "extra": [1]}


def test_write_config_fields_creates_missing_file(isolated_config):
    config = isolated_config
    config.configpath = os.path.join(config.conf["confdir"], "config.new.json")
    config.write_config_fields({"feedrate": 3})
    with open(config.configpath) as fp:
        assert json.load(fp) == {"feedrate": 3}


def test_write_config_fields_leaves_no_temp_files(isolated_config):
    config = isolated_config
    config.configpath = os.path.join(config.conf["confdir"], "config.clean.json")
    config.write_config_fields({"a": 1})
    assert os.listdir(config.conf["confdir"]) == ["config.clean.json"]


def test_write_config_fields_corrupt_existing_file_raises(isolated_config):
    config = isolated_config
    config.configpath = _write(config, "config.c.json", "{oops")
    with pytest.raises(ValueError):
        config.write_config_fields({"a": 1})
    with open(config.configpath) as fp:
        assert fp.read() == "{oops"  # untouched


### write_json_atomic


def test_write_json_atomic_replaces_file_and_passes_kwargs(tmp_path):
    import config

    target = tmp_path / "x.json"
    target.write_text("old")
    config.write_json_atomic(str(target), {"a": 1}, indent=2)
    assert target.read_text() == '{\n  "a": 1\n}'
    assert os.listdir(tmp_path) == ["x.json"]


def test_write_json_atomic_failure_keeps_original_and_cleans_temp(tmp_path):
    import config

    target = tmp_path / "x.json"
    target.write_text('{"keep": true}')
    with pytest.raises(TypeError):
        config.write_json_atomic(str(target), {"bad": object()})
    assert target.read_text() == '{"keep": true}'
    assert os.listdir(tmp_path) == ["x.json"]


### list_configs


def test_list_configs_prints_named_configs_and_restores_cwd(isolated_config, capsys):
    config = isolated_config
    for n in ("config.a.json", "config.b.json", "config.json", "other.txt"):
        _write(config, n, "{}")
    cwd = os.getcwd()
    config.list_configs()
    out = capsys.readouterr().out
    assert os.getcwd() == cwd
    assert out.startswith("Config files in " + config.conf["confdir"])
    assert "a - (config.a.json)" in out and "b - (config.b.json)" in out
    assert "config.json)" not in out.replace("config.a.json)", "").replace("config.b.json)", "")


### import-time behaviour: execute a private copy of config.py


CONFIG_SRC = os.path.join(os.path.dirname(__file__), "..", "config.py")


@pytest.fixture
def fresh_config(monkeypatch, tmp_path):
    """Exec config.py as a throwaway module with HOME, cwd and mkdtemp sandboxed.

    Never touches sys.modules['config'], so the shared module is unaffected.
    The returned callable accepts sys.platform / hasattr(sys,'_MEIPASS') overrides.
    """
    home = tmp_path / "home"
    home.mkdir()
    stor = tmp_path / "stor"
    stor.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: str(stor))

    def make(platform=None, meipass=None, rpi=False, euid=1000, machine="x86_64"):
        if platform:
            monkeypatch.setattr(sys, "platform", platform)
        if meipass:
            monkeypatch.setattr(sys, "_MEIPASS", meipass, raising=False)
        if rpi:
            gpio = types.SimpleNamespace(
                BCM=11, IN=1, OUT=0, HIGH=1, calls=[],
                setmode=lambda m: None, setup=lambda *a: gpio.calls.append(("setup", a)),
                input=lambda p: 1, output=lambda *a: gpio.calls.append(("output", a)),
            )  # fmt: skip
            monkeypatch.setitem(sys.modules, "RPi", types.ModuleType("RPi"))
            monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
            monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
        monkeypatch.setattr(os, "geteuid", lambda: euid)
        monkeypatch.setattr(os, "uname", lambda: ("Linux", "h", "r", "v", machine))
        spec = importlib.util.spec_from_file_location("config_private", CONFIG_SRC)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    make.home, make.stor, make.tmp = home, stor, tmp_path
    return make


def test_import_linux_defaults(fresh_config):
    mod = fresh_config(platform="linux")
    assert mod.conf["hardware"] == "standard"
    assert mod.conf["firmware"] == "driveboardusb"
    assert mod.conf["confdir"] == str(fresh_config.home / ".driveboardapp")
    assert os.path.isdir(mod.conf["confdir"])
    assert mod.conf["stordir"] == str(fresh_config.stor)
    assert mod.conf["rootdir"] == os.path.abspath(os.path.join(os.path.dirname(CONFIG_SRC), ".."))
    assert mod.conf == {**mod.conf_defaults, **{k: mod.conf[k] for k in
        ("rootdir", "confdir", "stordir", "hardware", "firmware")}}  # fmt: skip


def test_import_defaults_snapshot_is_independent_copy(fresh_config):
    mod = fresh_config(platform="linux")
    mod.conf["users"]["x"] = "y"
    mod.conf["workspace"][0] = 1
    assert mod.conf_defaults["users"] == {"laser": "laser"}
    assert mod.conf_defaults["workspace"] == [1220, 610, 0]


def test_import_macos_confdir(fresh_config):
    mod = fresh_config(platform="darwin")
    expected = (
        fresh_config.home / "Library" / "Application Support" / "com.nortd.labs" / "driveboardapp"
    )
    assert mod.conf["confdir"] == str(expected) and expected.is_dir()
    assert mod.conf["hardware"] == "standard"  # RPi/BBB detection is linux only


def test_import_windows_confdir_uses_appdata(fresh_config):
    mod = fresh_config(platform="win32")
    # os.path is posixpath here, so expandvars('%APPDATA%') is left literal
    assert mod.conf["confdir"].endswith(
        os.path.join("%APPDATA%", "com.nortd.labs", "driveboardapp")
    )
    assert os.path.isdir(mod.conf["confdir"])
    assert mod.conf["hardware"] == "standard"


def test_import_pyinstaller_rootdir(fresh_config):
    mod = fresh_config(platform="linux", meipass="/bundle")
    assert mod.conf["rootdir"] == "/bundle"


def test_import_raspberrypi_defaults_and_root_port(fresh_config):
    mod = fresh_config(platform="linux", rpi=True, euid=1000)
    assert mod.conf["hardware"] == "raspberrypi"
    assert mod.conf["firmware"] == "driveboard1403"
    assert mod.conf["serial_port"] == "/dev/ttyAMA0"
    assert mod.conf["network_port"] == 4444
    gpio = sys.modules["RPi.GPIO"]
    assert ("output", (2, 1)) in gpio.calls  # atmega reset pin driven high
    assert mod.isSMC11 == 1
    assert fresh_config(platform="linux", rpi=True, euid=0).conf["network_port"] == 80
