"""Regression tests for serial.c flow control and framing, executed in simavr.

  - one lost or extra wire byte must not leave the doubled-byte check
    misaligned for good, so a resume recovers the link.

Self-skips unless avr-gcc + libsimavr-dev are present.
"""

import firmware_sim as fw
import pytest

pytestmark = fw.skip_unless_available

CMD_STATUS = 3


def _pad(n):
    """Doubled status requests: harmless control bytes that let time pass."""
    return fw.double([CMD_STATUS] * n)


def _stop_codes(frame):
    return [b for b in frame if 32 < b < 65]


def test_lost_byte_resyncs_so_resume_recovers():
    # A lone extra byte shifts every following pair by one.
    send = (
        [ord("A")]
        + _pad(5)
        + fw.double([fw.CMD_RESUME, fw.CMD_SUPERSTATUS])
        + fw.line_program(5, feedrate=3000)
        + _pad(40)
        + fw.double([fw.CMD_SUPERSTATUS])
    )
    out, _, info = fw.run(send=send, run_cycles=10_000_000, count_portb=fw.X_STEP_PORTB_BIT)
    fr = fw.frames(out)
    assert fw.STOPERROR_TRANSMISSION_ERROR in _stop_codes(fr[1]), "mismatch not reported"
    assert _stop_codes(fr[-1]) == [], "link stayed misaligned after the resume"
    assert info["steps"] == 445


def test_harness_rejects_oversized_send():
    with pytest.raises(AssertionError, match="too many values"):
        fw.run(send=[CMD_STATUS] * 16385)
