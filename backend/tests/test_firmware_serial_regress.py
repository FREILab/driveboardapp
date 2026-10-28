"""Regression tests for serial.c flow control and framing, executed in simavr.

  - one lost or extra wire byte must not leave the doubled-byte check
    misaligned for good, so a resume recovers the link,
  - CMD_CHUNK_PROCESSED acks go out as soon as a chunk is read, not only when
    the firmware next has something else to send (shown on a slow link, where
    acks queue up behind each other).

Self-skips unless avr-gcc + libsimavr-dev are present.
"""

import shutil

import firmware_sim as fw
import pytest

pytestmark = fw.skip_unless_available

E = fw.encode_value_param
CMD_STATUS = 3
CMD_CHUNK_PROCESSED = 5
RX_CHUNK_SIZE = 16


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


@pytest.mark.parametrize("npx", [120, 200])
def test_chunk_acks_sent_without_waiting_for_other_output(npx, tmp_path, monkeypatch):
    # The firmware reads a chunk about every 4000 cycles, slower than an ack
    # frame at 57600 baud (2778), so acks never queue up there. On a slower
    # link they do: two chunks get counted while one ack is still going out,
    # and a UDRE ISR that disables itself on an empty tx buffer strands the
    # rest until the firmware next has other output, which in this run is never.
    src = tmp_path / "src"
    shutil.copytree(fw.SRC_DIR, src)
    cfg = (src / "config.driveboardusb.h").read_text()
    assert "BAUD_RATE 57600" in cfg
    (src / "config.slowbaud.h").write_text(cfg.replace("BAUD_RATE 57600", "BAUD_RATE 19200"))
    monkeypatch.setattr(fw, "SRC_DIR", str(src))
    logical = (
        E("f", 300)
        + E("s", 100)
        + E("p", 0.5)
        + E("x", 1.0)
        + [fw.CMD_RASTER, fw.CMD_RASTER_DATA_START]
        + [200] * npx
        + [fw.CMD_RASTER_DATA_END]
    )
    out, _ = fw.run(send=fw.double(logical), run_cycles=12_000_000, variant="config.slowbaud.h")
    assert out.count(CMD_CHUNK_PROCESSED) == len(logical) // RX_CHUNK_SIZE


def test_harness_rejects_oversized_send():
    with pytest.raises(AssertionError, match="too many values"):
        fw.run(send=[CMD_STATUS] * 16385)
