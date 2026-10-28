
DriveboardApp File Format
=========================

The native file format has the extension .dba and is technically a json text file. It can save paths, fills, and images along with pass definition.

Simple Example
--------------
```
{
  "passes":
  [
    {
      "items":[0],
      "feedrate":400,
      "intensity":15
    }
  ],
  "items":[{"def":0}],
  "defs":[
    {"kind":"path", "data":[[[100,520],[105,520]]]}
  ]
}
```

Complete File Description
-------------------------

```
{
  "head": {                      # optional
      "noreturn": True,          # optional, do not return to origin, default: False
      "optimized": 0.08,         # optional, tolerance to which it was optimized, default: 0 (not optimized)
   },
  "passes": [                    # optional
      {
          "items": [0],          # item by index
          "relative": True,      # optional, default: False
          "seekrate": 6000,      # optional, rate to first vertex
          "feedrate": 2000,      # optional, rate to other vertices
          "intensity": 100,      # optional, default: 0 (in percent)
          "seekzero": False,     # optional, default: True
          "pierce_time": 0,      # optional, default: conf pierce_time
          "pxsize": 0.4,         # optional
          "air_assist": "pass",  # optional (off, feed, pass, job), default: pass
          "aux_assist": "off",   # optional (off, feed, pass, job), default: off
      }
  ],
 "items": [
    {"def":0, "translate":[0,0,0], "color":"#BADA55"}   # translate, color are optional
 ],
 "defs": [
    {"kind":"path", "data":[[[0,10,0]]]},               # data is a list of polylines
    {"kind":"fill", "data":[[[0,10,0]]], "pxsize":0.4},
    {"kind":"image", "data":<data in base64>, "pos":[0,0], "size":[300,200], "source":<hash of pre-clip data>},  # source is optional
 ],
 "sources": {<hash>: <pre-clip data in base64>},   # optional, original data of clipped images for previews
 "stats":{"items":[{"bbox":[x1,y1,x2,y2], "len":100}], "all":{}},  # optional
 "template": {                  # optional, a parametric job, see below
    "params": [{"name": "v", "default": 1500, "min": 100, "max": 6000, "label": "Feed"}],
    "svg": "<svg ...> ... {{v}} ... </svg>"       # the source svg with its placeholders
 }
}
```

Parametric templates
--------------------

A job with a `template` can be generated again with other values. The job itself
is complete, generated with the defaults, so software that ignores the field
runs the job with the defaults. The values are checked against min and max; a
value outside is refused with a message naming the field and the range.

### Authoring a template

1. Draw it in Inkscape.
2. Declare each parameter as a text element:
   `=param:<name>:<default>:<min>:<max>:<label>=`. The label comes last so it
   may contain spaces, but neither `:` nor `=`. Like pass tags, these text
   elements are neither cut nor outlined.
3. Put `{{ expression }}` wherever a value goes: in visible text, in pass tags
   (`=pass3:{{round(v3)}}:{{round(p1)}}:#00ff00=`), and in any attribute, including
   geometry (`height="{{d}}"`, path data, transforms).
4. Open the SVG in the app as usual. The import finds the `=param:` tags,
   renders the template with the defaults, converts it as a normal SVG, and
   attaches `template` (the raw SVG and the parsed params) to the job.
5. Export. The downloaded .dba contains `template`. Put it in the library.

Keep the source SVG next to the library so it can be edited again in Inkscape.

Inkscape may rewrite geometry attributes (path data, transforms) on save and
lose a placeholder there. Set or check placeholders in geometry in a text editor
after the last Inkscape save.

Limits to keep in mind:

- Text attributes (`fill`, `font-family`, `font-size`, `text-anchor`) are not
  inherited from a parent `<g>` or `<svg>`. Set them on each `<text>`.
- A placeholder must sit in one text span or attribute. One split across
  `<tspan>`s is refused as "Placeholder not closed".
- Values in pass tags must be integers, so wrap them in `round()`.
- Results are limited to ±1e6 and are written as an integer or with at most 3
  decimals.
- Edits made in the app after an import (pass assignment, fills) are lost when
  the .dba is generated again from the SVG.

The number of elements is fixed by the template: there are no loops and no
conditionals. A different grid size means a different template.

Expressions use numbers, parameter names, `+ - * / //`, parentheses and
`round`, `min`, `max`, `abs`. Nothing else is evaluated.
