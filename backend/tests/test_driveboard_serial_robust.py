"""Host side of the serial link holding up against what the wire really does.

A resume resets the controller's rx buffer when it arrives, not when it is
sent, so acks and status frames already on their way cross it. The tally can
drift through lost acks and resyncs. The controller can reboot under a live
session. And when a stop nobody asked for does show up, there has to be
something to look at afterwards.
"""

import json
import os

import driveboard
import pytest
from config import conf
from helpers import FakeSerialDevice

ACK = ord(driveboard.CMD_CHUNK_PROCESSED)
END = ord(driveboard.STATUS_END)


@pytest.fixture
def clock(monkeypatch):
    """A clock the test moves on by hand."""
    t = [1000.0]
    monkeypatch.setattr(driveboard.time, "time", lambda: t[0])
    return t


def frame(*stops):
    """A status frame as the controller sends it while stopped."""
    return [ord(s) for s in stops] + [192] * 4 + [ord("x"), END]


def stopped_then_resumed(loop):
    loop.device = FakeSerialDevice()
    driveboard.stop()
    loop._serial_write()
    driveboard.unstop()
    loop._serial_write()  # RESUME on the wire
    loop._status["ready"] = True


# ---------------------------------------------------------------------------
# acks crossing a resume (Home with homing_reset_offset: /absoffset, /homing)
# ---------------------------------------------------------------------------


def test_ack_in_flight_across_resume_leaves_tally_true(loop, clock):
    loop.device = FakeSerialDevice()
    driveboard.absoffset(0, 0, 0)  # 18 bytes
    loop._serial_write()  # the first 16 go out
    assert loop.firmbuf_used == 16
    driveboard.homing()  # RESUME and 'I' a moment later
    loop._serial_write()
    # the controller acks the 16 it read before the resume reached it, and
    # that ack lands after the host zeroed its tally
    loop.device._rx.extend([ACK])
    loop._serial_read()
    clock[0] += loop.RESUME_SETTLE + 0.05
    loop._serial_write()
    # the controller holds what followed the resume: 2 offset bytes and 'I'
    assert loop.firmbuf_used == 3
    assert loop.acks_after_resume == 1


def test_no_data_goes_out_until_the_resume_settles(loop, clock):
    stopped_then_resumed(loop)
    driveboard.move(10, 10)
    loop.request_status = 0  # only the move is of interest
    sent = len(loop.device.written)
    loop._serial_write()
    assert len(loop.device.written) == sent, "sent into the pipe the resume is clearing"
    clock[0] += loop.RESUME_SETTLE + 0.05
    loop._serial_write()
    assert len(loop.device.written) > sent


# ---------------------------------------------------------------------------
# a status frame from before the resume arriving after it
# ---------------------------------------------------------------------------


def test_stale_stop_frame_after_resume_keeps_queued_commands(loop, clock):
    stopped_then_resumed(loop)
    driveboard.move(10, 10)
    queued = len(loop.tx_buffer)
    loop.device._rx.extend(frame("!"))  # built just before the resume arrived
    loop._serial_read()
    assert len(loop.tx_buffer) == queued, "a command cut off mid parameter"
    assert loop._status["stops"] == {}


def test_stop_still_standing_after_the_window_shows(loop, clock):
    stopped_then_resumed(loop)
    loop.device._rx.extend(frame("!"))
    loop._serial_read()
    clock[0] += loop.RESUME_SETTLE + 0.05
    # the resume never took: the controller keeps reporting the stop
    loop.device._rx.extend(frame("!"))
    loop._serial_read()
    assert loop._status["stops"] == {"requested": True}


def test_new_stop_after_the_window_is_handled(loop, clock):
    stopped_then_resumed(loop)
    clock[0] += loop.RESUME_SETTLE + 0.05
    driveboard.move(10, 10)
    loop.device._rx.extend(frame(":"))
    loop._serial_read()
    assert loop._status["stops"] == {"data": True}
    assert len(loop.tx_buffer) == 0  # stop housekeeping ran


# ---------------------------------------------------------------------------
# flow control margin and the negative tally
# ---------------------------------------------------------------------------


class ControllerModel(FakeSerialDevice):
    """Counts what the controller's rx buffer really holds, acking every 16
    bytes it consumes as firmware/src/serial.c does."""

    def __init__(self):
        super().__init__()
        self.held = 0
        self.peak = 0
        self.unacked = 0

    def write(self, data):
        # control chars below 16 bypass the buffer, by protocol sent twice
        self.held += sum(1 for b in list(data)[::2] if b >= 16)
        self.peak = max(self.peak, self.held)
        return super().write(data)

    def consume(self, n):
        n = min(n, self.held)
        self.held -= n
        acks, self.unacked = divmod(self.unacked + n, 16)
        self._rx.extend([ACK] * acks)


def test_resync_with_unacked_bytes_cannot_overflow(loop):
    """The stall resync zeroes the tally while the controller may have read up
    to 15 bytes it has not acked yet. Its next ack then counts those too, and
    the tally runs 15 low for good."""
    dev = loop.device = ControllerModel()
    dev.unacked = 15  # read, not yet acked; the buffer itself is empty
    loop.request_status = 0
    loop.tx_buffer = bytearray(b"L")  # a manual command ahead of the job
    loop.job_size = 1
    # an ack went missing: the tally is stuck high, but idle proves a drain
    loop.firmbuf_used = loop.FIRMBUF_SIZE
    loop.last_tx_progress = 0.0
    loop.last_firmware_idle = 1.0
    loop._serial_write()
    assert loop.firmbuf_used == 0  # resynced
    loop._serial_write()  # the command goes out
    loop._serial_write()  # and its buffer is let go
    loop.tx_buffer = bytearray([ord(driveboard.CMD_NONE)] * 2000)
    loop.job_size = len(loop.tx_buffer)
    for _ in range(40):
        loop._serial_write()
        loop._serial_read()
    for _ in range(100):  # the controller now works through it slowly
        dev.consume(1)
        loop._serial_read()
        loop._serial_write()
    assert dev.peak <= 255, f"controller rx buffer overflowed: {dev.peak} bytes"


def test_negative_tally_is_clamped_and_counted(loop, capsys):
    loop.firmbuf_used = 0
    loop.device = FakeSerialDevice(bytes([ACK]))
    loop._serial_read()
    assert loop.firmbuf_used == 0
    assert loop.negative_tally_clamps == 1
    assert "tracking too low" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the controller rebooting mid-session
# ---------------------------------------------------------------------------


def test_hello_mid_session_aborts_the_job_and_stands_a_stop(loop):
    loop.device = FakeSerialDevice()
    loop.tx_buffer = bytearray([ord(driveboard.CMD_NONE)] * 500)
    loop.job_size = 500
    loop.tx_pos = 200
    loop.firmbuf_used = 200
    loop.job_active = True
    loop.device._rx.extend(b"~")
    loop._serial_read()
    assert len(loop.tx_buffer) == 0 and loop.tx_pos == 0
    assert loop.firmbuf_used == 0
    assert loop.job_active is False
    assert loop.request_stop is True  # hold the fresh controller stopped
    assert loop._status["stops"].get("reset") is True
    assert loop.hello_midsession == 1
    # it stands through the frames that follow, unstop or not
    driveboard.unstop()
    loop._serial_write()
    loop.device._rx.extend([END])
    loop._serial_read()
    assert loop._status["stops"].get("reset") is True
    with pytest.raises(ValueError, match="reset"):
        driveboard.job_stop_guard()


def test_homing_clears_the_reset_stop(loop):
    loop.device = FakeSerialDevice(b"~")
    loop._serial_read()
    driveboard.homing()
    loop._serial_write()
    loop.device._rx.extend([END])
    loop._serial_read()
    assert "reset" not in loop._status["stops"]


# ---------------------------------------------------------------------------
# the incident record
# ---------------------------------------------------------------------------


def _incidents(path):
    with open(os.path.join(path, driveboard.INCIDENTS_FILE)) as fp:
        return [json.loads(line) for line in fp]


def test_unexplained_stop_writes_one_incident_per_episode(loop, tmp_path, monkeypatch):
    monkeypatch.setitem(conf, "confdir", str(tmp_path))
    loop.device = FakeSerialDevice()
    loop.request_status = 0
    driveboard.move(10, 10)
    loop._serial_write()
    loop.device._rx.extend(frame(":") + frame(":"))  # stopped, two frames
    loop._serial_read()
    (record,) = _incidents(tmp_path)
    assert record["stop"] == "data" and record["code"] == ":"
    assert record["job_size"] > 0 and record["tx_pos"] == record["job_size"]
    assert record["firmbuf_used"] == record["job_size"]
    assert record["mid_raster"] is False
    assert record["since_resume"] is None
    assert "42" in record["tx"]  # 'B', the line command
    assert "3a" in record["rx"]
    assert loop.incident.startswith("data stop")
    monkeypatch.setattr(driveboard, "connected", lambda: True)
    assert driveboard.status()["incident"] == loop.incident


def test_requested_and_limit_stops_write_no_incident(loop, tmp_path, monkeypatch):
    monkeypatch.setitem(conf, "confdir", str(tmp_path))
    loop.device = FakeSerialDevice(bytes(frame("!") + frame("$")))
    loop._serial_read()
    assert not os.path.exists(os.path.join(tmp_path, driveboard.INCIDENTS_FILE))
    assert loop.incident is None
    monkeypatch.setattr(driveboard, "connected", lambda: True)
    assert "incident" not in driveboard.status()


def test_incident_log_keeps_the_newest(loop, tmp_path, monkeypatch):
    monkeypatch.setitem(conf, "confdir", str(tmp_path))
    for i in range(driveboard.INCIDENTS_KEEP + 5):
        loop.job_size = i
        loop._record_incident('"', "buffer")
    records = _incidents(tmp_path)
    assert len(records) == driveboard.INCIDENTS_KEEP
    assert records[-1]["job_size"] == driveboard.INCIDENTS_KEEP + 4


def test_incidents_route_serves_the_log(app, tmp_path, monkeypatch):
    monkeypatch.setitem(conf, "confdir", str(tmp_path))
    with open(os.path.join(tmp_path, driveboard.INCIDENTS_FILE), "w") as fp:
        fp.write('{"stop": "data"}\n')
    app.authorization = ("Basic", ("laser", "laser"))
    resp = app.get("/incidents")
    assert resp.status_int == 200
    assert resp.text == '{"stop": "data"}\n'
