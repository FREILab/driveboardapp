# FREILab-Overlay

Alles FREILab-Eigene liegt hier. Die Upstream-Dateien (`frontend/`, `backend/`, `firmware/`) bleiben unverändert.

Starten statt `backend/app.py`:

    python freilab/run.py        # gleiche Argumente wie backend/app.py

## Branches

- **`JS-DEV`**: Scotts `main` plus alle Änderungen, die in den Standard zurückfließen sollen (z. B. `power_speed_limit`, `homing_reset_offset`). Kein `freilab/`.
- **`JS-DEV-Freilab`**: `JS-DEV` plus dieser Ordner.

Änderungen am Standard gehören nach `JS-DEV` und werden von dort in `JS-DEV-Freilab` gemerged, nie umgekehrt.

## Was drin ist

| Datei | Zweck |
|---|---|
| `run.py` | Startet die App und hängt `web/` und `library/` ein (Dateien in `web/` gewinnen, `app.html` bekommt Skript, CSS und Dialoge; die Library-Liste enthält auch `freilab/library/`) |
| `web/freilab.js` | Material-/Billing-Dialoge, Kontostand. Hängt sich vor den Run-Button: erst Prüfungen, Material, Abrechnung, dann startet Upstreams eigener Handler den Job |
| `web/freilab_modals.html` | Die Dialoge |
| `web/freilab.css` | Styles dazu |
| `web/img/material/` | Material-Icons |
| `library/` | Unsere Jobs, zusätzlich zu `library/` von Upstream. `Test_PowerQuadrate` (nur Vorschub), `Test_6Linien` (Vorschub und Leistung) und `Parts_FixFocus` (Dicke) sind Vorlagen, deren Werte beim Laden einstellbar sind; ihre SVG-Quellen liegen in `library/src/`. `Parts_FocusStairs_3-30mm` ist ein fester Job |
| `config.example.json` | Unsere Maschinen-Einstellungen |
| `firmware-homing.patch` | FREILab-Homing-Rückfahrstrecken für `firmware/src/stepper.c`, nur beim Firmware-Bau anwenden (siehe Firmware) |
| `firmware.driveboardusb.hex` | Fertige FREILab-Firmware, so wie sie auf der Maschine läuft: Profil **`driveboardusb`** plus `firmware-homing.patch` (siehe Firmware) |
| `test_overlay.py` | `pytest freilab/test_overlay.py` (getrennt von `backend/tests`, das Overlay patcht das Backend global) |

## Maschinen-Einstellungen (nicht im Repo)

Werte aus `config.example.json` in `~/.driveboardapp/config.json` eintragen (oder im UI unter Config):

- `power_speed_limit: 0.5`: Run wird abgelehnt, wenn bei einem Pass Intensität (%) / Vorschub (mm/min) ≥ 0,5 ist. Die Prüfung läuft vor der Abrechnung.
- `homing_reset_offset: true`: Offset wird beim Homing sofort zurückgesetzt.
- `raster_mode: "Forward"` (Upstream-Default ist `Bidirectional`).

## Firmware

### Profil: `driveboardusb`, nicht `driveboard1403`

**Unsere Steuerung läuft mit dem Profil `driveboardusb`** (`firmware/src/config.driveboardusb.h`, Hex `firmware/firmware.driveboardusb.hex`). `driveboard1403` ist ein anderes Board.

Die Profile (`firmware/src/config.*.h`) legen Pins und Polarität fest und unterscheiden sich an diesen Stellen:

| | `driveboardusb` (unser Profil) | `driveboard1403` |
|---|---|---|
| Sensoren (Endschalter, Tür, Kühler) | invertiert (`SENSE_INVERT`) | nicht invertiert |
| Achsrichtung X / Y (`CONFIG_INVERT_*_AXIS`) | 1 / 0 | 0 / 1 |
| Laser-PWM | PD5, `SYNCED_FREQ` | PD6, `STEPPED_FREQ_PD6` |
| Aux-Ausgang (LED) | PD6 | PD7 |
| Tür-Eingänge | PD2 und PD7 | PD2 |

Mit `driveboard1403` auf unserer Maschine sind Endschalter, Tür und Kühler falsch herum, X und Y laufen (auch beim Homing) in die falsche Richtung, und Laser und LED bleiben aus. Außerdem wird PD7, unser zweiter Tür-Eingang, als Ausgang betrieben.

**Welches Profil die App flasht:** sie nimmt `conf["firmware"]` (auch der Knopf *Flash Firmware*). Bei `hardware = standard` ist das `driveboardusb`, bei `beaglebone` und `raspberrypi` dagegen `driveboard1403`. Die Einstellung `firmware` in der `config.json` wird nicht gelesen. Die App muss deshalb als `standard` erkannt werden. Auf Linux-ARM ohne `RPi.GPIO` (z. B. ein Raspberry Pi mit piCore) hält sie die Maschine sonst für ein BeagleBone, dort `DRIVEBOARD_HARDWARE=standard` setzen.

### Homing-Rückfahrstrecken

`firmware-homing.patch` stellt die Rückfahrstrecken beim Homing wieder auf unsere Werte (X 8 mm / 2 mm, Y 7 mm / 3 mm statt je 10 mm / 4 mm), damit der Nullpunkt dort liegt, wo er bei der bisherigen Firmware lag. Ohne den Patch sitzt er 2 mm (X) und 1 mm (Y) anders. Das Hex von Upstream (`firmware/firmware.driveboardusb.hex`) enthält den Patch nicht.

### Flashen

Fertig gebaut, mit Profil `driveboardusb` und Patch (gebaut mit avr-gcc 7.3.0 wie die Upstream-Hex):

    avrdude -c arduino -b 115200 -P /dev/ttyACM0 -p atmega328p -Uflash:w:freilab/firmware.driveboardusb.hex:i

Vorher die App beenden (sie hält den seriellen Port). Danach ist die Maschine nicht referenziert, also neu homen.

### Selbst bauen

    git apply freilab/firmware-homing.patch   # dann in der App *Rebuild Firmware* (oder backend/build.py), Ergebnis: firmware/firmware.driveboardusb.hex

Den Patch nicht committen, `firmware/` bleibt Upstream. Ändert sich `firmware/src/` durch ein Upstream-Merge, den Patch prüfen und `freilab/firmware.driveboardusb.hex` neu bauen.

## Upstream nachziehen

    git fetch scott
    git checkout JS-DEV && git merge scott/main
    git checkout JS-DEV-Freilab && git merge JS-DEV

## Wenn das Overlay bricht

Es greift an diesen Stellen in Upstream ein: `web._frontend_file()`, `web._get_sorted()`, `web._get_path()`, `web._get()` (`backend/web.py`) sowie `controls_ready()`, den Handler von `#run_btn` und `jobhandler.passOverPowerSpeedLimit()` (`frontend/`). Ändert Upstream die, `run.py` bzw. `freilab.js` anpassen.
