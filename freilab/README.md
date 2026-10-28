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
| `firmware-homing.patch` | Homing-Rückfahrstrecken, nur beim Firmware-Bau anwenden (siehe unten) |
| `test_overlay.py` | `pytest freilab/test_overlay.py` (getrennt von `backend/tests`, das Overlay patcht das Backend global) |

## Maschinen-Einstellungen (nicht im Repo)

Werte aus `config.example.json` in `~/.driveboardapp/config.json` eintragen (oder im UI unter Config):

- `power_speed_limit: 0.5`: Run wird abgelehnt, wenn bei einem Pass Intensität (%) / Vorschub (mm/min) ≥ 0,5 ist. Die Prüfung läuft vor der Abrechnung.
- `homing_reset_offset: true`: Offset wird beim Homing sofort zurückgesetzt.
- `raster_mode: "Forward"` (Upstream-Default ist `Bidirectional`).

## Firmware

    git apply freilab/firmware-homing.patch   # dann Firmware wie upstream bauen und flashen

## Upstream nachziehen

    git fetch scott
    git checkout JS-DEV && git merge scott/main
    git checkout JS-DEV-Freilab && git merge JS-DEV

## Wenn das Overlay bricht

Es greift an diesen Stellen in Upstream ein: `web._frontend_file()`, `web._get_sorted()`, `web._get_path()`, `web._get()` (`backend/web.py`) sowie `controls_ready()`, den Handler von `#run_btn` und `jobhandler.passOverPowerSpeedLimit()` (`frontend/`). Ändert Upstream die, `run.py` bzw. `freilab.js` anpassen.
