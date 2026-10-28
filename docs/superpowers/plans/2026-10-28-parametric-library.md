# Parametric Library Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A .dba can carry an SVG template with parameters. Loading it from the library asks for the values, and labels, pass settings and geometry follow them.

**Architecture:** A new stdlib-only module `backend/jobimport/template.py` parses `=param:` tags, evaluates `{{ }}` with a whitelisted AST evaluator and renders the SVG. `converter.read_svg` renders before parsing and attaches `job["template"]`. `/load` passes optional `params` through to the convert worker. In the frontend, a library job with `template` opens a dialog that posts the template SVG plus values to `/load`, using the normal import path.

**Tech Stack:** Python 3 (stdlib `ast`, `re`), bottle, jQuery/Bootstrap 3, node:test for the JS units, pytest + webtest.

**Spec:** `docs/superpowers/specs/2026-10-28-parametric-library-design.md`

## Global Constraints

- Tasks 1–3 go to branch `JS-DEV`: generic, no `freilab/` paths, upstream-ready. Tasks 4–5 go to `JS-DEV-Freilab` after `git merge JS-DEV`.
- Tag syntax: `=param:<name>:<default>:<min>:<max>:<label>=`. The label comes last and may contain spaces but no colons.
- Expressions: number literals, parameter names, `+ - * / //`, unary `-`/`+`, parentheses, `round`, `min`, `max`, `abs`. Nothing else. Never `eval`, `exec` or `SimpleTemplate`.
- Output: an integral result is written as an integer (`1600`). Otherwise at most 3 decimals, with trailing zeros stripped.
- Old .dba files without `template` load exactly as before.
- No new dependencies.
- Commit messages: no `Co-Authored-By`, no "Generated with" line, no Claude/Anthropic attribution.
- Tests: `uv run pytest backend/tests` (docs/testing.md). If `uv` is missing, run `.venv/bin/pip install pytest webtest` once and use `.venv/bin/python -m pytest`. Overlay tests: `pytest freilab/test_overlay.py`.

## Review Focus

1. **Placeholder split by Inkscape across tspans** (`{{v` … `1}}`). Must fail the import, not engrave literal braces. Test: `test_render_rejects_leftover_braces` (Task 1).
2. **Non-integer value in a pass tag** (`=pass1:{{v/3}}:…=`). Must fail with a hint to use `round()`, not drop the pass silently. Test: `test_render_rejects_unreadable_pass_tag` (Task 1).
3. **Hostile expression in an uploaded SVG** (`__import__`, attribute access, `9**9**9`, a huge result). Must be refused. Test: `test_safe_eval_refuses_everything_else` (Task 1).
4. **Label from the SVG with HTML in it** (`<img onerror=…>`). Must render as text in the dialog. Test: `template_form_html escapes labels` (Task 3).
5. **Plain SVG and plain .dba imports**. These must be unchanged, with no `template` key added. Tests: `test_plain_svg_has_no_template` (Task 2) and `job without template has no template key` (Task 3).

---

### Task 1: Template engine

**Files:**
- Create: `backend/jobimport/template.py`
- Test: `backend/tests/test_template.py`

**Interfaces:**
- Produces:
  - `class TemplateError(issues.ImportFailed)`, raised as `TemplateError(problem, fix, where)`
  - `has_params(svg: str) -> bool`, which is `"=param:" in svg`
  - `parse_params(svg: str) -> list[dict]`. Each dict has `{"name", "default", "min", "max", "label"}`, numbers as int when integral, else float. Order is as in the document.
  - `resolve_values(params: list[dict], values: dict | None) -> dict[str, int | float]`
  - `safe_eval(expr: str, names: dict) -> int | float`
  - `format_number(x: int | float) -> str`
  - `render(svg: str, values: dict) -> str`

- [ ] **Step 0 (once):** make pytest available (see Global Constraints), then `git checkout JS-DEV`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_template.py`

```python
import pytest
from jobimport import template
from jobimport.template import TemplateError

SVG = '<svg><text>=param:vmin:1500:100:6000:Vorschub min=</text><text>=param:p:100:1:100:Power %=</text></svg>'

def test_parse_params():
    assert template.parse_params(SVG) == [
        {"name": "vmin", "default": 1500, "min": 100, "max": 6000, "label": "Vorschub min"},
        {"name": "p", "default": 100, "min": 1, "max": 100, "label": "Power %"},
    ]

@pytest.mark.parametrize("bad", [
    "=param:v:1:0=",                 # too few fields
    "=param:v:x:0:10:L=",            # not a number
    "=param:v:20:0:10:L=",           # default outside range
    "=param:9v:1:0:10:L=",           # not a name
    "=param:v:1:0:10:A==param:v:2:0:10:B=",  # duplicate name
])
def test_parse_params_rejects(bad):
    with pytest.raises(TemplateError):
        template.parse_params(bad)

def test_resolve_values_defaults_and_overrides():
    params = template.parse_params(SVG)
    assert template.resolve_values(params, None) == {"vmin": 1500, "p": 100}
    assert template.resolve_values(params, {"vmin": 1200}) == {"vmin": 1200, "p": 100}

@pytest.mark.parametrize("values", [{"vmin": 99}, {"vmin": 6001}, {"vmin": "fast"}, {"vmin": True}, {"nope": 1}])
def test_resolve_values_rejects(values):
    with pytest.raises(TemplateError) as e:
        template.resolve_values(template.parse_params(SVG), values)
    assert e.value.issue["fix"]  # names the allowed range or the bad name

@pytest.mark.parametrize("expr,expected", [
    ("v", 1500), ("v + 2*3", 1506), ("(v - 500) / 4", 250), ("v // 7", 214),
    ("-v", -1500), ("round(v / 7)", 214), ("round(v / 7, 1)", 214.3),
    ("min(v, 10)", 10), ("max(v, 2000)", 2000), ("abs(-3)", 3),
])
def test_safe_eval(expr, expected):
    assert template.safe_eval(expr, {"v": 1500}) == expected

@pytest.mark.parametrize("expr", [
    "__import__('os')", "v.real", "(1).__class__", "[1]", "v ** 2", "9**9**9",
    "open('x')", "lambda: 1", "v if v else 1", "x", "1/0", "10000000 * 1000", "round(v, n=1)",
])
def test_safe_eval_refuses_everything_else(expr):
    with pytest.raises(TemplateError):
        template.safe_eval(expr, {"v": 1500})

@pytest.mark.parametrize("x,s", [(1600, "1600"), (1600.0, "1600"), (2.5, "2.5"), (1/3, "0.333"), (-0.1, "-0.1")])
def test_format_number(x, s):
    assert template.format_number(x) == s

def test_render_substitutes_text_attributes_and_pass_tags():
    svg = '<rect height="{{ d }}"/><text>{{v}} mm/min</text><text>=pass1:{{round(v/3)}}:100:#ff0000=</text>'
    assert template.render(svg, {"v": 1500, "d": 4.5}) == (
        '<rect height="4.5"/><text>1500 mm/min</text><text>=pass1:500:100:#ff0000=</text>')

def test_render_rejects_leftover_braces():
    with pytest.raises(TemplateError):
        template.render("<text><tspan>{{v</tspan><tspan>}}</tspan></text>", {"v": 1})

def test_render_rejects_unreadable_pass_tag():
    with pytest.raises(TemplateError) as e:
        template.render("<text>=pass1:{{v/3}}:100:#ff0000=</text>", {"v": 1000})
    assert "round" in e.value.issue["fix"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest backend/tests/test_template.py -q`. Expected: collection error, `cannot import name 'template'`.

- [ ] **Step 3: Implement `backend/jobimport/template.py`**

- **`safe_eval`:** `ast.parse(expr.strip(), mode="eval")`, then a recursive walk.
  - Allowed: `Expression`, `Constant` (int/float, not bool), `Name` (must be in `names`), `UnaryOp` (`USub`, `UAdd`), `BinOp` (`Add`, `Sub`, `Mult`, `Div`, `FloorDiv`), and `Call` where `func` is a `Name` in `{"round", "min", "max", "abs"}`, with no keywords and positional arguments only.
  - Everything else raises `TemplateError`, and so do `SyntaxError` and `ZeroDivisionError`.
  - Results with `abs(result) > 1e6` raise as well. Check that after every operation, so `10000000 * 1000` fails.
  - The error carries `where=expr`.
- **`parse_params`:**
  - Scan with `re.finditer(r"=param:([^=]*)=", svg)`.
  - Split each match on `:`. It must give exactly 5 parts.
  - The name must match `[A-Za-z_]\w*`. Default, min and max must be numbers with `min <= default <= max`.
  - Duplicate names are an error.
- **`resolve_values`:**
  - Unknown names are an error (fix: `"Remove <name>"`).
  - A value that is not int/float, or is a bool, is an error.
  - A value outside `[min, max]` is an error. Problem: `"<label> out of range"`. Fix: `"Use <min>–<max>"`.
  - Missing names take their default.
- **`render`:**
  - Replace `re.sub(r"\{\{(.*?)\}\}", …)` with `format_number(safe_eval(...))`.
  - Afterwards, any remaining `{{` or `}}` raises (`"Placeholder not closed"`, fix `"Keep {{ … }} in one text span"`).
  - Then every `=pass\d+:[^=]*=` must fullmatch `=pass\d+:\d*(mm/min)?:\d*%?(:#[0-9a-fA-F]{6}){0,6}=`. If one doesn't, raise with the fix `"Use round() in pass tags"`.
- **Messages** are short English, like the existing issue rows.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest backend/tests/test_template.py -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/jobimport/template.py backend/tests/test_template.py
git commit -m "Add a safe template engine for parametric SVG jobs"
```

---

### Task 2: Import and /load wiring

**Files:**
- Modify: `backend/jobimport/converter.py` (`convert()` at :22, `read_svg()` at :136)
- Modify: `backend/jobimport/svg_text_converter.py:32` (`_re_lasertag`)
- Modify: `backend/web.py` (`_convert_worker` :680, `_convert_job` :802, `load()` :865)
- Modify: `docs/dba.md` (document `template`, plus a short authoring section)
- Modify: `backend/tests/helpers.py` (add `TEMPLATE_SVG`, shared by both test files)
- Test: `backend/tests/test_jobimport.py`, `backend/tests/test_web.py`

**Interfaces:**
- Consumes: Task 1 `has_params`, `parse_params`, `resolve_values`, `render`
- Produces:
  - `convert(job, optimize=True, tolerance=..., matrix=None, params=None)` and `read_svg(svg_string, tolerance, forced_dpi=None, optimize=True, params=None)`
  - The returned job has `job["template"] == {"params": parse_params(svg), "svg": <original svg str>}` whenever the SVG contains `=param:`.
  - `/load` accepts an optional `"params": {name: number}` in `load_request`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/helpers.py` (imported by both tests as `from helpers import TEMPLATE_SVG`), then the tests in `backend/tests/test_jobimport.py`:

```python
TEMPLATE_SVG = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="20mm" viewBox="0 0 40 20">
  <text x="0" y="19" font-size="2">=param:v:1500:100:6000:Feed=</text>
  <text x="0" y="17" font-size="2">=pass1:{{round(v)}}:100:#ff0000=</text>
  <rect x="0" y="0" width="{{v/150}}" height="5" fill="none" stroke="#ff0000"/>
</svg>"""

def test_template_svg_renders_defaults_and_keeps_template():
    job = jobimport.convert(TEMPLATE_SVG, optimize=False)
    assert job["passes"][0]["feedrate"] == 1500
    assert job["template"]["svg"] == TEMPLATE_SVG
    assert job["template"]["params"][0]["name"] == "v"
    # the =param: text is neither outlined nor cut: only the red rect is an item
    assert [i.get("color") for i in job["items"]] == ["#ff0000"]

def test_template_svg_takes_params():
    job = jobimport.convert(TEMPLATE_SVG, optimize=False, params={"v": 1200})
    assert job["passes"][0]["feedrate"] == 1200
    xs = [p[0] for path in job["defs"][0]["data"] for p in path]
    assert max(xs) - min(xs) == pytest.approx(8.0)  # width 1200/150

def test_template_svg_bad_param_fails_import():
    with pytest.raises(issues.ImportFailed):
        jobimport.convert(TEMPLATE_SVG, optimize=False, params={"v": 1})

def test_plain_svg_has_no_template(testjobs_dir):
    with open(os.path.join(testjobs_dir, "full-bed.svg")) as fp:
        assert "template" not in jobimport.convert(fp.read(), optimize=False)
```

(Add whatever imports and fixtures the file is missing. `testjobs_dir` is the existing fixture used in `test_web.py`.)

In `backend/tests/test_web.py`, under the `/load` section:

```python
def test_load_template_with_params(auth_app, isolated_config):
    from helpers import TEMPLATE_SVG
    req = {"job": TEMPLATE_SVG, "name": "tpl", "optimize": False, "params": {"v": 1200}}
    resp = auth_app.post("/load", {"load_request": json.dumps(req)})
    job = _stored_job(isolated_config, json.loads(resp.body))
    assert job["passes"][0]["feedrate"] == 1200 and "template" in job

def test_load_template_out_of_range_is_fatal(auth_app, isolated_config):
    from helpers import TEMPLATE_SVG
    req = {"job": TEMPLATE_SVG, "name": "tpl", "review_issues": True, "params": {"v": 1}}
    resp = auth_app.post("/load", {"load_request": json.dumps(req)}, expect_errors=True)
    assert resp.status_int == 422
    assert json.loads(resp.body)["fatal"]["problem"] == "Feed out of range"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest backend/tests/test_jobimport.py backend/tests/test_web.py -q -k "template"`. Expected: FAIL (`unexpected keyword argument 'params'`, missing `template`).

- [ ] **Step 3: Implement**

- `read_svg`:
  - Decode bytes to str first, since the input may arrive as bytes.
  - If `template.has_params(svg)`: `decl = parse_params`, `values = resolve_values(decl, params)`, and parse `render(svg, values)` instead.
  - After building the job, set `job["template"] = {"params": decl, "svg": svg}`, where `svg` is the original string.
- `convert` passes `params` only to `read_svg`. Other types ignore it.
- `_re_lasertag`: change it to `re.compile(r"=(?:pass[0-9]+|param):")` and update its comment.
- `web.py`:
  - `load()` reads `params = load_request.get("params")`.
  - Thread it through `_convert_job(job, optimize, matrix, progressive, params=None)` into the args tuple and `_convert_worker(..., conf_overrides, params=None)`, then into both `jobimport.convert(...)` calls.
- `docs/dba.md`:
  - Add the optional `template` field (shape as in the spec).
  - Add an "Authoring a template" section with the 5 steps from the spec, including the text-editor note for geometry placeholders after Inkscape.

- [ ] **Step 4: Run them to verify they pass, then the full suite**

Run: `uv run pytest backend/tests -q`. Expected: all pass. Existing tests are unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/jobimport/converter.py backend/jobimport/svg_text_converter.py backend/web.py docs/dba.md backend/tests/test_jobimport.py backend/tests/test_web.py
git commit -m "Render parametric SVG templates on import, accept params in /load"
```

---

### Task 3: Frontend dialog

**Files:**
- Create: `frontend/template.js`
- Modify: `frontend/app.html` (script tag next to `presets.js` at :56; `#template_modal` next to `#presets_modal` at :338, same Bootstrap markup)
- Modify: `frontend/import.js` (`import_open()` at :215)
- Modify: `frontend/jobhandler.js` (`clear()` :50, `set()` :123, `get()` :276)
- Test: `frontend/tests/template.test.js`, `frontend/tests/jobhandler.test.js`

**Interfaces:**
- Consumes: the `/load` `params` field (Task 2), `import_load_answer`/`import_load_failed` (`import.js`), `issues_escape` (`issues.js:6`)
- Produces:
  - `template_form_html(params) -> string`: one row per param, with the escaped label, `<input type="number" class="form-control template_param" data-name="…" min max value=default step="any">` and the hint `min–max`.
  - `template_check_values(params, raw) -> {values: {name: number}} | {error: string}`. Here `raw` is `{name: string}`, and the error text is `"<label>: use <min>–<max>"`.
  - `template_open(jobname, template)`: fills `#template_body`, sets `#template_title` to the jobname, shows `#template_modal`.
  - `jobhandler.template`: `null` or `{params, svg}`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/template.test.js`, in the style of `fillpopup.test.js`, using `loadFrontend(["issues.js", "template.js"])`:

```js
const P = [{ name: "vmin", default: 1500, min: 100, max: 6000, label: "Vorschub min" }];
test("form shows label, default and range", () => {
  const html = app.template_form_html(P);
  assert.ok(html.includes("Vorschub min") && html.includes('value="1500"'));
  assert.ok(html.includes('min="100"') && html.includes('max="6000"') && html.includes('data-name="vmin"'));
});
test("template_form_html escapes labels", () => {
  const html = app.template_form_html([{ ...P[0], label: '<img src=x onerror="alert(1)">' }]);
  assert.ok(!html.includes("<img"));
});
test("values are numbers within range", () => {
  assert.deepStrictEqual(app.template_check_values(P, { vmin: "1200" }), { values: { vmin: 1200 } });
  assert.deepStrictEqual(app.template_check_values(P, { vmin: "99" }), { error: "Vorschub min: use 100–6000" });
  assert.deepStrictEqual(app.template_check_values(P, { vmin: "" }), { error: "Vorschub min: use 100–6000" });
});
```

In `jobhandler.test.js`:
- `"template survives set and getJson"`: a minimal job with `template: {params: P, svg: "<svg/>"}` → `JSON.parse(getJson()).template` deep-equals it.
- `"job without template has no template key"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `node --test frontend/tests/template.test.js frontend/tests/jobhandler.test.js`. Expected: FAIL (`template_form_html is not a function`, missing `template`).

- [ ] **Step 3: Implement**

- jobhandler:
  - `clear()` sets `this.template = null`.
  - `set()` copies `job.template` when present.
  - `get()` adds `job_out.template` only when it is non-null.
- `import_open`: in its success handler, `if (from_library === true && job.template) { template_open(jobname, job.template); return; }` before `jobhandler.set`.
- `template_open`: the OK button (`#template_ok_btn`) collects `{name: input.val()}` from `.template_param`, then runs `template_check_values`.
  - On an error: `$().uxmessage("error", error)` and the modal stays open.
  - Otherwise: hide the modal, set `import_name = jobname` (the fatal dialog uses it), and `request_post({url: "/load", data: {job: template.svg, name: jobname, optimize: true, progressive: true, review_issues: true, params: values}, success: import_load_answer, error: import_load_failed})`.

- [ ] **Step 4: Run them to verify they pass, then pytest (which runs the JS suite too)**

Run: `node --test frontend/tests/*.test.js && uv run pytest backend/tests/test_frontend_js.py -q`. Expected: all pass.

- [ ] **Step 5: Manual check (run skill or `python backend/app.py`):** export a .dba from `TEMPLATE_SVG` via Open → Export, put it in `library/`, click it, change Feed, OK. The job shows the new pass value. Then delete the test file.

- [ ] **Step 6: Commit**

```bash
git add frontend/template.js frontend/app.html frontend/import.js frontend/jobhandler.js frontend/tests/template.test.js frontend/tests/jobhandler.test.js
git commit -m "Ask for template parameters when loading a library job"
```

---

### Task 4: FREILab test jobs as templates

**Files:**
- Create: `freilab/library/src/Test_PowerQuadrate.svg`, `freilab/library/src/Test_6Linien.svg`
- Replace: `freilab/library/Test_PowerQuadrate.dba`, `freilab/library/Test_6Linien.dba` (generated)
- Modify: `freilab/test_overlay.py`, `freilab/README.md` (table row for `library/`: mention `src/` and the templates)
- Add: `docs/superpowers/specs/2026-10-28-parametric-library-design.md` and this plan (committed here, not on JS-DEV)

**Interfaces:**
- Consumes: Tasks 1–2 (`jobimport.convert(svg, params=...)`, `template.render`)

Geometry is copied from the current .dba files (read their `defs`/`items`/`passes`):

- **PowerQuadrate:**
  - 6 squares of 10 mm at x = 0, 15, 30, 45, 60, 75 and y = 0.
  - Stroke colours in order: `#000000 #ff0000 #00ff00 #0000ff #fffb11 #ff02de`.
  - Each square has its feed as text centred inside it, and again below it (y ≈ 13), in `#02ffff`.
  - Params: `=param:vmin:1500:100:6000:Vorschub min=` and `=param:vmax:2000:100:6000:Vorschub max=`.
  - Square i (0 = left) runs at `round(vmax - (vmax - vmin) * i / 5)` and 100 %. Use the same expression in its labels.
  - Passes: `=pass1:2000:15:#02ffff=` (labels), then `=pass2…pass7` for the squares, in today's order.
- **6Linien:**
  - Lines of 5 mm at x = 0, 3, …, 15, from y = −2.5 to 2.5, so that (0,0) is the top edge.
  - Colours: `#00ffff #e50000 #00e500 #0000e3 #ffff00 #e21fc3`.
  - Params: `vmin 1500`, `vmax 2000` (100–6000) and `=param:p:100:1:100:Leistung %=`.
  - Line i runs at `round(vmax - (vmax - vmin) * i / 5)` and `{{p}}`.
  - The instruction text has no pass and is black (`#000000`). Place it like today (x ≈ 135, y ≈ 38). It is today's lines, minus the colour sequence line, plus one line `Türkis {{…}}, Rot {{…}}, Grün {{…}}, Blau {{…}}, Gelb {{…}}, Pink {{…}} mm/min bei {{p}} %`.
  - Today's arrow diagram and the "Werkstück" box are dropped (implementer's call, noted in the commit).
- **Both:**
  - `font-family="DejaVu Sans"`.
  - Units in mm (`width="…mm"` with a matching `viewBox`).
  - Lay out with a text editor; Inkscape is not required.

- [ ] **Step 1: `git checkout JS-DEV-Freilab && git merge JS-DEV`**, then run `pytest freilab/test_overlay.py -q`. Expected: pass, since the old .dba files are still present.

- [ ] **Step 2: Write the failing tests** in `freilab/test_overlay.py`

```python
def _lib(name):
    with open(os.path.join(HERE, "library", name + ".dba")) as fp:
        return json.load(fp)

def _render(name, params=None):
    with issues.collecting() as found:
        job = jobimport.convert(_lib(name)["template"]["svg"], optimize=False, params=params)
    assert found.issues() == []  # fonts, colours, tags all clean
    return job

def test_power_squares_defaults_match_todays_job():
    job = _render("Test_PowerQuadrate")
    assert [(p["feedrate"], p["intensity"]) for p in job["passes"]] == \
        [(2000, 15)] + [(f, 100) for f in (2000, 1900, 1800, 1700, 1600, 1500)]

def test_power_squares_follow_feed_range():
    job = _render("Test_PowerQuadrate", {"vmin": 1000, "vmax": 1500})
    assert [p["feedrate"] for p in job["passes"][1:]] == [1500, 1400, 1300, 1200, 1100, 1000]
    svg = template.render(_lib("Test_PowerQuadrate")["template"]["svg"], {"vmin": 1000, "vmax": 1500})
    assert svg.count(">1400<") == 2  # inside and below the square

def test_six_lines_follow_feed_and_power():
    job = _render("Test_6Linien", {"vmin": 1000, "vmax": 1500, "p": 80})
    assert [(p["feedrate"], p["intensity"]) for p in job["passes"]] == \
        [(f, 80) for f in (1500, 1400, 1300, 1200, 1100, 1000)]
    first = job["defs"][job["items"][job["passes"][0]["items"][0]]["def"]]["data"][0]
    assert first == [[0, -2.5], [0, 2.5]]  # (0,0) still on the top edge
```

(`HERE`, `json`, `os` already exist in the file or are added. Import `jobimport`, `from jobimport import issues, template`; the backend is on `sys.path` the way the file already arranges it. Keep `test_library_lists_and_loads_our_jobs_next_to_upstream_ones` as is.)

- [ ] **Step 3: Run them to verify they fail** (no `template` in the old files)

Run: `pytest freilab/test_overlay.py -q`. Expected: `KeyError: 'template'`.

- [ ] **Step 4: Write the two SVGs, then generate the .dba files**

```bash
for n in Test_PowerQuadrate Test_6Linien; do
  .venv/bin/python -c "import sys; sys.path.insert(0,'backend'); import jobimport; \
s=open('freilab/library/src/$n.svg').read(); \
open('freilab/library/$n.dba','w').write(jobimport.dumps(jobimport.convert(s)))"
done
```

- [ ] **Step 5: Run them to verify they pass**

Run: `pytest freilab/test_overlay.py -q`. Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add freilab/library docs/superpowers freilab/test_overlay.py freilab/README.md
git commit -m "Make the FREILab test jobs parametric (feed range, power)"
```

---

### Task 5: FixFocus and FocusStairs

**Files:**
- Create: `freilab/library/src/Parts_FixFocus.svg`, `freilab/library/Parts_FixFocus.dba` (generated)
- Delete: `freilab/library/Parts_Fokus9mm.dba`
- Rename: `git mv freilab/library/Parts_Fokushilfe_3-30mm.dba freilab/library/Parts_FocusStairs_3-30mm.dba` (content unchanged, no template)
- Modify: `freilab/test_overlay.py`

**Interfaces:**
- Consumes: Tasks 1–2

`Parts_FixFocus.svg`:

- **Disc:** copy both disc subpaths from `Parts_Fokus9mm.dba` def 0 (subpaths 0 and 1: the hole and the outline with the slot) as `<path d="M … L … Z">`, black `#000000`, unchanged.
- **Strip:** `<rect x="37.78" y="2.17" width="80" height="{{d}}" rx="1.5">`, black. It replaces def 0 subpath 2.
- **Label:**
  - `Fix Focus {{d}} mm`, red `#e00000`, `text-anchor="middle"` at x = 77.78, `font-family="DejaVu Sans"`.
  - `font-size="{{min(7, d*0.75)}}"`.
  - Baseline `y="{{2.17 + d/2 + 0.36*min(7, d*0.75)}}"`.
- **Param:** `=param:d:9:3:20:Dicke mm=`.
- **Passes:** none, as today.

- [ ] **Step 1: Write the failing tests** in `freilab/test_overlay.py` (reuse `_render`/`_lib` from Task 4)

```python
def _bbox(job, color):
    pts = [p for it in job["items"] if it.get("color") == color
           for path in job["defs"][it["def"]]["data"] for p in path]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)

@pytest.mark.parametrize("d", [3, 9, 20])
def test_fix_focus_strip_is_d_high_and_label_fits(d):
    job = _render("Parts_FixFocus", {"d": d})
    strip = [path for it in job["items"] if it.get("color") == "#000000"
             for path in job["defs"][it["def"]]["data"]
             if min(p[0] for p in path) > 35]  # right of the disc
    ys = [p[1] for path in strip for p in path]
    assert max(ys) - min(ys) == pytest.approx(d, abs=0.05)
    x0, y0, x1, y1 = _bbox(job, "#e00000")
    assert min(ys) < y0 and y1 < max(ys)  # label inside the strip

def test_fix_focus_label_says_fix_focus():
    svg = template.render(_lib("Parts_FixFocus")["template"]["svg"], {"d": 12})
    assert "Fix Focus 12 mm" in svg

def test_focus_files_renamed(client):
    files = json.loads(client.get("/listing_library").text)["files"]
    assert "Parts_FixFocus" in files and "Parts_FocusStairs_3-30mm" in files
    assert "Parts_Fokus9mm" not in files and "Parts_Fokushilfe_3-30mm" not in files
```

- [ ] **Step 2: Run them to verify they fail.** Run: `pytest freilab/test_overlay.py -q -k "focus"`. Expected: FAIL (file not found).

- [ ] **Step 3: Write the SVG, then generate the .dba** with the Task 4 Step 4 loop over `Parts_FixFocus`, `git rm` the old file and `git mv` the stairs.

- [ ] **Step 4: Run them to verify they pass, then everything**

Run: `pytest freilab/test_overlay.py -q && uv run pytest backend/tests -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A freilab/library freilab/test_overlay.py
git commit -m "Replace Fokus9mm with a parametric FixFocus, rename the focus stairs"
```
