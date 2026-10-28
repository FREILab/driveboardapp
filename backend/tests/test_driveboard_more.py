"""Additional driveboard.py coverage: firmware status parsing, connection
management, job validation/conversion corners, raster ordering, mill jobs.

Complements test_driveboard_safety.py and reuses its approach: the real
SerialLoopClass (never thread-started, see the ``loop`` fixture) plus fakes.

Deliberately not covered: a real serial port, and the wall-clock part of the
serial thread (``run`` is driven here through a fake clock instead).
"""

import base64
import contextlib
import io
import subprocess
import sys
import types

import driveboard
import pytest
import serial
from config import conf
from helpers import FakeSerialDevice
from PIL import Image
from test_driveboard_safety import (
    _dots_png,
    _path_job,
    decode_param,
    encode_param,
    feed,
)

# ---------------------------------------------------------------------------
# Firmware status parser: rarely-seen flags and malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker,key",
    [
        (driveboard.ERROR_RX_BUFFER_OVERFLOW, "buffer"),
        (driveboard.ERROR_INVALID_MARKER, "marker"),
        (driveboard.ERROR_INVALID_DATA, "data"),
        (driveboard.ERROR_INVALID_COMMAND, "command"),
        (driveboard.ERROR_INVALID_PARAMETER, "parameter"),
        (driveboard.ERROR_TRANSMISSION_ERROR, "transmission"),
        (driveboard.ERROR_SERIAL_WATCHDOG, "watchdog"),
    ],
)
def test_rx_firmware_errors_set_stop_and_purge_the_job(loop, marker, key, capsys):
    loop.tx_buffer = bytearray(b"B" + bytes(5))
    loop.tx_pos = 3
    loop.job_size = 6
    loop.job_active = True
    loop._paused = True
    feed(loop, marker.encode())
    assert loop._s["stops"][key] is True
    # stop housekeeping: nothing queued survives, machine reported ready-in-stop
    assert loop.tx_buffer == bytearray() and loop.tx_pos == 0 and loop.job_size == 0
    assert loop.job_active is False and loop._paused is False
    assert loop._s["ready"] is True
    # a real fault dumps the recent stream for diagnosis
    assert "RECENT TX BUFFER" in capsys.readouterr().out


def test_rx_unknown_stop_marker_is_flagged_but_still_stops(loop, capsys):
    loop.job_active = True
    feed(loop, b"@")  # 64: in the stop-marker range, but not a defined one
    out = capsys.readouterr().out
    assert "invalid stop error marker" in out
    assert loop.job_active is False and loop._s["ready"] is True


def test_rx_recent_tx_dump_names_markers_data_and_garbage(loop, capsys):
    loop.tx_buffer = bytearray([ord(driveboard.CMD_LINE), 200, 0])
    loop.tx_pos = 3
    feed(loop, driveboard.ERROR_INVALID_COMMAND.encode())
    out = capsys.readouterr().out
    assert "CMD_LINE" in out and "(data byte)" in out and "(invalid)" in out


def test_rx_stop_request_and_limits_skip_the_tx_dump(loop, capsys):
    loop.tx_buffer = bytearray(b"BBB")
    loop.tx_pos = 3
    feed(loop, driveboard.ERROR_LIMIT_HIT_X1.encode())
    assert "RECENT TX BUFFER" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "marker,value,getter",
    [
        (driveboard.INFO_POS_Z, 3.5, lambda s: s["pos"][2]),
        (driveboard.INFO_OFFSET_Z, -2.25, lambda s: s["offset"][2]),
        (driveboard.INFO_BUFFER_UNDERRUN, 7, lambda s: s["underruns"]),
        (driveboard.INFO_DURATION, 1.5, lambda s: s["duration"]),
        (driveboard.INFO_PIXEL_WIDTH, 0.2, lambda s: s["pixelwidth"]),
    ],
)
def test_rx_remaining_params_decode(loop, marker, value, getter):
    feed(loop, encode_param(marker, value))
    assert getter(loop._s) == pytest.approx(value, abs=1e-3)


def test_rx_debug_param_is_consumed_without_side_effects(loop):
    before = repr(loop._s)
    feed(loop, encode_param(driveboard.INFO_DEBUG, 12.0))
    assert repr(loop._s) == before
    assert loop.pdata_count == 0


def test_rx_unknown_param_resets_the_decoder(loop, capsys):
    feed(loop, encode_param("m", 1.0))  # lowercase, but not a reported param
    assert "invalid param" in capsys.readouterr().out
    assert loop.pdata_count == 0 and loop.pdata_nums == [128, 128, 128, 192]


def test_rx_unknown_info_flag_is_reported(loop, capsys):
    feed(loop, b"Z")
    assert "invalid info flag" in capsys.readouterr().out
    assert loop.pdata_count == 0


def test_rx_too_many_data_bytes_is_reported(loop, capsys):
    feed(loop, bytes([200] * 5))
    assert "invalid data" in capsys.readouterr().out


def test_rx_invalid_marker_resets_the_decoder(loop, capsys):
    loop.pdata_count = 2
    feed(loop, b"\x7f")  # 127 is no marker at all
    assert "invalid marker" in capsys.readouterr().out
    assert loop.pdata_count == 0


def test_rx_chunk_processed_underflow_is_flagged(loop, capsys):
    loop.firmbuf_used = 0
    feed(loop, driveboard.CMD_CHUNK_PROCESSED.encode())
    assert loop.firmbuf_used == -loop.TX_CHUNK_SIZE
    assert "firmware buffer tracking too low" in capsys.readouterr().out


def test_rx_idle_flag_with_queued_tx_does_not_claim_ready(loop):
    loop._s["ready"] = False
    loop.tx_buffer = bytearray(b"B")
    feed(loop, driveboard.INFO_IDLE_YES.encode())
    assert loop._s["ready"] is False
    assert loop.last_firmware_idle > 0


def test_serial_read_prints_traffic_when_enabled(loop, monkeypatch, capsys):
    monkeypatch.setitem(conf, "print_serial_data", True)
    feed(loop, driveboard.INFO_DOOR_OPEN.encode())
    assert "INFO_DOOR_OPEN" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Transmit side corners
# ---------------------------------------------------------------------------


class ShortWriteDevice(FakeSerialDevice):
    def write(self, data):
        super().write(data)
        return len(data) - 1  # the OS took less than asked


class TimeoutDevice(FakeSerialDevice):
    def write(self, data):
        raise serial.SerialTimeoutException


def _queue(loop, n=40):
    loop.tx_buffer = bytearray([ord(driveboard.CMD_NONE)] * n)
    loop.job_size = n
    loop.firmbuf_used = 0


def test_short_write_does_not_advance_the_stream(loop, capsys):
    _queue(loop)
    loop.device = ShortWriteDevice()
    loop._serial_write()
    assert loop.tx_pos == 0 and loop.firmbuf_used == 0
    assert "write did not complete" in capsys.readouterr().out


def test_write_timeout_is_retried_not_skipped(loop, capsys):
    _queue(loop)
    loop.device = TimeoutDevice()
    loop._serial_write()
    assert loop.tx_pos == 0
    assert "writeTimeoutError 2" in capsys.readouterr().out


def test_send_char_timeout_is_swallowed(loop, capsys):
    loop.device = TimeoutDevice()
    loop._send_char(driveboard.CMD_STATUS)
    assert "writeTimeoutError 1" in capsys.readouterr().out


def test_send_char_sends_every_byte_twice(loop, monkeypatch, capsys):
    monkeypatch.setitem(conf, "print_serial_data", True)
    loop.device = FakeSerialDevice()
    loop._send_char(driveboard.CMD_STATUS)
    assert bytes(loop.device.written) == bytes([3, 3])
    assert "CMD_STATUS" in capsys.readouterr().out


def test_serial_write_prints_traffic_when_enabled(loop, monkeypatch, capsys):
    monkeypatch.setitem(conf, "print_serial_data", True)
    _queue(loop, 4)
    loop.device = FakeSerialDevice()
    loop._serial_write()
    assert "Sending" in capsys.readouterr().out
    assert loop.tx_pos == 4


# ---------------------------------------------------------------------------
# The serial thread body, driven with a fake clock (no real waiting)
# ---------------------------------------------------------------------------


class FakeClock:
    """Stand-in for the time module inside driveboard: the first sleep ends
    the loop, and time jumps so the periodic status request is due."""

    def __init__(self, loop):
        self.loop = loop
        self.now = 1000.0

    def time(self):
        self.now += 1.0
        return self.now

    def sleep(self, _secs):
        self.loop.stop_processing = True


@pytest.fixture
def clocked(loop, monkeypatch):
    calls = []
    monkeypatch.setattr(driveboard, "time", FakeClock(loop))
    monkeypatch.setattr(driveboard, "disable_computer_sleep", lambda: calls.append("off"))
    monkeypatch.setattr(driveboard, "enable_computer_sleep", lambda: calls.append("on"))
    loop.calls = calls
    return loop


def test_run_without_a_device_marks_serial_down(clocked):
    clocked.device = None
    clocked.run()
    assert clocked._status["serial"] is False and clocked._status["ready"] is False
    assert clocked.stop_processing is True
    assert clocked.calls == ["off", "on"]  # sleep inhibited only while running


@pytest.mark.parametrize("exc", [OSError, ValueError, RuntimeError])
def test_run_survives_a_failing_device(clocked, exc, capsys):
    class Broken(FakeSerialDevice):
        def read(self, n):
            raise exc("boom")

    clocked.device = Broken()
    clocked.run()
    assert clocked._status["serial"] is False and clocked._status["ready"] is False
    assert "ERROR" in capsys.readouterr().out


def test_run_requests_superstatus_when_idle(clocked):
    clocked.device = FakeSerialDevice()
    clocked._status["ready"] = True
    clocked.run()
    assert clocked.request_status == 2


def test_run_requests_plain_status_while_busy(clocked):
    clocked.device = FakeSerialDevice()
    clocked._status["ready"] = False
    clocked.run()
    assert clocked.request_status == 1


# ---------------------------------------------------------------------------
# prettify_serial: the debug view of the wire format
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_prettify():
    for name in ("tx_pdata_nums", "rx_pdata_nums"):
        if hasattr(driveboard.prettify_serial, name):
            delattr(driveboard.prettify_serial, name)


def test_prettify_tx_command_and_param(fresh_prettify):
    wire = bytes([ord(driveboard.CMD_LINE)]) + _encode_tx(driveboard.PARAM_TARGET_X, 12.5)
    text = driveboard.prettify_serial(wire)
    assert "CMD_LINE" in text and "PARAM_TARGET_X" in text and "(12.5)" in text


def test_prettify_accepts_a_bare_int(fresh_prettify):
    assert driveboard.prettify_serial(ord(driveboard.CMD_STATUS)) == "CMD_STATUS"


def test_prettify_rx_param_value(fresh_prettify):
    wire = encode_param(driveboard.INFO_POS_X, -3.25)
    text = driveboard.prettify_serial(wire, markers=driveboard.markers_rx)
    assert "INFO_POS_X" in text and "(-3.25)" in text


def test_prettify_raster_stream_counts_pixels(fresh_prettify):
    wire = bytes(
        [ord(driveboard.CMD_RASTER_DATA_START), 130, 131, 132, ord(driveboard.CMD_RASTER_DATA_END)]
    )
    text = driveboard.prettify_serial(wire)
    assert "CMD_RASTER_DATA_START" in text and "(3)" in text and "CMD_RASTER_DATA_END" in text


def _encode_tx(marker, val):
    # firmware and host share the 28-bit encoding
    return encode_param(marker, val)


# ---------------------------------------------------------------------------
# Controller discovery and connection
# ---------------------------------------------------------------------------


class FakePort:
    """serial.Serial replacement: context manager, scripted hello."""

    hello_ports = ()
    fail_ports = ()
    opened = []

    def __init__(self, port=None, baudrate=None, timeout=None, writeTimeout=None):
        if port in self.fail_ports:
            raise serial.SerialException("busy")
        self.port = port
        self.opened.append(port)
        self.dtr = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return b"~" if self.port in self.hello_ports else b"\x00"

    def setDTR(self, v):
        self.dtr.append(v)

    def flushInput(self):
        pass

    def flushOutput(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def ports(monkeypatch):
    FakePort.hello_ports = ()
    FakePort.fail_ports = ()
    FakePort.opened = []
    listing = []
    monkeypatch.setattr(driveboard.serial, "Serial", FakePort)
    monkeypatch.setattr(driveboard.serial.tools.list_ports, "comports", lambda: listing)
    return listing


def test_find_controller_prefers_an_arduino_with_firmware(ports):
    ports[:] = [("/dev/a", "other", ""), ("/dev/b", "Arduino Uno", "")]
    FakePort.hello_ports = ("/dev/b",)
    assert driveboard.find_controller(verbose=False) == "/dev/b"
    assert FakePort.opened == ["/dev/b"]  # the plain port was never probed


def test_find_controller_falls_back_to_any_port_saying_hello(ports):
    ports[:] = [("/dev/a", "usb serial", ""), ("/dev/b", "usb serial 2", "")]
    FakePort.hello_ports = ("/dev/b",)
    assert driveboard.find_controller(verbose=False) == "/dev/b"


def test_find_controller_skips_ports_that_cannot_be_opened(ports):
    ports[:] = [("/dev/a", "Arduino", ""), ("/dev/b", "usb", "")]
    FakePort.fail_ports = ("/dev/a",)
    FakePort.hello_ports = ("/dev/b",)
    assert driveboard.find_controller(verbose=False) == "/dev/b"


def test_find_controller_arduino_without_firmware_is_still_returned(ports):
    ports[:] = [("/dev/a", "Arduino Mega", "")]
    assert driveboard.find_controller(verbose=False) == "/dev/a"


def test_find_controller_none_found(ports, capsys):
    ports[:] = [("/dev/a", "modem", "")]
    assert driveboard.find_controller() is None
    assert "No controller found" in capsys.readouterr().out
    assert driveboard.find_controller(verbose=False) is None


class NoClock:
    """time replacement: sleeps return instantly, the clock advances 1 s a call."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        self.now += 1.0
        return self.now

    def sleep(self, _s):
        pass


@pytest.fixture
def connectable(ports, monkeypatch):
    """No SerialLoop yet, a hello-sending port, and no real thread start."""
    monkeypatch.setattr(driveboard, "SerialLoop", None)
    monkeypatch.setattr(driveboard, "time", NoClock())
    started = []
    monkeypatch.setattr(driveboard.SerialLoopClass, "start", lambda self: started.append(self))
    monkeypatch.setitem(conf, "usb_reset_hack", False)
    monkeypatch.setitem(conf, "hardware", "standard")
    FakePort.hello_ports = ("/dev/ok",)
    yield started
    driveboard.SerialLoop = None


def test_connect_resets_the_board_and_starts_the_loop(connectable, capsys):
    driveboard.connect(port="/dev/ok", baudrate=57600)
    sl = driveboard.SerialLoop
    assert sl is not None and connectable == [sl]
    assert sl.device.dtr == [False, True]  # DTR toggle resets the Arduino
    assert "Controller says Hello" in capsys.readouterr().out


def test_connect_uses_config_when_not_given_a_port(connectable, monkeypatch):
    monkeypatch.setitem(conf, "serial_port", "/dev/ok")
    driveboard.connect(verbose=False)
    assert driveboard.SerialLoop.device.port == "/dev/ok"


def test_connect_without_hello_gives_up_and_closes_the_port(connectable, capsys):
    holder = []
    orig_init = FakePort.__init__

    def spy(self, *a, **k):
        orig_init(self, *a, **k)
        holder.append(self)

    FakePort.__init__ = spy
    try:
        driveboard.connect(port="/dev/silent")
    finally:
        FakePort.__init__ = orig_init
    assert driveboard.SerialLoop is None
    assert holder[0].closed is True  # no leaked OS handle
    assert connectable == []
    out = capsys.readouterr().out
    assert "Cannot get 'hello'" in out and "Cannot connect serial" in out


def test_connect_open_failure_leaves_no_loop(connectable):
    FakePort.fail_ports = ("/dev/bad",)
    driveboard.connect(port="/dev/bad", verbose=False)
    assert driveboard.SerialLoop is None


def test_connect_refuses_when_already_connected(loop, capsys):
    before = driveboard.SerialLoop
    driveboard.connect(port="/dev/ok")
    assert driveboard.SerialLoop is before
    assert "disconnect first" in capsys.readouterr().out


def test_connect_non_standard_hardware_resets_via_flash_module(connectable, monkeypatch):
    calls = []
    fake_flash = types.SimpleNamespace(
        reset_atmega=lambda: calls.append("reset"), usb_reset_hack=lambda: calls.append("hack")
    )
    monkeypatch.setitem(sys.modules, "flash", fake_flash)
    monkeypatch.setitem(conf, "hardware", "raspberrypi")
    monkeypatch.setitem(conf, "usb_reset_hack", True)
    driveboard.connect(port="/dev/ok", verbose=False)
    assert calls == ["hack", "reset"]
    assert driveboard.SerialLoop is not None


@pytest.fixture
def scripted_connect(monkeypatch):
    """connect()/connected() replaced by a script of outcomes."""
    state = {"connected": False, "tries": [], "outcomes": []}

    def fake_connect(port=None, baudrate=None, verbose=True):
        state["tries"].append(port)
        state["connected"] = state["outcomes"].pop(0)

    monkeypatch.setattr(driveboard, "connect", fake_connect)
    monkeypatch.setattr(driveboard, "connected", lambda: state["connected"])
    saved = []
    monkeypatch.setattr(driveboard, "write_config_fields", saved.append)
    state["saved"] = saved
    return state


def test_connect_withfind_uses_configured_port_first(scripted_connect, monkeypatch):
    scripted_connect["outcomes"] = [True]
    monkeypatch.setattr(driveboard, "find_controller", lambda **k: pytest.fail("no search"))
    monkeypatch.setitem(conf, "serial_port", "/dev/conf")
    driveboard.connect_withfind(verbose=False)
    assert scripted_connect["tries"] == ["/dev/conf"]


def test_connect_withfind_discovers_and_remembers_the_port(scripted_connect, monkeypatch, capsys):
    scripted_connect["outcomes"] = [False, True]
    monkeypatch.setattr(driveboard, "find_controller", lambda **k: "/dev/found")
    monkeypatch.setitem(conf, "serial_port", "/dev/conf")
    driveboard.connect_withfind()
    assert scripted_connect["tries"] == ["/dev/conf", "/dev/found"]
    assert conf["serial_port"] == "/dev/found"
    assert scripted_connect["saved"] == [{"serial_port": "/dev/found"}]
    assert "Hardware found at /dev/found" in capsys.readouterr().out


def test_connect_withfind_gives_help_when_nothing_found(scripted_connect, monkeypatch, capsys):
    scripted_connect["outcomes"] = [False]
    monkeypatch.setattr(driveboard, "find_controller", lambda **k: None)
    driveboard.connect_withfind()
    assert "How to configure" in capsys.readouterr().out
    assert scripted_connect["saved"] == []


@pytest.mark.parametrize("answer,flashed", [("y", True), ("n", False)])
def test_connect_withfind_offers_to_flash_missing_firmware(
    scripted_connect, monkeypatch, answer, flashed
):
    scripted_connect["outcomes"] = [False, False] + ([True] if flashed else [])
    monkeypatch.setattr(driveboard, "find_controller", lambda **k: "/dev/ard")
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    calls = []
    monkeypatch.setattr(driveboard, "flash", lambda serial_port: calls.append(serial_port) or 0)
    driveboard.connect_withfind(verbose=False)
    assert (calls == ["/dev/ard"]) is flashed


# ---------------------------------------------------------------------------
# flash / build / reset: delegate to helper modules, releasing the port first
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_flash(monkeypatch):
    log = []
    mod = types.SimpleNamespace(
        flash_upload=lambda serial_port, firmware: (
            log.append(("upload", serial_port, firmware)) or mod.ret
        ),
        reset_atmega=lambda: log.append("reset"),
        ret=0,
    )
    monkeypatch.setitem(sys.modules, "flash", mod)
    mod.log = log
    return mod


def test_flash_without_a_port_refuses(fake_flash, monkeypatch, capsys):
    monkeypatch.setitem(conf, "serial_port", "")
    assert driveboard.flash() == 1
    assert fake_flash.log == []
    assert "no serial port" in capsys.readouterr().out


def test_flash_closes_the_port_then_reconnects(fake_flash, monkeypatch):
    events = []
    monkeypatch.setattr(driveboard, "connected", lambda: True)
    monkeypatch.setattr(driveboard, "close", lambda: events.append("close"))
    monkeypatch.setattr(driveboard, "connect", lambda port: events.append(("connect", port)))
    assert driveboard.flash(serial_port="/dev/x", firmware="fw.hex") == 0
    assert events == ["close", ("connect", "/dev/x")]
    assert fake_flash.log == [("upload", "/dev/x", "fw.hex")]


def test_flash_failure_is_reported_and_returned(fake_flash, monkeypatch, capsys):
    fake_flash.ret = 3
    monkeypatch.setattr(driveboard, "connected", lambda: False)
    monkeypatch.setitem(conf, "firmware", "conf.hex")
    assert driveboard.flash(serial_port="/dev/x") == 3
    assert fake_flash.log == [("upload", "/dev/x", "conf.hex")]
    assert "flash failed" in capsys.readouterr().out


def test_reset_closes_and_reconnects_a_live_port(fake_flash, monkeypatch):
    events = []
    monkeypatch.setattr(driveboard, "connected", lambda: True)
    monkeypatch.setattr(driveboard, "close", lambda: events.append("close"))
    monkeypatch.setattr(driveboard, "connect", lambda port: events.append(("connect", port)))
    monkeypatch.setitem(conf, "serial_port", "/dev/y")
    driveboard.reset()
    assert events == ["close", ("connect", "/dev/y")]
    assert fake_flash.log == ["reset"]


def test_reset_with_no_connection_only_resets(fake_flash, monkeypatch):
    monkeypatch.setattr(driveboard, "connected", lambda: False)
    monkeypatch.setattr(driveboard, "connect", lambda **k: pytest.fail("no reconnect"))
    driveboard.reset()
    assert fake_flash.log == ["reset"]


@pytest.mark.parametrize("ret", [0, 2])
def test_build_returns_the_build_status(monkeypatch, ret, capsys):
    monkeypatch.setitem(sys.modules, "build", types.SimpleNamespace(build_all=lambda: ret))
    assert driveboard.build() == ret
    assert ("build_all failed" in capsys.readouterr().out) is bool(ret)


# ---------------------------------------------------------------------------
# Keeping the computer awake during a job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn,verb,msg",
    [
        (driveboard.disable_computer_sleep, "mask", "disable system sleep"),
        (driveboard.enable_computer_sleep, "unmask", "re-enable system sleep"),
    ],
)
class TestSleepInhibit:
    def _run(self, monkeypatch, fn, result):
        monkeypatch.setattr(driveboard.platform, "system", lambda: "Linux")
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            if isinstance(result, Exception):
                raise result
            return types.SimpleNamespace(returncode=result)

        monkeypatch.setattr(subprocess, "run", fake_run)
        fn()
        return calls

    def test_linux_masks_the_sleep_targets(self, monkeypatch, fn, verb, msg, capsys):
        calls = self._run(monkeypatch, fn, 0)
        assert calls[0][:2] == ["systemctl", verb] and "sleep.target" in calls[0]
        assert capsys.readouterr().out == ""

    def test_linux_without_privilege_carries_on(self, monkeypatch, fn, verb, msg, capsys):
        self._run(monkeypatch, fn, 1)
        assert msg in capsys.readouterr().out

    def test_linux_without_systemctl_carries_on(self, monkeypatch, fn, verb, msg, capsys):
        self._run(monkeypatch, fn, FileNotFoundError())
        assert msg in capsys.readouterr().out

    def test_other_os_just_says_so(self, monkeypatch, fn, verb, msg, capsys):
        monkeypatch.setattr(driveboard.platform, "system", lambda: "Darwin")
        fn()
        assert "not implemented in Darwin" in capsys.readouterr().out

    def test_windows_uses_the_execution_state_flag(self, monkeypatch, fn, verb, msg):
        flags = []
        kernel = types.SimpleNamespace(SetThreadExecutionState=flags.append)
        fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=kernel))
        monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
        monkeypatch.setattr(driveboard.platform, "system", lambda: "Windows")
        fn()
        assert flags == [0x80000001 if verb == "mask" else 0x80000000]


# ---------------------------------------------------------------------------
# Floyd-Steinberg dithering
# ---------------------------------------------------------------------------


def test_dither_two_levels_only_yields_black_or_white():
    out = driveboard.raster_dither(4, 4, [100, 150, 200, 50] * 4, 2)
    assert set(out) <= {0.0, 255.0}


def test_dither_preserves_pure_levels():
    assert driveboard.raster_dither(3, 1, [0, 255, 0], 2) == [0, 255, 0]


def test_dither_does_not_modify_its_input():
    src = [128] * 6
    driveboard.raster_dither(3, 2, src, 2)
    assert src == [128] * 6


def test_dither_midgray_keeps_roughly_half_the_ink():
    out = driveboard.raster_dither(16, 16, [128] * 256, 2)
    assert 0.4 < out.count(255.0) / 256 < 0.6


def test_dither_four_levels_lands_on_the_level_grid():
    out = driveboard.raster_dither(8, 8, list(range(0, 256, 4)), 4)
    assert set(out) <= {0.0, 85.0, 170.0, 255.0}


def test_dither_clamps_accumulated_error():
    # error diffused from neighbours must never push a value out of range
    out = driveboard.raster_dither(2, 2, [255, 255, 255, 0], 2)
    assert set(out) <= {0.0, 255.0}


# ---------------------------------------------------------------------------
# Helpers for reading back what a job put on the wire
# ---------------------------------------------------------------------------


def _events(buf):
    """tx_buffer -> [("cmd", char) | ("param", char, value)] (raster pixels skipped)."""
    out = []
    i = 0
    buf = bytes(buf)
    while i < len(buf):
        b = buf[i]
        if b == ord(driveboard.CMD_RASTER_DATA_START):
            i = buf.index(ord(driveboard.CMD_RASTER_DATA_END), i) + 1
            out.append(("cmd", driveboard.CMD_RASTER_DATA_START))
        elif b >= 128:
            p, v = decode_param(buf, i)
            out.append(("param", p, round(v, 3)))
            i += 5
        else:
            out.append(("cmd", chr(b)))
            i += 1
    return out


def _count(buf, cmd):
    return sum(1 for e in _events(buf) if e == ("cmd", cmd))


def _param_values(buf, param):
    return [e[2] for e in _events(buf) if e[0] == "param" and e[1] == param]


def _job(*polylines, kind="path", **pass_kw):
    pass_ = {"items": [0]}
    pass_.update(pass_kw)
    return {
        "head": {},
        "passes": [pass_],
        "items": [{"def": 0}],
        "defs": [{"kind": kind, "data": [list(p) for p in polylines]}],
    }


def _white_png(w=40, h=3):
    return _dots_png([], w, h)


# ---------------------------------------------------------------------------
# Admission of a job: what is refused, and that nothing is queued when it is
# ---------------------------------------------------------------------------


def test_job_refused_while_another_operation_holds_the_machine(loop):
    import threading

    held, release = threading.Event(), threading.Event()

    def holder():
        with driveboard._operation_lock:
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(5)
    try:
        with pytest.raises(driveboard.MachineBusy, match="another machine operation"):
            driveboard.job(_job([[10.0, 10.0], [20.0, 20.0]]))
    finally:
        release.set()
        t.join()
    assert loop.tx_buffer == bytearray()
    # the admission lock was handed back, so a later job is not locked out
    driveboard.job(_job([[10.0, 10.0], [20.0, 20.0]]))
    assert _count(loop.tx_buffer, driveboard.CMD_LINE) > 0


@pytest.mark.parametrize(
    "setup,match",
    [
        (lambda sl: setattr(sl, "discard_writes", True), "dispatch was stopped"),
        (lambda sl: setattr(sl, "request_stop", True), "dispatch was stopped"),
        (lambda sl: setattr(sl, "job_active", True), "not idle"),
        (lambda sl: sl._status.update(ready=False), "not idle"),
    ],
    ids=["discarding", "stop-requested", "job-active", "not-ready"],
)
def test_job_refused_unless_the_machine_is_idle_and_running(loop, setup, match):
    setup(loop)
    with pytest.raises(driveboard.MachineBusy, match=match):
        driveboard.job(_job([[10.0, 10.0], [20.0, 20.0]]))
    assert loop.tx_buffer == bytearray()


@pytest.mark.parametrize(
    "bad,expect",
    [
        ({"head": {"kind": "mill"}}, "invalid job"),
        ({"head": {}, "items": []}, "invalid job"),
        ({"head": {}, "defs": []}, "invalid job"),
        ({"head": {}, "defs": [], "items": []}, "no passes defined"),
    ],
    ids=["mill-no-defs", "laser-no-defs", "laser-no-items", "no-passes"],
)
def test_incomplete_jobs_are_ignored_with_a_message(loop, bad, expect, capsys):
    driveboard.job(bad)
    assert expect in capsys.readouterr().out
    assert loop.tx_buffer == bytearray() and loop.job_active is False


def test_incomplete_jobs_are_ignored_by_the_emitters_too(loop, capsys):
    driveboard.job_laser({"defs": []})
    driveboard.job_laser({"defs": [], "items": []})
    out = capsys.readouterr().out
    assert "invalid job" in out and "no passes defined" in out
    driveboard.job_mill({"head": {"kind": "laser"}})
    driveboard.job_mill({"head": {"kind": "mill"}})
    out = capsys.readouterr().out
    assert "not a mill job" in out and "invalid job" in out
    assert loop.tx_buffer == bytearray()


def test_job_stop_guard_is_a_noop_without_a_machine(monkeypatch):
    monkeypatch.setattr(driveboard, "SerialLoop", None)
    driveboard.job_stop_guard()


def test_writes_from_a_foreign_thread_are_refused_mid_dispatch(loop, monkeypatch):
    import threading

    monkeypatch.setattr(driveboard, "_dispatch_thread", threading.get_ident() + 1)
    with pytest.raises(driveboard.MachineBusy, match="being sent"):
        loop._guard_foreign_write()
    with pytest.raises(driveboard.MachineBusy):
        driveboard.air_on()


# ---------------------------------------------------------------------------
# Work-area validation corners
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relative", [False, True])
def test_validate_rejects_a_point_without_two_coordinates(loop, relative):
    loop._status["offset"] = [0.0, 0.0, 0.0]
    job = _job([[5.0]], relative=relative)
    with pytest.raises(ValueError, match="malformed point"):
        driveboard.job_laser_validate(job)


def test_validate_relative_motion_after_absolute_is_unsafe(loop):
    loop._status["offset"] = [0.0, 0.0, 0.0]
    job = {
        "passes": [{"items": [0]}, {"items": [1], "relative": True}],
        "items": [{"def": 0}, {"def": 0}],
        "defs": [{"kind": "path", "data": [[[10.0, 10.0], [20.0, 20.0]]]}],
    }
    with pytest.raises(ValueError, match="relative motion after absolute"):
        driveboard.job_laser_validate(job)


def test_validate_pads_a_short_reported_position(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 50.0])
    loop._status["offset"] = [0.0, 0.0, 0.0]
    loop._status["pos"] = [10.0, 10.0]  # no z reported
    ok = _job([[1.0, 1.0, 10.0]], relative=True)
    driveboard.job_laser_validate(ok)
    bad = _job([[1.0, 1.0, 60.0]], relative=True)
    with pytest.raises(ValueError, match="Z"):
        driveboard.job_laser_validate(bad)


def test_validate_reports_which_edge_was_missed(loop):
    loop._status["offset"] = [0.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="top left"):
        driveboard.job_laser_validate(_path_job([[-1.0, -1.0]]))
    with pytest.raises(ValueError, match="bottom right"):
        driveboard.job_laser_validate(
            _path_job([[conf["workspace"][0] + 1, conf["workspace"][1] + 1]])
        )


def test_validate_skips_an_image_that_burns_nothing(loop):
    # entirely white image far off the bed: nothing engraves, nothing to bound
    loop._status["offset"] = [0.0, 0.0, 0.0]
    job = {
        "passes": [{"items": [0]}],
        "items": [{"def": 0}],
        "defs": [
            {
                "kind": "image",
                "pos": [5000.0, 5000.0],
                "size": [20.0, 5.0],
                "data": _white_png(),
            }
        ],
    }
    driveboard.job_laser_validate(job)


def test_image_extent_of_a_sub_pixel_image_is_empty():
    def_ = {"kind": "image", "pos": [0, 0], "size": [0.1, 0.1], "data": _white_png()}
    assert driveboard._raster_engraved_box_mm(def_, {"pxsize": 1.0}) is None


def test_mill_validate_ignores_non_motion_items():
    job = {"defs": [{"data": [["F", 500], ["S", 1000], ["MIST", 1]]}]}
    driveboard.job_mill_validate(job)  # nothing to bound, must not raise


@pytest.mark.parametrize(
    "target,axis",
    [([-1.0, 0.0, 0.0], "x=-1.0"), ([0.0, -2.0, 0.0], "y=-2.0"), ([0.0, 0.0, -3.0], "z=-3.0")],
)
def test_mill_validate_rejects_negative_targets(target, axis):
    with pytest.raises(ValueError, match=axis):
        driveboard.job_mill_validate({"defs": [{"data": [["G0", target]]}]})


# ---------------------------------------------------------------------------
# Mill job conversion
# ---------------------------------------------------------------------------


def _run_mill(loop, items):
    loop._status["offset"] = [0.0, 0.0, 0.0]
    driveboard.job({"head": {"kind": "mill"}, "defs": [{"data": items}]})
    return _events(loop.tx_buffer)


def test_mill_job_primes_and_finalizes_the_machine(loop):
    ev = _run_mill(loop, [["G0", [10.0, 20.0, 0.0]]])
    assert ev[:4] == [
        ("cmd", driveboard.CMD_AIR_DISABLE),
        ("cmd", driveboard.CMD_AUX_DISABLE),
        ("cmd", driveboard.CMD_REF_ABSOLUTE),
        ("param", driveboard.PARAM_INTENSITY, 0.0),
    ]
    # finishes with spindle off and assists off, then z up before x/y home
    cmds = [e[1] for e in ev if e[0] == "cmd"]
    assert cmds.count(driveboard.CMD_AIR_DISABLE) == 2
    assert cmds.count(driveboard.CMD_AUX_DISABLE) == 2
    assert ("param", driveboard.PARAM_INTENSITY, 0.0) in ev[-20:]
    last_z = max(i for i, e in enumerate(ev) if e[:2] == ("param", driveboard.PARAM_TARGET_Z))
    last_x = max(i for i, e in enumerate(ev) if e[:2] == ("param", driveboard.PARAM_TARGET_X))
    assert last_z < last_x
    assert ev[last_x : last_x + 2] == [
        ("param", driveboard.PARAM_TARGET_X, 0.0),
        ("param", driveboard.PARAM_TARGET_Y, 0.0),
    ]


def test_mill_job_switches_between_seek_and_feed_rates(loop):
    ev = _run_mill(
        loop,
        [
            ["F", 750],
            ["G1", [10.0, 10.0, 0.0]],
            ["G1", [20.0, 10.0, 0.0]],
            ["G0", [0.0, 0.0, 0.0]],
        ],
    )
    feeds = [e[2] for e in ev if e[:2] == ("param", driveboard.PARAM_FEEDRATE)]
    # seekrate to start, the F word for G1 (once), seekrate again for G0, seek to finish
    assert feeds[:3] == [conf["seekrate"], 750.0, conf["seekrate"]]
    assert feeds.count(750.0) == 1


def test_mill_spindle_rpm_is_converted_to_percent_power(loop):
    half = conf["mill_max_rpm"] / 2
    ev = _run_mill(loop, [["S", half], ["G1", [5.0, 5.0, 0.0]]])
    # 50% of full scale is 127.5 raw on the wire
    assert 127.5 in [e[2] for e in ev if e[:2] == ("param", driveboard.PARAM_INTENSITY)]


def test_mill_coolant_switches_follow_the_job(loop):
    ev = _run_mill(
        loop,
        [
            ["MIST", True],
            ["FLOOD", True],
            ["G0", [1.0, 1.0, 0.0]],
            ["MIST", False],
            ["FLOOD", False],
        ],
    )
    cmds = [e[1] for e in ev if e[0] == "cmd"]
    # job order after the priming off/off: air on, aux on, ..., air off, aux off
    body = cmds[cmds.index(driveboard.CMD_AIR_ENABLE) :]
    assert body[0] == driveboard.CMD_AIR_ENABLE
    assert driveboard.CMD_AUX_ENABLE in body
    assert body.index(driveboard.CMD_AIR_DISABLE) > body.index(driveboard.CMD_LINE)
    assert body.index(driveboard.CMD_AUX_DISABLE) > body.index(driveboard.CMD_LINE)


# ---------------------------------------------------------------------------
# Path conversion: z, seek intensity, pixel size
# ---------------------------------------------------------------------------


def _run(loop, job):
    loop._status["offset"] = [0.0, 0.0, 0.0]
    driveboard.job(job)
    return loop.tx_buffer


def test_3d_path_moves_carry_z(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 50.0])
    buf = _run(loop, _job([[10.0, 10.0, 1.0], [20.0, 10.0, 2.0]], intensity=40.0))
    assert _param_values(buf, driveboard.PARAM_TARGET_Z)[:2] == [1.0, 2.0]
    # seek to the start, then burn to the end: two line moves plus the return home
    assert _count(buf, driveboard.CMD_LINE) >= 2


def test_3d_lone_vertex_seeks_with_z_and_never_burns(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 50.0])
    buf = _run(loop, _job([[10.0, 10.0, 3.0]], intensity=40.0, pierce_time=0.0))
    assert 3.0 in _param_values(buf, driveboard.PARAM_TARGET_Z)
    ev = _events(buf)
    first_move = next(i for i, e in enumerate(ev) if e == ("cmd", driveboard.CMD_LINE))
    # the beam is held off for the lone-vertex seek
    last_s = [e for e in ev[:first_move] if e[:2] == ("param", driveboard.PARAM_INTENSITY)][-1]
    assert last_s[2] == 0.0


def test_contiguous_3d_pieces_are_burned_as_one_cut(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 50.0])
    job = _job(
        [[0.0, 0.0, 1.0], [10.0, 0.0, 1.0]],
        [[10.0, 0.0, 1.0], [20.0, 0.0, 2.0]],
        kind="fill",
        intensity=30.0,
        seekrate=5000.0,
        feedrate=1500.0,
        pierce_time=0.0,
    )
    buf = _run(loop, job)
    feeds = _param_values(buf, driveboard.PARAM_FEEDRATE)
    # seek once to the first vertex; the joined second piece does not seek again
    assert feeds[:3] == [5000.0, 1500.0, 1500.0]
    assert 2.0 in _param_values(buf, driveboard.PARAM_TARGET_Z)
    # seek, burn to the joint, burn on: three moves before the return home
    assert _count(buf, driveboard.CMD_LINE) >= 3


@pytest.mark.parametrize("seekzero,seek_power", [(True, 0.0), (False, 50.0)])
def test_seekzero_decides_whether_the_beam_is_on_while_seeking(loop, seekzero, seek_power):
    buf = _run(
        loop,
        _job([[10.0, 10.0], [20.0, 10.0]], intensity=50.0, seekzero=seekzero, pierce_time=0.0),
    )
    ev = _events(buf)
    first_move = next(i for i, e in enumerate(ev) if e == ("cmd", driveboard.CMD_LINE))
    powers = [e[2] for e in ev[:first_move] if e[:2] == ("param", driveboard.PARAM_INTENSITY)]
    # wire value is 0..255 of the percentage; 50% -> 127.5
    assert powers[-1] == pytest.approx(0.0 if seek_power == 0.0 else 127.5)


def test_tiny_pxsize_is_raised_to_the_minimum_with_a_warning(loop, capsys):
    buf = _run(loop, _job([[10.0, 10.0], [20.0, 10.0]], pxsize=0.001))
    assert "pxsize of 0.001" in capsys.readouterr().out
    # x pixels are half the (clamped 0.01 mm) y pixel size
    assert _param_values(buf, driveboard.PARAM_PIXEL_WIDTH)[0] == 0.005


# ---------------------------------------------------------------------------
# Raster conversion
# ---------------------------------------------------------------------------


def _gradient_png(w=40, h=4):
    img = Image.new("L", (w, h))
    img.putdata([int(255 * (i % w) / (w - 1)) for i in range(w * h)])
    out = io.BytesIO()
    img.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def _image(data, pos=(100.0, 100.0), px=0.4, w=40, h=4):
    return {"kind": "image", "pos": list(pos), "size": [w * px / 2, h * px], "data": data}


def _engrave(loop, def_, px=0.4, head_pos=None, next_rect=None):
    return driveboard._job_laser_image(
        def_,
        {"air_assist": "off", "pxsize": px},
        px / 2,
        px,
        6000.0,
        2000.0,
        25.5,
        head_pos,
        next_rect,
    )


def _pixels(buf):
    """Pixel bytes of every raster data block in a tx_buffer."""
    buf = bytes(buf)
    out = []
    i = 0
    while True:
        try:
            i = buf.index(ord(driveboard.CMD_RASTER_DATA_START), i)
        except ValueError:
            return out
        end = buf.index(ord(driveboard.CMD_RASTER_DATA_END), i)
        out.extend(buf[i + 1 : end])
        i = end + 1


def test_raster_works_in_mill_mode_too(loop, monkeypatch):
    # PIL is imported unconditionally, mill mode must not disable rasters
    monkeypatch.setitem(conf, "mill_mode", True)
    _engrave(loop, _image(_dots_png([(5, 1), (6, 1)], 40, 4)))
    assert _count(loop.tx_buffer, driveboard.CMD_RASTER) >= 1
    assert len(_pixels(loop.tx_buffer)) >= 2


@pytest.mark.parametrize("mode", ["Bidirectional", "NearestNeighbor", "Forward", "Reverse"])
def test_all_white_image_never_moves_the_head(loop, mode, monkeypatch):
    monkeypatch.setitem(conf, "raster_mode", mode)
    head = [3.0, 4.0]
    last = _engrave(loop, _image(_white_png(40, 4)), head_pos=head)
    assert last == head  # nothing engraved, the head stayed put
    assert _count(loop.tx_buffer, driveboard.CMD_RASTER) == 0
    assert _count(loop.tx_buffer, driveboard.CMD_LINE) == 0


def test_two_level_raster_emits_only_full_or_zero_power(loop, monkeypatch):
    monkeypatch.setitem(conf, "raster_levels", 2)
    _engrave(loop, _image(_gradient_png()))
    pixels = set(_pixels(loop.tx_buffer))
    # wire byte is int((255 - value) / 2) + 128: black -> 255, white -> 128
    assert pixels == {128, 255}


def test_full_resolution_raster_keeps_the_gray_levels(loop, monkeypatch):
    monkeypatch.setitem(conf, "raster_levels", 128)
    _engrave(loop, _image(_gradient_png()))
    assert len(set(_pixels(loop.tx_buffer))) > 8


@pytest.mark.parametrize("levels,clamped", [(1000, 128), (1, 2)])
def test_raster_levels_out_of_range_are_clamped_with_a_warning(
    loop, monkeypatch, capsys, levels, clamped
):
    monkeypatch.setitem(conf, "raster_levels", levels)
    _engrave(loop, _image(_gradient_png()))
    assert f"set to {clamped}" in capsys.readouterr().out
    assert _count(loop.tx_buffer, driveboard.CMD_RASTER) >= 1


def test_unknown_raster_mode_falls_back_to_bidirectional(loop, monkeypatch, capsys):
    img = _image(_gradient_png())
    monkeypatch.setitem(conf, "raster_mode", "Bidirectional")
    _engrave(loop, img)
    expected = bytes(loop.tx_buffer)
    loop.tx_buffer = bytearray()
    monkeypatch.setitem(conf, "raster_mode", "Spiral")
    _engrave(loop, img)
    assert "raster_mode not recognized" in capsys.readouterr().out
    assert bytes(loop.tx_buffer) == expected


@pytest.mark.parametrize(
    "x,message",
    [(0.0, "not enough leadin space"), (None, "not enough leadout space")],
)
def test_raster_warns_when_there_is_no_room_for_the_lead_moves(loop, capsys, x, message):
    img = _image(_dots_png([(0, 1), (39, 1)], 40, 4))
    right_edge = conf["workspace"][0] - img["size"][0]
    img["pos"] = [right_edge if x is None else x, 100.0]
    loop._status["offset"] = [0.0, 0.0, 0.0]
    _engrave(loop, img)
    assert message in capsys.readouterr().out


def test_raster_lead_moves_stay_on_the_machine(loop):
    # even when the warning fires the lead-in is clamped to the travel limit
    img = _image(_dots_png([(0, 1)], 40, 4), pos=(0.0, 100.0))
    loop._status["offset"] = [0.0, 0.0, 0.0]
    _engrave(loop, img)
    xs = _param_values(loop.tx_buffer, driveboard.PARAM_TARGET_X)
    assert min(xs) >= 0.0 and max(xs) <= conf["workspace"][0]


def test_raster_exit_without_a_following_item_is_unconstrained():
    assert driveboard._raster_exit_y({"line_y": 5.0}, None) is None


def test_bidirectional_order_defaults_and_picks_the_near_end():
    fwd = {"leadin": 0.0, "leadout": 10.0, "line_y": 5.0}
    rev = {"leadin": 100.0, "leadout": 90.0, "line_y": 5.0}
    # head at the right: entering from the right (direction -1) is cheaper
    order = driveboard._order_raster_bidi([(fwd, rev)], [0], [100.0, 5.0], 1)
    assert order == [(0, -1)]
    order = driveboard._order_raster_bidi([(fwd, rev)], [0], [0.0, 5.0], 1)
    assert order == [(0, 1)]


def test_raster_emitters_return_none_for_nothing_to_emit():
    args = {
        "segments": [],
        "start": [0.0, 0.0],
        "next_rect": None,
        "posx": 0.0,
        "pxsize_x": 0.1,
        "leadin": 1.0,
        "x_min": 0.0,
        "x_max": 100.0,
        "pxarray": [],
        "pxarray_reversed": [],
        "px_n": 0,
        "seekrate": 6000.0,
        "feedrate_": 2000.0,
        "intensity_": 10.0,
    }
    nn = driveboard._emit_raster_nn(**args)
    bidi = driveboard._emit_raster_bidi(px_w=1, line_count=1, **args)
    assert nn is None and bidi is None


# ---------------------------------------------------------------------------
# Time estimates and the seek preview
# ---------------------------------------------------------------------------


def test_repeated_vertices_add_neither_time_nor_direction_change():
    plain = driveboard._polyline_time([[0.0, 0.0], [10.0, 0.0]], 10.0)
    padded = driveboard._polyline_time([[0.0, 0.0], [0.0, 0.0], [10.0, 0.0]], 10.0)
    assert padded == plain and plain[0] > 0


def _timed_image(**kw):
    def_ = {"kind": "image", "pos": [10.0, 10.0], "size": [20.0, 8.0], "data": ""}
    def_.update(kw)
    return def_


@pytest.mark.parametrize(
    "def_kw",
    [{"size": [0.0, 8.0]}, {"size": [20.0, -1.0]}, {"size": [0.1, 0.1]}],
    ids=["zero-width", "negative-height", "sub-pixel"],
)
def test_degenerate_images_take_no_time(def_kw):
    assert driveboard._image_time(_timed_image(**def_kw), {"pxsize": 0.4}, 6000.0, 2000.0) == 0.0


def test_unknown_raster_mode_is_timed_as_bidirectional(monkeypatch):
    monkeypatch.setitem(conf, "raster_mode", "Bidirectional")
    expected = driveboard._image_time(_timed_image(), {"pxsize": 0.4}, 6000.0, 2000.0)
    monkeypatch.setitem(conf, "raster_mode", "Spiral")
    assert driveboard._image_time(_timed_image(), {"pxsize": 0.4}, 6000.0, 2000.0) == expected
    assert expected > 0


def test_unreadable_image_data_is_timed_as_a_full_image():
    full = driveboard._image_time(_timed_image(), {"pxsize": 0.4}, 6000.0, 2000.0)
    junk = _timed_image(data="data:image/png;base64,AAAA")
    assert driveboard._image_time(junk, {"pxsize": 0.4}, 6000.0, 2000.0) == full


def test_image_wholly_beyond_the_bed_has_no_reachable_travel():
    off = _timed_image(pos=[conf["workspace"][0] + 500.0, 10.0])
    assert driveboard._image_time(off, {"pxsize": 0.4}, 6000.0, 2000.0) == 0.0


def test_preview_stops_tracking_the_head_after_a_relative_pass():
    def job(relative):
        return {
            "head": {},
            "passes": [{"items": [0], "relative": relative}, {"items": [0]}],
            "items": [{"def": 0}],
            "defs": [{"kind": "path", "data": [[[10.0, 10.0], [20.0, 10.0]]]}],
        }

    absolute_run = driveboard.job_preview(job(False))
    relative_run = driveboard.job_preview(job(True))
    # an absolute job ends with a seek home; once position is unknown there is none
    assert absolute_run["seeks"][-1][1] == [0.0, 0.0]
    assert [0.0, 0.0] not in [s[1] for s in relative_run["seeks"]]


def test_seek_extent_is_unknown_for_a_def_without_geometry():
    job = {"items": [{"def": 0}], "defs": [{"kind": "fill", "data": [[]]}]}
    assert driveboard._item_seek_rect(job, 0) is None


def test_items_keep_their_order_when_one_has_no_extent():
    job = {
        "items": [{"def": 0}, {"def": 1}, {"def": 2}],
        "defs": [
            {"kind": "path", "data": [[[300.0, 0.0]]]},
            {"kind": "fill", "data": []},
            {"kind": "path", "data": [[[100.0, 0.0]]]},
        ],
    }
    pass_ = {"items": [0, 1, 2]}
    assert driveboard._order_pass_items(job, pass_, [0.0, 0.0], None, 6000.0) == [0, 1, 2]


def test_items_far_to_near_are_reordered_near_first_when_nothing_follows():
    job = {
        "items": [{"def": i} for i in range(3)],
        "defs": [{"kind": "path", "data": [[[x, 0.0]]]} for x in (300.0, 200.0, 100.0)],
    }
    pass_ = {"items": [0, 1, 2]}
    assert driveboard._order_pass_items(job, pass_, [0.0, 0.0], None, 6000.0) == [2, 1, 0]


# ---------------------------------------------------------------------------
# Teardown and recovery of the serial link
# ---------------------------------------------------------------------------


class FakeLoop:
    """Just enough of SerialLoopClass for connected/close/reconnect."""

    def __init__(self, device="dev", alive=True, stop=False, close_raises=False):
        self.device = device
        self.alive = alive
        self.stop_processing = stop
        self.joined = False
        self.closed = False
        if device == "dev":
            self.device = types.SimpleNamespace(
                flushOutput=lambda: None, flushInput=lambda: None, close=self._close
            )
        self.close_raises = close_raises

    def _close(self):
        self.closed = True
        if self.close_raises:
            raise OSError("already gone")

    def is_alive(self):
        return self.alive

    def join(self):
        self.joined = True
        self.alive = False


def test_close_stops_the_thread_then_closes_the_port(monkeypatch):
    fake = FakeLoop()
    monkeypatch.setattr(driveboard, "SerialLoop", fake)
    assert driveboard.close() is True
    assert fake.stop_processing and fake.joined and fake.closed
    assert driveboard.SerialLoop is None


def test_close_tolerates_a_port_that_will_not_close(monkeypatch):
    monkeypatch.setattr(driveboard, "SerialLoop", FakeLoop(alive=False, close_raises=True))
    assert driveboard.close() is True
    assert driveboard.SerialLoop is None


def test_close_without_a_device_or_loop_reports_false(monkeypatch):
    monkeypatch.setattr(driveboard, "SerialLoop", FakeLoop(device=None, alive=False))
    assert driveboard.close() is False
    monkeypatch.setattr(driveboard, "SerialLoop", None)
    assert driveboard.close() is False


def test_reconnect_replaces_a_dead_loop(monkeypatch, capsys):
    dead = FakeLoop(alive=True, stop=True, close_raises=True)  # thread flagged to stop
    monkeypatch.setattr(driveboard, "SerialLoop", dead)

    def fake_connect_withfind(verbose):
        driveboard.SerialLoop = FakeLoop()

    monkeypatch.setattr(driveboard, "connect_withfind", fake_connect_withfind)
    driveboard.reconnect()
    assert dead.closed and dead.joined  # old thread and handle released first
    assert driveboard.SerialLoop is not dead
    assert "serial reconnected" in capsys.readouterr().out


def test_connected_is_false_for_a_stopping_or_dead_loop(monkeypatch):
    for fake in (FakeLoop(stop=True), FakeLoop(alive=False), FakeLoop(device=None)):
        monkeypatch.setattr(driveboard, "SerialLoop", fake)
        assert driveboard.connected() is False
    monkeypatch.setattr(driveboard, "SerialLoop", FakeLoop())
    assert driveboard.connected() is True


def test_prettify_ignores_a_trailing_blank_after_data_only_chunks(fresh_prettify):
    assert driveboard.prettify_serial(bytes([200])) == "200"


def test_mill_job_without_a_machine_runs_nowhere_and_leaves_no_dispatch_state(monkeypatch):
    monkeypatch.setattr(driveboard, "SerialLoop", None)
    # a disconnected job falls over on the first send; locks must still be released
    with contextlib.suppress(AttributeError):
        driveboard.job({"head": {"kind": "mill"}, "defs": []})
    assert driveboard._dispatch_thread is None
    assert driveboard._dispatch_lock.acquire(blocking=False)
    driveboard._dispatch_lock.release()


# ---------------------------------------------------------------------------
# Whole-job work-area check (import review)
# ---------------------------------------------------------------------------


def _area_job(def_):
    return {"items": [{"def": 0}], "defs": [def_]}  # imports usually have no passes


def _area_path(*points):
    return _area_job({"kind": "path", "data": [list(points)]})


def test_outside_work_area_none_when_everything_fits(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 0])
    loop._status["offset"] = [0.0, 0.0, 0.0]
    assert driveboard.job_outside_work_area(_area_path([0, 0], [1220, 610])) is None
    assert driveboard.job_outside_work_area({"items": [], "defs": []}) is None


def test_outside_work_area_names_the_first_excess(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 0])
    loop._status["offset"] = [0.0, 0.0, 0.0]
    job = _area_path([10, 10], [1450.4, 10])
    assert driveboard.job_outside_work_area(job) == "x 1450 > 1220 mm"
    job = _area_path([10, -5])
    assert driveboard.job_outside_work_area(job) == "y -5 < 0 mm"
    job = _area_job({"kind": "fill", "data": [[[10, 700]]]})
    assert driveboard.job_outside_work_area(job) == "y 700 > 610 mm"


def test_outside_work_area_ignores_item_translate(loop, monkeypatch):
    # no run applies an item's translate, so neither does the check
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 0])
    loop._status["offset"] = [0.0, 0.0, 0.0]
    job = _area_path([10, 10])
    job["items"][0]["translate"] = [1300, 0, 0]
    assert driveboard.job_outside_work_area(job) is None


def _area_mill(*moves):
    return {"head": {"kind": "mill"}, "defs": [{"data": list(moves)}]}


def test_outside_work_area_checks_mill_moves_like_a_mill_run(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 50.0])
    loop._status["offset"] = [300.0, 0.0, 0.0]  # a mill runs in machine coordinates
    job = _area_mill(["G0", [10, 10, None]], ["F", 100], ["G1", [5000, 10, None]])
    assert driveboard.job_outside_work_area(job) == "x 5000 > 1220 mm"
    job = _area_mill(["G0", [10, None, None]], ["G1", [1220, 610, 50]])
    assert driveboard.job_outside_work_area(job) is None
    assert driveboard.job_outside_work_area(_area_mill(["G1", [10, 10, -1]])) == "z -1 < 0 mm"


def test_outside_work_area_checks_image_corners(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 0])
    loop._status["offset"] = [0.0, 0.0, 0.0]
    img = {"kind": "image", "pos": [1200, 0], "size": [50, 10], "data": ""}
    assert driveboard.job_outside_work_area(_area_job(img)) == "x 1250 > 1220 mm"
    img["pos"] = [100, 0]
    assert driveboard.job_outside_work_area(_area_job(img)) is None


def test_outside_work_area_shifts_the_limits_by_the_offset(loop, monkeypatch):
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 0])
    loop._status["offset"] = [100.0, 0.0, 0.0]
    # limits become [-100, 1120]
    assert driveboard.job_outside_work_area(_area_path([-100, 0], [1120, 0])) is None
    assert driveboard.job_outside_work_area(_area_path([1150, 0])) == "x 1150 > 1120 mm"
    assert driveboard.job_outside_work_area(_area_path([-101, 0])) == "x -101 < -100 mm"


def test_outside_work_area_z_only_with_a_z_axis(loop, monkeypatch):
    loop._status["offset"] = [0.0, 0.0, 0.0]
    job = _area_path([10, 10, 60])
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 0])
    assert driveboard.job_outside_work_area(job) is None
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 50.0])
    assert driveboard.job_outside_work_area(job) == "z 60 > 50 mm"


def test_outside_work_area_without_a_machine_uses_zero_offsets(monkeypatch):
    monkeypatch.setattr(driveboard, "SerialLoop", None)
    monkeypatch.setitem(conf, "workspace", [1220.0, 610.0, 0])
    assert driveboard.job_outside_work_area(_area_path([1, 1])) is None
    assert driveboard.job_outside_work_area(_area_path([-1, 1])) == "x -1 < 0 mm"
