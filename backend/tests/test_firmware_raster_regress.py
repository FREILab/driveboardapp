"""Raster line regressions, the host's segment stream executed in simavr.

  - a raster line must carry straight on into its lead-out. The controller
    reads no command while a line's pixel data streams, which lasts until the
    raster move has run, so a lead-out sent after the data reached the planner
    only once the head had already stopped dead at full speed,
  - sending the lead-out ahead of the data must still have every pixel arrive
    and latch where it was sent.

The firmware reports an underrun ('w') whenever its protocol loop waits on the
wire, which the harness's byte pacing makes constant, so the pixels themselves
are checked instead: a missing or late byte blanks a pixel and pushes the rest
back, which leaves the line's last pixel unread.

Self-skips unless avr-gcc + libsimavr-dev are present.
"""

import driveboard
import firmware_sim as fw

pytestmark = fw.skip_unless_available

CMD_STATUS = 3
PIXEL_WIDTH = 0.1
LEADIN = 10.0
START = 20.0


def _segment(loop, pixels):
    """The doubled wire bytes the host emits for one raster segment, followed
    by status requests that report the head position as it runs and keep the
    serial watchdog fed. Returns (send, leadout_x)."""
    end = START + len(pixels) * PIXEL_WIDTH
    orientation = {
        "leadin": START - LEADIN,
        "start": START,
        "end": end,
        "leadout": end + LEADIN,
        "line_y": 5.0,
        "data": pixels,
        "lo": 0,
        "hi": len(pixels),
    }
    driveboard.pixelwidth(PIXEL_WIDTH)
    driveboard._emit_raster_segment(orientation, 6000, 3000, 80)
    return fw.double(list(loop.tx_buffer) + [CMD_STATUS] * 3400), orientation["leadout"]


def _reported_x(out):
    """The x position of every status frame, in order."""
    xs = []
    for i in range(4, len(out)):
        if out[i] == ord("x") and all(b >= 128 for b in out[i - 4 : i]):
            num = sum((out[i - 4 + k] - 128) << (7 * k) for k in range(4))
            xs.append(num / 1000.0 - 134217.728)
    return xs


def test_raster_line_runs_straight_into_its_lead_out(loop):
    send, leadout = _segment(loop, [128] * 200)
    out, _ = fw.run(send=send, run_cycles=22_000_000)
    xs = _reported_x(out)
    assert xs and abs(xs[-1] - leadout) < 0.01, "the lead-out did not run to its end"

    # Status frames go out back to back, so the x travelled between two of them
    # tracks the head's speed. From the raster start to the last millimetre of
    # the lead-out, where braking to a stop takes over, it must never stall.
    steps = [(a, b - a) for a, b in zip(xs, xs[1:]) if START <= a <= leadout - 1.0]
    raster = sorted(dx for a, dx in steps if a <= START + 15.0)
    nominal = raster[len(raster) // 2]
    slowest = min(steps, key=lambda s: s[1])
    assert slowest[1] > 0.25 * nominal, (
        f"head stalled at x={slowest[0]:.2f}, {slowest[1]:.3f}mm per status frame "
        f"against {nominal:.3f}mm at speed"
    )


def test_raster_pixels_arrive_intact_with_the_lead_out_first(loop):
    # a black first pixel, a mid grey last one, white between: the first pixel
    # sets the maximum, and the beam is left latched on the last
    pixels = [0] + [255] * 58 + [128]
    send, _ = _segment(loop, pixels)
    _, _, info = fw.run(send=send, run_cycles=12_000_000, watch_symbol="raster_pixel_intensity")
    nominal = 255 * 80 // 100

    def latched(gray):
        wire = int((255 - gray) / 2) + 128  # as SerialLoopClass.send_raster_data
        return (wire - 128) * 2 * nominal // 255  # as the stepper ISR

    assert info["max"] == latched(0), "the first pixel was lost"
    assert info["final"] == latched(128), "pixels were blanked or shifted along the line"
