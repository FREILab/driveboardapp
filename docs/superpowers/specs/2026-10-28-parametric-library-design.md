# Parametric library jobs

## Why

Library jobs are fixed. `Test_PowerQuadrate` always cuts 2000 to 1500 mm/min at
100 %, and its engraved labels say so. Testing a different range means drawing
a new file. The labels are stored as outlined paths in the .dba, so they cannot
change at all once a job is saved.

Goal: a library job can carry a template with parameters. Loading it asks for
the values (prefilled with defaults), and labels, cut settings and dimensions
follow those values. Every member at FREILab uses this, so input is checked.

## What the operator sees

- The library list looks as it does today. All entries are `.dba`.
- A plain .dba loads as today, with no dialog.
- A .dba with a template opens a dialog when you click it. There is one field
  per parameter: its label, the default value, and the allowed range. **OK**
  generates the job and loads it like any other job. **Cancel** loads nothing.
- Values outside the range are refused with a message that names the field and
  the range.
- The rest stays as it is: `power_speed_limit` refuses the run if any pass is
  over the limit (the whole job is refused, there is no per-cell skipping), and
  billing runs as for any job.

## Format: an extension of .dba

The template is an optional field. The job itself is complete, generated with
the defaults, so older software that ignores the field runs the job with the
defaults.

```json
{
  "head": {...}, "passes": [...], "items": [...], "defs": [...],
  "template": {
    "params": [
      {"name": "vmin", "default": 1500, "min": 100, "max": 6000, "label": "Vorschub min"}
    ],
    "svg": "<svg ...> ... {{vmin}} ... </svg>"
  }
}
```

## Authoring a template

1. Draw it in Inkscape.
2. Declare each parameter as a text element:
   `=param:<name>:<default>:<min>:<max>:<label>=`. The label comes last so it
   may contain spaces. Colons are not allowed in it. Like pass tags, these text
   elements are neither cut nor outlined.
3. Put `{{ expression }}` wherever a value goes: in visible text, in pass tags
   (`=pass3:{{round(v3)}}:{{p1}}:#00ff00=`), and in any attribute, including
   geometry (`height="{{d}}"`, path data, transforms).
4. Open the SVG in the app as usual. The import finds the `=param:` tags,
   renders the template with the defaults, converts it as a normal SVG, and
   attaches `template` (the raw SVG and the parsed params) to the job.
5. Export. The downloaded .dba contains `template`. Put it in the library.

The source SVG is kept in the repo next to the library, e.g.
`freilab/library/src/Test_PowerQuadrate.svg`, so it can be edited again in
Inkscape. Extracting the SVG back out of a .dba is not needed.

Inkscape may rewrite geometry attributes (path data, transforms) on save and
lose a placeholder there. That is accepted: placeholders in geometry are set
or checked in a text editor after the last Inkscape save. The authoring guide
says so.

The number of elements is fixed by the template. Templates have no loops and no
conditionals. A different grid size means a different template.

## Expressions (security-relevant)

Any member can import an SVG, so a template is **untrusted input**. Expressions
must not run arbitrary Python (`eval`, `bottle.SimpleTemplate` and similar are
not acceptable).

Allowed:

- number literals and parameter names
- `+ - * / //`, unary minus, and parentheses
- `round`, `min`, `max` and `abs`

Anything else is refused, and so are absurd results such as huge numbers. An
AST walk over the stdlib `ast` module is enough for this.

Output formatting: an integral result is written as an integer (`1600`, not
`1600.0`). Other results get at most 3 decimals.

A broken expression, an unknown name, or a malformed `=param:` tag fails the
import with a clear message in the existing import issues dialog. A pass tag
that no longer matches after rendering (the pass tag reader accepts integers
only, `svg_tag_reader.py:46`) must not be dropped silently either.

## Where things change (pointers, not a design)

- Backend: `jobimport/converter.py` `read_svg()` and `svg_reader.py`. Rendering
  happens on the raw SVG string before `convert_text_to_paths()`. The
  `_re_lasertag` exclusion in `svg_text_converter.py` must also cover
  `=param:`.
- A backend entry point takes a library job name plus values, validates the
  values against min/max (this is the trust boundary, not the dialog), renders,
  converts, and returns the job. The returned job keeps `template`.
- The entry point reads library files through `web._get`/`_get_path`, so the
  FREILab override in `freilab/run.py` also finds `freilab/library/`.
- A plain library job still only shows in the editor. A generated job goes
  through `/load` like an imported file, so it gets the import issues dialog
  and lands in the queue. (Builder's call: this reuses the existing path.)
- Templates use `round()` both in the pass tag and in the engraved label, so
  the label never disagrees with the actual cut setting.
- Frontend: `library.js` (click → dialog when `template` is present), and
  `jobhandler.js` `set()`/`getJson()`. Both currently copy only known fields,
  so `template` would be lost on export (`jobhandler.js:123`, `:276`).

## Branches

- The mechanism goes to `JS-DEV`, so it can go upstream to Scott. It has no
  FREILab specifics.
- The FREILab templates and the library changes go to `JS-DEV-Freilab`, in
  `freilab/library/`.

## FREILab library changes

| Old | New | Parameters |
|---|---|---|
| `Test_PowerQuadrate.dba` | `Test_PowerQuadrate.dba` with template | Feed from–to |
| `Test_6Linien.dba` | `Test_6Linien.dba` with template | Speed from–to, power |
| `Parts_Fokus9mm.dba` | `Parts_FixFocus.dba` with template | Thickness `d` |
| `Parts_Fokushilfe_3-30mm.dba` | `Parts_FocusStairs_3-30mm.dba` | None, renamed only |

The old files are removed. Source SVGs go into `freilab/library/src/`.

- **Test_PowerQuadrate:** the same layout as today: one row of 6 squares of
  10 mm at 100 % power, each labelled with its feed rate, inside and below
  (today 2000 to 1500). Parameters: feed min/max only, spread evenly over the 6
  squares, with defaults 1500–2000. Power stays fixed at 100 %. The labels are
  engraved at 2000/15 as today, also fixed.
- **Test_6Linien:** 6 lines of 5 mm, 3 mm apart, cut across the workpiece
  edge. The instruction text stays as an unassigned layer, as today, but now
  also lists the value for each colour. Parameters: feed min/max and power.
- **Parts_FixFocus:** the disc (Ø 32 mm, hole, slot) is unchanged, and so is
  the slot length. The label strip is `d` mm high (today 9 mm) and reads
  `Fix Focus {{d}} mm`. `d` ranges from 3 to 20 mm, default 9. The text size
  scales with `d` (capped at today's size), so it fits the strip down to 3 mm.
  There are no cut settings, as today.

## Non-goals

- loops or conditionals in templates
- saving parameter sets (as presets do)
- uploading templates through the UI
- extracting the SVG from a .dba
- skipping single grid cells that exceed `power_speed_limit`

## Calls made without the user (check these)

- the safe expression subset, and integer-or-3-decimal output
- `src/` for the source SVGs
- FixFocus text size scaling with `d`
- the doc is written in English, like the other specs
