"""Stepper ISR and homing regressions, executed in simavr.

  - a dwell or assist block that follows a line must not repeat the line's
    last step pulse on every tick (silent position loss, head moving while
    the beam pierces),
  - a stop during homing other than the limit hit that ends an approach must
    abort homing and stay set, not be resumed into the back-off.

Self-skips unless avr-gcc + libsimavr-dev are present.
"""

import firmware_sim as fw
import pytest

pytestmark = fw.skip_unless_available

CMD_HOMING = ord("I")
CMD_NONE = ord("A")


def _x_steps(send):
    """X step edges and the final X position in steps after the scenario."""
    _, _, info = fw.run(
        send=send,
        run_cycles=24_000_000,
        count_portb=fw.X_STEP_PORTB_BIT,
        watch_symbol="stepper_position",
        watch_size=4,
    )
    return info["steps"], info["final"]


@pytest.mark.parametrize(
    "follow",
    [
        fw.dwell_program(0, 0.5),
        fw.double([fw.CMD_AIR_ENABLE, fw.CMD_AIR_DISABLE]),
        fw.double([fw.CMD_AUX_ENABLE, fw.CMD_AUX_DISABLE]),
    ],
    ids=["dwell", "air", "aux"],
)
def test_block_after_line_does_not_repeat_its_last_step(follow):
    line = fw.line_program(1.0, feedrate=1000)
    steps, pos = _x_steps(line)
    assert steps > 0, "control line produced no step pulses"

    # the line's final step is still pulsed, at the top of the tick that pops
    # the following block, but only once
    after_steps, after_pos = _x_steps(line + follow)
    assert after_pos == pos
    assert after_steps == steps, "block after a line re-pulsed its last step"


@pytest.mark.parametrize(
    "send,code",
    [
        # stop part way into the first approach (filler delays it ~50ms)
        (
            fw.double([CMD_HOMING] + [CMD_NONE] * 100 + [fw.CMD_STOP]),
            fw.STOPERROR_SERIAL_STOP_REQUEST,
        ),
        # host goes silent: the watchdog stops the approach after ~1s
        (fw.double([CMD_HOMING]), fw.STOPERROR_SERIAL_WATCHDOG),
    ],
    ids=["user_stop", "watchdog"],
)
def test_stop_during_homing_aborts_it(send, code):
    _, _, info = fw.run(send=send, run_cycles=24_000_000, watch_symbol="stop_status")
    assert info["final"] == code, "homing cleared the stop and carried on"
