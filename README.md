# DriveboardApp

[![CI](https://github.com/FREILab/driveboardapp/actions/workflows/ci.yml/badge.svg)](https://github.com/FREILab/driveboardapp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)

Control software for Driveboard-based laser cutters like the [Lasersaur](http://lasersaur.com).
Load SVG, DXF or DBA files in the browser, assign cut and engrave settings per
colour, and run the job on the machine.

This is a maintained fork of [nortd/driveboardapp](https://github.com/nortd/driveboardapp),
which is no longer developed. See [History](#history) and the
[changelog](./CHANGELOG.md) for what changed.

The app has three parts:

| Part | Language | Runs on |
|---|---|---|
| Frontend | JavaScript | any web browser, also tablets |
| Backend | Python 3 | the Driveboard itself or a computer attached to it |
| Firmware | C (AVR) | the Driveboard controller |

## Features

- **Import** of SVG (including text, fills, clones, patterns and embedded
  raster images), DXF and DBA jobs.
- **Import issues dialog:** problems in a file are shown before the job is
  queued. Each row says what the app does about the problem and how to fix
  it in the source file. You choose *Load anyway* or *Cancel*. Jobs that fall
  outside the work area are flagged too.
- **Passes:** feed rate, intensity, pierce time and air/aux assist per pass,
  with savable material presets.
- **Fills** for closed paths, nested so text and holes come out right, with
  Forward, Reverse, Bidirectional and NearestNeighbor modes.
- **Raster engraving** with skipping of blank areas, inverting and selectable
  grey levels (2-3 for dithering, up to 128).
- **Path optimization** that minimises seek time, using the firmware's real
  acceleration profile.
- **Machine control:** status, pause, resume, stop, jog, offsets, homing and
  a short *pulse* for alignment (all safety interlocks stay active).
- **Safety checks** before anything moves: work area, power/speed limit,
  stop handling, and a log of unexplained stops at `/incidents`.
- **Firmware flashing** from the app.

## Quick start

Requires Python 3.9 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/FREILab/driveboardapp.git
cd driveboardapp
uv sync
uv run python backend/app.py
```

The app opens in your browser at `http://127.0.0.1:4444/`. Use
`--nobrowser` to skip that and `--cli` to run without the server window.
Other ways to install, including pip, are in [docs/install.md](docs/install.md).

## Configuration

Settings live in `config.json` in the config directory (`~/.driveboardapp`
on Linux; the app prints its path on start). The app writes it with defaults
on first start, and most settings can also be edited in the UI. Pass
`--config NAME` to use `config.NAME.json` instead. Every option is
described in [docs/configure.md](docs/configure.md).

`mill_mode` switches to the separate mill frontend. With it off (the
default for a laser), G-code files and mill jobs are refused, because a
laser would run the spindle speed as beam power.

## Development

```bash
uv sync --dev
uv run pytest backend/tests                # Python backend
node --test "frontend/tests/*.test.js"     # frontend logic
uv run ruff check . && uv run ruff format --check .
```

The firmware tests compile the real firmware and run it in
[simavr](https://github.com/buserror/simavr). They skip themselves when the
AVR toolchain is missing. No Driveboard is needed for any test. Details are
in [docs/testing.md](docs/testing.md).

## Documentation

- [Installation](docs/install.md)
- [Configuration](docs/configure.md)
- [Frontend](docs/frontend.md)
- [Backend](docs/backend.md): [low-level API](docs/api_low.md),
  [high-level API](docs/api_high.md)
- [Firmware](docs/firmware.md): [serial protocol](docs/protocol.md)
- [DBA job file format](docs/dba.md)
- [Testing](docs/testing.md)

## Hardware

- [Driveboard](https://github.com/nortd/lasersaur/wiki/driveboard) from the
  [Lasersaur project](http://www.lasersaur.com)
- [MinimalDriveboard](docs/minimaldriveboard.md)

## History

The original DriveboardApp by NORTD Labs is no longer maintained. Main
changes in this fork:

- Ported from Python 2 to Python 3.
- Raster engraving rewritten, with support for PNG, JPEG, GIF, BMP, WebP,
  TIFF, AVIF and more embedded in SVG.
- SVG text and area fills imported as paths.
- Faster path and seek optimization.
- UI for material presets, config editing, fine jogging and offsets.
- Import issues dialog, work-area checks and many safety fixes in host and
  firmware.
- Automated test suite for backend, firmware (in a simulator) and frontend,
  run in GitHub Actions.

More background is in [this thread](https://groups.google.com/g/lasersaur/c/n54LNN86H-c)
and [this blog post](https://theshamblog.com/software-mods-to-the-lasersaur/).

Known issues:

- Building a Windows executable does not work at the moment.
- Parts of the documentation are out of date.

Thanks to scottshambaugh, FREILab, Johann150, makermusings, vanillasoap and
martinxyz for keeping this project alive.

## Disclaimer

Operating a laser cutter or other CNC machine is dangerous and requires full
awareness of the risks involved. This software comes without any warranty,
and neither NORTD Labs nor the contributors take on any risk from its use.
