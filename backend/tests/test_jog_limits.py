"""Jogging must not run the head into a limit switch at seek speed.

The real SerialLoopClass, its RX parser and the real /jog route; only the
serial device is faked. Frames are built the way the firmware writes them
(protocol_idle in firmware/src/protocol.c).
"""

import driveboard
import pytest
import web
from config import conf
from test_driveboard_safety import encode_param, feed


def frame(pos, offset=None, idle=True, stops=""):
    """One firmware status frame; giving an offset makes it a super frame."""
    out = bytearray()
    if idle:
        out += driveboard.INFO_IDLE_YES.encode("latin-1")
    out += stops.encode("latin-1")
    for marker, v in zip(
        (driveboard.INFO_POS_X, driveboard.INFO_POS_Y, driveboard.INFO_POS_Z), pos
    ):
        out += encode_param(marker, v)
    if offset is not None:
        for marker, v in zip(
            (driveboard.INFO_OFFSET_X, driveboard.INFO_OFFSET_Y, driveboard.INFO_OFFSET_Z), offset
        ):
            out += encode_param(marker, v)
    out += driveboard.STATUS_END.encode("latin-1")
    return bytes(out)


def drain(loop):
    """Pretend everything queued just went out."""
    loop.tx_buffer = bytearray()
    loop.tx_pos = 0
    loop.frames_since_write = 0


def settle(loop, pos, offset=(0, 0, 0)):
    """The machine idle at pos: the frame in flight, then a fresh one."""
    drain(loop)
    feed(loop, frame(pos, offset))
    feed(loop, frame(pos, offset))


def home(loop):
    """A homing cycle seen through to its end, from the host's side."""
    driveboard.homing()
    drain(loop)
    feed(loop, frame([0, 0, 0], [0, 0, 0], idle=False))
    feed(loop, frame([-5, -1, 0], [0, 0, 0], idle=False))
    feed(loop, frame([0, 0, 0], [0, 0, 0]))


@pytest.fixture
def machine(loop, app, monkeypatch):
    """Real loop behind the real /jog route. Records each move() and feedrate()."""
    app.authorization = ("Basic", ("laser", "laser"))
    monkeypatch.setattr(driveboard, "connected", lambda: True)
    monkeypatch.setitem(conf, "workspace", [1220, 610, 0])
    monkeypatch.setitem(conf, "jog_soft_limits", True)
    monkeypatch.setitem(conf, "seekrate", 6000)
    monkeypatch.setitem(conf, "jog_unhomed_feedrate", 2000)
    web.jog_target_last = None
    moves, feeds = [], []
    real_move, real_feed = driveboard.move, driveboard.feedrate
    monkeypatch.setattr(driveboard, "move", lambda *a: (moves.append(a), real_move(*a)))
    monkeypatch.setattr(driveboard, "feedrate", lambda v: (feeds.append(v), real_feed(v)))
    yield {"loop": loop, "app": app, "moves": moves, "feeds": feeds}
    web.jog_target_last = None


def jog(app, x, y=0, z=0):
    return app.get(f"/jog/{x}/{y}/{z}", expect_errors=True).status_int


# --------------------------------------------------------------------------
# Status double buffer
# --------------------------------------------------------------------------


def test_offset_survives_a_normal_frame(loop):
    feed(loop, frame([1200, 0, 0], [0, 0, 0]))
    feed(loop, frame([0, 0, 0], [1200, 0, 0]))  # offset set at the head
    # Busy, so the host asks normal frames only. The flip reuses the buffer
    # from two frames back, and only a super frame writes the offset.
    feed(loop, frame([5, 0, 0], idle=False))
    assert loop._status["offset"][0] == 1200


def test_jog_burst_after_an_offset_change_stays_inside(machine):
    loop, app, moves = machine["loop"], machine["app"], machine["moves"]
    home(loop)
    settle(loop, [1200, 0, 0])
    driveboard.absoffset(1200, 0, 0)
    drain(loop)
    feed(loop, frame([1200, 0, 0], [0, 0, 0]))  # in flight from before
    feed(loop, frame([0, 0, 0], [1200, 0, 0]))  # the one fresh super frame
    assert jog(app, 50) == 200
    assert moves[-1][0] == 20  # machine x 1220, the edge
    drain(loop)
    feed(loop, frame([10, 0, 0], idle=False))  # moving, a normal frame
    assert jog(app, 50) == 200  # key repeat
    assert moves[-1][0] + 1200 <= conf["workspace"][0]


# --------------------------------------------------------------------------
# Homing
# --------------------------------------------------------------------------


def test_jog_refused_while_homing(machine):
    loop, app, moves = machine["loop"], machine["app"], machine["moves"]
    home(loop)
    settle(loop, [-600, 0, 0], [600, 0, 0])
    driveboard.homing()  # the firmware clears its offset when it runs this
    # queued behind the cycle, a jog computed against the offset 600 frame
    # would run to machine x=-610, past the X1 switch
    assert jog(app, -10) == 409
    assert moves == []


def test_homing_itself_is_untouched(machine):
    loop = machine["loop"]
    driveboard.homing()
    assert bytes(loop.tx_buffer) == driveboard.CMD_HOMING.encode()
    assert loop.request_resume is True


def test_homing_success_enables_clamped_seek_jogs(machine):
    loop, app, moves, feeds = machine["loop"], machine["app"], machine["moves"], machine["feeds"]
    assert driveboard.status()["homed"] is False
    home(loop)
    st = driveboard.status()
    assert st["homed"] is True and st["homing"] is False
    settle(loop, [1200, 0, 0])
    assert jog(app, 50) == 200
    assert moves[-1][0] == 1220 and feeds[-1] == conf["seekrate"]


def test_idle_between_homing_moves_is_not_the_end(machine):
    # homing_move leaves the stepper flagged as processing for one tick after
    # its planner has emptied, and a status frame then says idle with the
    # head a back-off from the switch
    loop = machine["loop"]
    driveboard.homing()
    drain(loop)
    feed(loop, frame([0, 0, 0], idle=False))
    feed(loop, frame([0, 0, 0], idle=False))
    feed(loop, frame([5, -5, 0]))  # x backed off 10 mm, minus the 5 mm origin
    assert driveboard.status()["homing"] is True
    assert driveboard.status()["homed"] is False
    feed(loop, frame([-5, -1, 0], idle=False))
    feed(loop, frame([0, 0, 0]))  # the origin move has run
    assert driveboard.status()["homed"] is True


def test_idle_frame_from_before_homing_is_not_the_end(machine):
    # sent before CMD_HOMING arrived, idle at the origin it was switched on at
    loop = machine["loop"]
    driveboard.homing()
    drain(loop)
    feed(loop, frame([0, 0, 0], [0, 0, 0]))
    feed(loop, frame([0, 0, 0], [0, 0, 0]))
    assert driveboard.status()["homed"] is False
    assert driveboard.status()["homing"] is True


def test_switch_reported_during_homing_does_not_abort_it(machine):
    # every approach ends on a limit stop, and a switch still pressed during a
    # back-off is reported as one too
    loop = machine["loop"]
    driveboard.homing()
    drain(loop)
    feed(loop, frame([0, 0, 0], idle=False))
    feed(loop, frame([0, 0, 0], idle=False, stops=driveboard.ERROR_LIMIT_HIT_X1))
    feed(loop, frame([-5, -1, 0], idle=False))
    feed(loop, frame([0, 0, 0]))
    assert driveboard.status()["homed"] is True


def test_stopped_homing_stays_unhomed(machine):
    loop, app = machine["loop"], machine["app"]
    driveboard.homing()
    drain(loop)
    feed(loop, frame([0, 0, 0], idle=False))
    feed(loop, frame([0, 0, 0], idle=False, stops=driveboard.ERROR_SERIAL_STOP_REQUEST))
    st = driveboard.status()
    assert st["homed"] is False and st["homing"] is False
    assert jog(app, 10) == 200  # not trapped


def test_limit_hit_drops_homed(machine):
    loop, app, feeds = machine["loop"], machine["app"], machine["feeds"]
    home(loop)
    # the head coasts on past the switch, the position is gone
    feed(loop, frame([1100, 0, 0], [0, 0, 0], idle=False, stops=driveboard.ERROR_LIMIT_HIT_X2))
    assert driveboard.status()["homed"] is False
    assert jog(app, -10) == 200
    assert feeds[-1] == conf["jog_unhomed_feedrate"]


def test_controller_reset_drops_homed_and_still_jogs_slowly(machine):
    loop, app, moves, feeds = machine["loop"], machine["app"], machine["moves"], machine["feeds"]
    home(loop)
    feed(loop, driveboard.INFO_HELLO.encode("latin-1"))
    driveboard.unstop()
    settle(loop, [0, 0, 0])
    st = driveboard.status()
    assert st["homed"] is False and "reset" in st["stops"]
    assert jog(app, -10, -10) == 200  # towards the switches too
    assert moves[-1] == (-10.0, -10.0, 0.0) and feeds[-1] == conf["jog_unhomed_feedrate"]


# --------------------------------------------------------------------------
# Jog rules
# --------------------------------------------------------------------------


def test_unhomed_jog_is_relative_and_slow_in_every_direction(machine):
    loop, app, moves, feeds = machine["loop"], machine["app"], machine["moves"], machine["feeds"]
    # power-up reports 0,0 wherever the head really is
    settle(loop, [0, 0, 0])
    assert jog(app, -50, -50) == 200  # towards home: clamped to nothing before
    settle(loop, [0, 0, 0])
    assert jog(app, 50) == 200
    assert moves == [(-50.0, -50.0, 0.0), (50.0, 0.0, 0.0)]
    assert set(feeds) == {conf["jog_unhomed_feedrate"]}
    assert conf["jog_unhomed_feedrate"] < 0.4 * 8000  # firmware homing approach


def test_jog_waits_for_the_new_offset(machine):
    loop, app, moves = machine["loop"], machine["app"], machine["moves"]
    home(loop)
    settle(loop, [1200, 0, 0])
    driveboard.absoffset(1200, 0, 0)
    drain(loop)
    feed(loop, frame([1200, 0, 0], [0, 0, 0]))  # in flight from before
    feed(loop, frame([0, 0, 0]))  # new position, old offset
    assert jog(app, 50) == 409
    feed(loop, frame([0, 0, 0], [1200, 0, 0]))  # idle super frame
    assert jog(app, 50) == 200
    assert moves[-1][0] == 20


@pytest.mark.parametrize("homed", [True, False])
def test_held_key_queues_at_most_two_jogs(machine, homed):
    loop, app, moves = machine["loop"], machine["app"], machine["moves"]
    if homed:
        home(loop)
    settle(loop, [100, 100, 0])
    codes = []
    for _ in range(6):
        codes.append(jog(app, 10))
        drain(loop)  # sent, but the machine has not got far yet
        feed(loop, frame([100, 100, 0], idle=False))
    assert codes == [200, 200, 409, 409, 409, 409] and len(moves) == 2
    # one step left to run: one more may queue behind it
    feed(loop, frame([110, 100, 0], idle=False))
    assert jog(app, 10) == 200
    settle(loop, [130, 100, 0])
    assert jog(app, 10) == 200


def test_soft_limits_off_jogs_homed_relative_at_seek(machine, monkeypatch):
    monkeypatch.setitem(conf, "jog_soft_limits", False)
    loop, app, moves, feeds = machine["loop"], machine["app"], machine["moves"], machine["feeds"]
    home(loop)
    settle(loop, [1200, 0, 0])
    assert jog(app, 50) == 200
    assert moves == [(50.0, 0.0, 0.0)] and feeds == [conf["seekrate"]]
