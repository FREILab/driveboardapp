"""Regression tests for a CMD_RESUME racing the protocol loop's rx reads, in simavr.

CMD_RESUME resets the rx buffer and clears the stop from the rx ISR, so where
it lands relative to the main loop's read matters to within a few cycles:

  - between the empty check and the tail advance, tail ends up one past head
    and the buffer reads as 255 stale bytes, replayed with the stop cleared,
  - between reading a byte while stopped and the protocol loop's stopped
    check, that one stale byte runs as a fresh command.

The window is too narrow to hit by chance, so the harness forks at the resume
and each child sends it a few cycles later than the last, sweeping it across
whole main loop passes.

Self-skips unless avr-gcc + libsimavr-dev are present.
"""

import subprocess

import firmware_sim as fw

pytestmark = fw.skip_unless_available

E = fw.encode_value_param
RX_GAP = fw.RX_GAP_CYCLES
CMD_NONE = ord("A")
CMD_REF_RELATIVE = ord("E")
CMD_INVALID = ord("Z")


def _resume_sweep(before, count, step):
    """Send `before`, then fork `count` children that each send a doubled
    CMD_RESUME `step` cycles later than the previous one. Returns each child's
    (max, final) stop_status, keyed by that delay."""
    send = before + fw.double([fw.CMD_RESUME])
    run = fw.BOOT_CYCLES + (len(send) + 10) * RX_GAP + count * step
    r = subprocess.run(
        [
            fw.harness(),
            fw.firmware_elf("config.driveboardusb.h"),
            "--send=" + ",".join(map(str, send)),
            f"--run-cycles={run}",
            "--watch-symbol=stop_status",
            f"--fork-at={len(before)}",
            f"--fork-count={count}",
            f"--fork-step={step}",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stderr
    results = {}
    for j, block in enumerate(r.stdout.split("FORK=")[1:]):
        sym = next(ln for ln in block.splitlines() if ln.startswith("SYM "))
        info = dict(tok.split("=") for tok in sym.split()[2:])
        results[j * step] = (int(info["max"]), int(info["final"]))
    assert len(results) == count, r.stdout[-500:]
    return results


def test_resume_while_idle_never_replays_stale_bytes():
    # Three no-op commands leave tail == head == 3; the idle loop spins on the
    # empty check, one pass taking ~107 cycles.
    before = fw.double([CMD_NONE] * 3)
    hit = [d for d, (mx, _) in _resume_sweep(before, 240, 1).items() if mx != fw.STOPERROR_OK]
    assert hit == [], f"resume at cycles {hit} replayed stale rx bytes"


def test_resume_while_draining_stopped_backlog_runs_no_stale_byte():
    # A relative offset waits for the slow move to finish, so the Z commands
    # pile up in the rx buffer. The stop ends the wait and the loop drains
    # them while stopped, one per pass of ~3400 cycles, from ~9000 cycles past
    # the earliest resume. A Z that runs stops '<'.
    before = (
        fw.line_program(50, feedrate=300)
        + fw.double([CMD_REF_RELATIVE] + E("h", 0) + [CMD_INVALID] * 100)
        + fw.double([fw.CMD_STOP])
    )
    sweep = _resume_sweep(before, 1600, 8)
    hit = [d for d, (_, final) in sweep.items() if final != fw.STOPERROR_OK]
    assert hit == [], f"resume at cycles {hit} ran a byte read while stopped"
