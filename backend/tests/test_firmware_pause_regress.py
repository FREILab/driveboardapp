"""Resume-after-pause speed profile, executed in simavr.

A pause freezes the head mid-block. On unpause the stepper must ease back up
from rest rather than resume at the planned speed for that point:

  - mid raster line (entered at full speed off a colinear lead-in) it must not
    jump from standstill straight to the nominal rate,
  - late in a decelerating block it must still brake to the block's final
    rate, not run into the end of the move at speed,
  - paused in the lead-in, the ramp must carry into the raster line instead
    of stepping up to the planned junction speed.

Timings below are ms of simulated time, from the first byte on the wire; one
logical byte (two wire bytes) takes BYTE_MS. Each scenario checks where its
pause landed before asserting, so a timing drift fails loudly instead of
testing the wrong phase.

Self-skips unless avr-gcc + libsimavr-dev are present.
"""

import firmware_sim as fw

pytestmark = fw.skip_unless_available

P = fw.encode_value_param
CMD_NONE = ord("A")  # filler: paces the scenario and feeds the serial watchdog
FEED = 4000  # mm/min
X_STEPS_PER_MM = 88.88888888
NOMINAL = FEED * X_STEPS_PER_MM  # steps/min
X_ORIGIN = 5.0  # CONFIG_X_ORIGIN_OFFSET, stepper_position is absolute
BYTE_MS = 2 * fw.RX_GAP_CYCLES / fw.CYCLES_PER_MS


def _wait(ms):
    return [CMD_NONE] * int(ms / BYTE_MS)


def _line(x):
    return P(fw.PARAM_FEEDRATE, FEED) + P(fw.PARAM_TARGET_X, x) + [fw.CMD_LINE]


def _sample(logical, at_ms, symbol):
    """Value of a 4-byte symbol at_ms into the scenario."""
    send = fw.double(logical + _wait(800))
    _, _, info = fw.run(
        send=send,
        run_cycles=int(at_ms * fw.CYCLES_PER_MS) + fw.BOOT_CYCLES,
        watch_symbol=symbol,
        watch_size=4,
    )
    return info["final"]


def _rate(logical, at_ms):
    return _sample(logical, at_ms, "adjusted_rate")


def _x_mm(logical, at_ms):
    return _sample(logical, at_ms, "stepper_position") / X_STEPS_PER_MM


def _paused(prog, pause_at):
    """prog, a pause at pause_at ms, 380 ms held, then an unpause.
    Returns the stream and the ms the unpause has arrived at."""
    send = prog + _wait(pause_at - len(prog) * BYTE_MS) + [fw.CMD_PAUSE] + _wait(380)
    send += [fw.CMD_UNPAUSE]
    return send, len(send) * BYTE_MS


def test_unpause_mid_line_ramps_from_rest():
    # the second line enters at the nominal rate off the colinear first one
    send, t_un = _paused(_line(10) + _line(110), 950)
    assert _rate(send, t_un - 95) == round(NOMINAL), "pause not at cruise"
    x_paused = _x_mm(send, t_un)
    assert 30 < x_paused < 90, "pause not mid second line"

    # a rate from rest, not straight back to nominal
    assert _rate(send, t_un + 38) < 0.25 * NOMINAL
    assert _x_mm(send, t_un + 38) - x_paused < 0.2
    # then it ramps back up to the nominal rate
    ramp = _rate(send, t_un + 76)
    assert 0.25 * NOMINAL < ramp < NOMINAL
    assert _rate(send, t_un + 380) == round(NOMINAL)


def test_unpause_late_in_deceleration_still_brakes():
    send, t_un = _paused(_line(20), 380)
    # paused part way down the deceleration, a little over a mm short of the end
    assert 0.3 * NOMINAL < _rate(send, t_un - 95) < 0.8 * NOMINAL, "pause not decelerating"
    assert 0.5 < X_ORIGIN + 20 - _x_mm(send, t_un) < 2.0

    # reaches the end of the move (no crawl at the minimum rate) and arrives
    # there braked like the plan (~250 mm/min), not at speed
    assert abs(_x_mm(send, t_un + 285) - (X_ORIGIN + 20)) < 0.01
    assert _rate(send, t_un + 285) < 600 * X_STEPS_PER_MM


def test_unpause_in_lead_in_carries_ramp_into_next_line():
    send, t_un = _paused(_line(10) + _line(110), 190)
    junction = X_ORIGIN + 10
    assert 2.0 < junction - _x_mm(send, t_un) < 4.0, "pause not late in the lead-in"

    # just past the junction the head is still easing up, the next line's
    # planned entry speed is the nominal rate
    assert 0.1 < _x_mm(send, t_un + 115) - junction < 1.0
    assert _rate(send, t_un + 115) < 0.95 * NOMINAL
