# FREILab-Overlay

Alles FREILab-Eigene liegt hier. Die Upstream-Dateien (`frontend/`, `backend/`, `firmware/`) bleiben unverändert,
deshalb lässt sich `scottshambaugh/driveboardapp` ohne Konflikte nachziehen.

Starten statt `backend/app.py`:

    python freilab/run.py        # gleiche Argumente wie backend/app.py

## Was drin ist

| Datei | Zweck |
|---|---|
| `run.py` | Startet die App und hängt `web/` in die Auslieferung der UI ein (Dateien in `web/` gewinnen, `app.html` bekommt Skript, CSS und Dialoge) |
| `web/freilab.js` | Material-/Billing-Dialoge, Kontostand. Hängt sich vor den Run-Button (Upstreams eigener Handler startet den Job nach der Abrechnung) und vor den Homing-Button (Offset-Reset vorher) |
| `web/freilab_modals.html` | Die Dialoge |
| `web/freilab.css` | Styles dazu |
| `web/img/material/` | Material-Icons |
| `library/` | Unsere Jobs, `run.py` listet und lädt sie zusätzlich zu `library/` von Upstream |
| `firmware-homing.patch` | Homing-Rückfahrstrecken, nur beim Firmware-Bau anwenden (siehe unten) |
| `test_overlay.py` | `pytest freilab/test_overlay.py` |


## Maschinen-Einstellungen (nicht im Repo)

`~/.driveboardapp/config.json`: `"raster_mode": "Forward"` (Upstream-Default ist `Bidirectional`).

## Firmware

    git apply freilab/firmware-homing.patch   # dann Firmware wie upstream bauen und flashen

## Upstream nachziehen

    git fetch scott && git checkout master && git merge --ff-only scott/main   # master = reiner Upstream-Stand
    git checkout JS-Dev && git merge master

## Wenn das Overlay bricht

Es greift an zwei Stellen in Upstream ein: `web._frontend_file()` (`backend/web.py`) und die Handler von
`#run_btn` / `#homing_btn` sowie `controls_ready()` (`frontend/`). Ändert Upstream die, `run.py` bzw. `freilab.js` anpassen.
