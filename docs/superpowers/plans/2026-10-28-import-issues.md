# Import Issues Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every import problem reaches the laser operator as a short row (problem · result · fix) before anything is queued; they cancel or load anyway. Unloadable files show one blocker dialog.

**Architecture:** Importers report structured issues into a per-import collector (`jobimport/issues.py`, a `ContextVar`, so concurrent imports never mix). Fatal problems raise `ImportFailed(ValueError)`. The convert worker ships issues across the process boundary in a sidecar file; `/load` with `review_issues: true` holds the converted job under a token and answers with the issue list; `/load_commit/<hold>` queues it, `/load_cancel/<hold>` drops it. The laser frontend renders both dialogs from a small pure-JS module.

**Tech Stack:** Python 3.9+, bottle, multiprocessing spawn pool (existing); jQuery + Bootstrap 3 (existing); pytest 8.4.2 + webtest; `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-28-import-issues-design.md` (read it first; mockups next to it).

## Global Constraints

- Python 3.9 compatible (no `match`, no runtime `X | Y` types). No new dependencies, backend or frontend.
- ruff: `line-length = 100`; `ruff check .` and `ruff format --check .` must pass.
- UI copy English, short. A row needing a sentence is wrong. Fix text 2–4 words.
- Result kinds, exactly three: `changed` (yellow, `#fcf8e3`/`#8a6d3b`), `replaced` (blue, `#d9edf7`/`#31708f`), `dropped` (red, `#f2dede`/`#a94442`).
- Laser frontend (`frontend/`) only. `frontend_mill/` untouched; it still reads `job.head.warnings`, so that field keeps being filled.
- `/load` without `review_issues` behaves exactly as today (status codes, bodies, queueing).
- Nothing is written to the queue (and no queue trimming happens) until commit.
- Existing tests keep passing, except where this plan names a test whose asserted behaviour the spec changes.
- Commits: plain message, **no** `Co-Authored-By` trailer, no "Generated with" line, no Claude/Anthropic mention (user's global rule).
- Test env: `S=/tmp/claude-1000/-home-ubuntu-Development-Freilab-driveboardapp/09e1101a-796f-40b6-a9a0-e105f0477c1c/scratchpad/uvenv/bin`; run `$S/python -m pytest backend/tests -q -p no:cacheprovider`. Baseline: 1450 passed, 1 skipped, 7 failed (the firmware hex tests in `test_firmware.py`, local avr-gcc mismatch — ignore).

## Review Focus

1. **File-controlled text in the dialog** (element ids, colour strings, font names) must be HTML-escaped; an SVG with `id="<img src=x onerror=alert(1)>"` must render as text. → Task 6, `issues.test.js`.
2. **Two imports at once** (in-process fallback runs conversions on server threads) must not see each other's issues. → Task 1, threaded collector test.
3. **Cancel while the progressive optimize is still running**: the optimized result must never land in the queue afterwards. → Task 5.
4. **Commit after expiry or with an unknown token**: 404 with a fatal body ("Import expired" / "Open the file again"), nothing queued. → Task 5.
5. **10 000 repeats of one problem**: one row with `count: 10000`, response stays small. → Task 1.

---

### Task 1: Issue collector and `ImportFailed`

**Files:**
- Create: `backend/jobimport/issues.py`
- Test: `backend/tests/test_import_issues.py`

**Interfaces:**
- Produces:
  - `KINDS = ("changed", "replaced", "dropped")`
  - `report(problem, result, kind, fix, where=None) -> None` — adds to the active collector (no-op without one) and always logs `problem`/`where` at warning level on logger `"jobimport"`. `kind` not in `KINDS` → `ValueError`.
  - `collecting()` — context manager yielding a `Collector`; nests safely (inner restores outer on exit).
  - `Collector.issues() -> list[dict]` — rows `{"problem", "where", "result", "kind", "fix", "count"}`, in first-seen order, collapsed on `(problem, result, kind, fix)`; `where` is the first occurrence's; `count` ≥ 1.
  - `snapshot() -> list[dict]` — `issues()` of the active collector, `[]` without one.
  - `class ImportFailed(ValueError)`: `__init__(self, problem, fix, where=None)`; attribute `.issue -> {"problem", "where", "fix"}`; `str(e)` is `problem` or `f"{problem} ({where})"`; survives `pickle` round-trip (pass all three to `super().__init__`).

- [ ] **Step 1: Write the failing tests**

```python
def test_report_without_collector_is_noop():
    issues.report("x", "NOT CUT", "dropped", "fix it")
    assert issues.snapshot() == []


def test_rows_collapse_with_count_and_first_where():
    with issues.collecting() as c:
        for i in range(10000):
            issues.report(
                "Invalid colour",
                "DEFAULT COLOUR",
                "changed",
                "Set a valid colour",
                where=f'rect "r{i}"',
            )
        issues.report("Font missing", "DEJAVU SANS USED", "replaced", "Convert text to path")
    rows = c.issues()
    assert rows[0] == {
        "problem": "Invalid colour",
        "where": 'rect "r0"',
        "result": "DEFAULT COLOUR",
        "kind": "changed",
        "fix": "Set a valid colour",
        "count": 10000,
    }
    assert [r["problem"] for r in rows] == ["Invalid colour", "Font missing"]


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        issues.report("x", "Y", "bogus", "z")


def test_collectors_are_isolated_per_thread():
    # two threads each collect 500 of their own rows concurrently; each sees only its own
    ...  # barrier-start two threads, assert each Collector.issues() has exactly its problem, count 500


def test_nested_collecting_restores_outer():
    with issues.collecting() as outer:
        with issues.collecting() as inner:
            issues.report("a", "R", "changed", "f")
        issues.report("b", "R", "changed", "f")
    assert [r["problem"] for r in inner.issues()] == ["a"]
    assert [r["problem"] for r in outer.issues()] == ["b"]


def test_import_failed_is_valueerror_and_pickles():
    e = issues.ImportFailed("Not a readable DXF", "Export again", where="line 412")
    assert isinstance(e, ValueError)
    assert str(e) == "Not a readable DXF (line 412)"
    e2 = pickle.loads(pickle.dumps(e))
    assert e2.issue == {"problem": "Not a readable DXF", "where": "line 412", "fix": "Export again"}
```

- [ ] **Step 2: Run, expect FAIL** — `$S/python -m pytest backend/tests/test_import_issues.py -q` → `ModuleNotFoundError: jobimport.issues`.
- [ ] **Step 3: Implement `backend/jobimport/issues.py`** — `contextvars.ContextVar("import_issues", default=None)`; `Collector` keeps an insertion-ordered dict keyed on the 4-tuple.
- [ ] **Step 4: Run, expect PASS**; also `ruff check backend/jobimport/issues.py`.
- [ ] **Step 5: Commit** — `git add backend/jobimport/issues.py backend/tests/test_import_issues.py && git commit -m "Add per-import issue collector"`

---

### Task 2: SVG importers report issues

**Files:**
- Modify: `backend/jobimport/svg_attribute_reader.py`, `svg_tag_reader.py`, `svg_reader.py`, `svg_text_converter.py`, `imagedata.py`, `converter.py`
- Test: `backend/tests/test_import_issues.py` (append), `backend/tests/test_svg_readers.py` (update the 4 `get_conversion_warnings` assertions, lines ~821–971)

**Interfaces:**
- Consumes: `issues.report`, `issues.snapshot`, `issues.ImportFailed` (Task 1).
- Produces: `jobimport.convert(...)` unchanged signature; when called inside `collecting()` the collector holds every SVG/image issue; `job["head"]["warnings"]` = `[row["problem"] + (f" ({row['where']})" if row["where"] else "") for row in snapshot()]` when non-empty (mill compat), set in `converter.convert` after parsing for every format.
- Removed: `svg_text_converter._conversion_warnings`, `_add_warning`, `get_conversion_warnings`; `svg_reader` no longer sets `parse_results["warnings"]`; `converter.read_svg` no longer copies `res["warnings"]`.

Rules for converting each `log.warn` / `log.warning` / `log.error` site in these files (grep them):
- Becomes `issues.report(...)` if the operator can act on it. `result` states what the code **actually does** at that site (read the code; e.g. `parse_color` returning `"inherit"` → `DEFAULT COLOUR`). `kind`: geometry/appearance still cut → `changed`; substitute used → `replaced`; content not cut → `dropped`.
- Pass `where` when the call site has the element (`tag` + `id`, e.g. `rect "frame"`, `path "hinge"`). `parse_color` has no element: report from `colorAttrib` instead, where `node` is in reach, and keep `parse_color` itself silent except its return value.
- Pure internals (e.g. `px2mm by ...` info lines) stay `log.info`/`log.debug`.
- Keep the existing `log` call next to `report` only where `report`'s own logging would not cover it (it logs already).

Fixed rows (exact copy):

| Site | problem | result | kind | fix |
|---|---|---|---|---|
| invalid colour (`colorAttrib`) | `Invalid colour "<val>"` | `DEFAULT COLOUR` | changed | `Set a valid colour` |
| font fallback (`svg_text_converter` ~261) | `Font "<family>" missing` | `<FALLBACK> USED` (upper-case name) | replaced | `Convert text to path` |
| no font file found / font fails to load (~651, ~657) | `Font "<family>" missing` | `TEXT NOT CUT` | dropped | `Convert text to path` |
| 90 dpi fallback (`svg_reader` ~992) | `No size info` | `SCALED AT 90 DPI` | changed | `Set units to mm` |
| linked image (`svg_tag_reader` ~352) | `Linked image` | `NOT CUT` | dropped | `Embed the image` |

Fatal sites → `raise issues.ImportFailed(...)`:
- `svg_reader` ~857 XML parse error → `("SVG not well-formed", "Export again", where=str(e))`.
- `svg_reader` ~865 no `svg` root tag (today: silently returns an empty job) → `("Not an SVG file", "Export as SVG")`.
- `svg_reader` ~955 `require_unit` → `("No unit in SVG", "Set units to mm")`.

- [ ] **Step 1: Write failing tests** (append to `test_import_issues.py`), each `with issues.collecting() as c: jobimport.convert(svg, optimize=False)`:
  - `test_svg_invalid_colour_row`: `<rect id="frame" stroke="#12" .../>` → a row `problem == 'Invalid colour "#12"'`, `where == 'rect "frame"'`, `kind == "changed"`.
  - `test_svg_linked_image_row`: `<image href="a.png" .../>` → row `Linked image` / `dropped`.
  - `test_svg_without_size_info_row`: no width/height/viewBox units, no app signature → row `No size info`.
  - `test_svg_not_an_svg_is_fatal`: `<html/>` → `pytest.raises(issues.ImportFailed)` with `.issue["problem"] == "Not an SVG file"`.
  - `test_svg_head_warnings_kept_for_mill`: invalid colour SVG → `job["head"]["warnings"] == ['Invalid colour "#12" (rect "frame")']`.
  - `test_clean_svg_has_no_rows`: a plain mm-unit rect → `c.issues() == []` and no `"warnings"` in `job["head"]`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per the rules and tables above; update the 4 `test_svg_readers.py` assertions to collect via `issues.collecting()` instead of `get_conversion_warnings()`, and any test that asserted the old empty-job result for a non-SVG root.
- [ ] **Step 4: Run full suite, expect baseline + new tests passing.**
- [ ] **Step 5: Commit** — `git commit -m "Report SVG import issues as structured rows"`

---

### Task 3: DXF and G-code importers report issues

**Files:**
- Modify: `backend/jobimport/dxf_parser.py`, `backend/jobimport/gcode_reader.py`
- Test: `backend/tests/test_import_issues.py` (append), `backend/tests/test_dxf_gcode.py` (update tests asserting the removed bounds `RuntimeError`, the `print` outputs now reported, and `RuntimeError` on unsupported units)

**Interfaces:**
- Consumes: Task 1.
- Produces: no `RuntimeError` escapes `DXFParser.parse` any more — fatal paths raise `ImportFailed`.

Changes:
- `validateBoundaries` and its call (~213) are **deleted**: out-of-bounds is now the work-area warning of Task 4/5 (operator decision).
- Fatal: `dxfgrabber.read` failure (~83) → `("Not a readable DXF", "Export as DXF R12")`; unsupported units (~162 and ~503) → `("Unsupported DXF units", "Set units to mm", where=<unitsString or code>)`. `complain_spline`/`complain_invalid`: if unreachable from `parse`, delete them; else same treatment.
- Rows (exact copy):

| Site | problem | result | kind | fix |
|---|---|---|---|---|
| unknown/unsupported entity (~190/194), per type | `<TYPE> unsupported` (e.g. `ELLIPSE unsupported`) | `NOT CUT` | dropped | `Convert to polyline` |
| unrecognized colour (~301) | `Unknown colour <n>` | `CUT IN CYAN` | changed | `Use a standard colour` |
| G2/G3 | `Arcs (G2/G3) unsupported` | `STRAIGHT LINE` | changed | `Export arcs as lines` |
| G4, G53, G55–59, G92, G93/G95, M0/M1, M4 | `<code> unsupported` (code as printed today) | `IGNORED` | changed | `Remove from G-code` |
| M6 with extra words (~182) | `Odd tool change line` | `IGNORED` | changed | `Only T on M6 line` |
| motion before any tool (~195) | `Move before tool set` | `NOT CUT` | dropped | `Set tool first` |

  `where` for G-code rows: `line <n>` (1-based source line) when the reader knows it; DXF rows: `layer "<name>"` when known.
- Debug prints (min/max, "Done!", versions, `using forced_unit`, shift values) stay as they are (`verbose`-gated or plain debug), not reported.

- [ ] **Step 1: Write failing tests**: `test_dxf_ellipse_row` (minimal DXF with one ELLIPSE on layer "Gravur" → row `ELLIPSE unsupported`, `where == 'layer "Gravur"'`, `dropped`; use the DXF builder helpers already in `test_dxf_gcode.py` — import them); `test_dxf_two_ellipses_count_2`; `test_dxf_garbage_is_fatal` (`"0\nEOF"`-style junk → `ImportFailed`, problem `Not a readable DXF`); `test_dxf_outside_bed_no_longer_raises` (a LINE at x=5000 converts without exception); `test_gcode_arc_row` (`G2 X10 Y10 I5 J0` → row `Arcs (G2/G3) unsupported`, `where == "line 1"`).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**; update the `test_dxf_gcode.py` tests that asserted old prints/`RuntimeError` to assert the rows / `ImportFailed` instead.
- [ ] **Step 4: Full suite passes.**
- [ ] **Step 5: Commit** — `git commit -m "Report DXF and G-code import issues as structured rows"`

---

### Task 4: Work-area check for a whole job

**Files:**
- Modify: `backend/driveboard.py` (add next to `job_laser_validate`, ~1718)
- Test: `backend/tests/test_driveboard_more.py` (append)

**Interfaces:**
- Produces: `job_outside_work_area(jobdict) -> str or None` — `None` when every point fits; else a short description of the first excess, format `"x 1450 > 1220 mm"` / `"y -5 < 0 mm"` (values rounded to whole mm; limits are the work area in offset coordinates, same rule as `check_point`: `[-off, workspace - off]` per axis, Z only if `conf["workspace"][2]`).
- Geometry: every item (not passes — imports usually have none) → its def, plus `item.get("translate", [0, 0, 0])`; `path`/`fill`: all points; `image`: corners `pos` and `pos + size`. Offsets read under `SerialLoop.lock` exactly like `job_laser_validate`.

- [ ] **Step 1: Failing tests** (reuse the file's existing conf/offset fixtures): fits → `None`; path point at x 1450 with workspace 1220 → `"x 1450 > 1220 mm"`; negative y → `"y -5 < 0 mm"`; item `translate` pushes an inside path out → not `None`; image `pos [1200,0] size [50,10]` → `"x 1250 > 1220 mm"`; non-zero offset shifts the limit.
- [ ] **Step 2: FAIL.** **Step 3: Implement.** **Step 4: PASS + full suite.**
- [ ] **Step 5: Commit** — `git commit -m "Add whole-job work-area check"`

---

### Task 5: `/load` review flow (hold, commit, cancel, fatal JSON)

**Files:**
- Modify: `backend/web.py` (`_convert_worker` ~629, `_convert_job` ~716, `load` ~773, new routes after `load_result`)
- Test: `backend/tests/test_web.py` (append; update the 3 existing `_convert_job` call sites ~529/536/1564 to the 3-tuple)

**Interfaces:**
- Consumes: Tasks 1–4 (`issues.collecting`, `ImportFailed`, `driveboard.job_outside_work_area`).
- `_convert_worker`: wraps each `jobimport.convert` in `issues.collecting()`; writes `out_path + ".issues"` (JSON list) **before** the quick file appears (progressive) or before the dba (non-progressive). Optimize-stage messages are not collected.
- `_convert_job(job, optimize, matrix, progressive=False) -> (dba_str, token_or_None, issues_list)`; issues read from the sidecar (`[]` if absent).
- `load()` with `review_issues: true`:
  - after conversion, append `{"problem": "Outside work area", "where": <desc>, "result": "RUN REFUSED", "kind": "dropped", "fix": "Move origin or scale", "count": 1}` when `driveboard.job_outside_work_area(json.loads(dba))` is not `None`;
  - no issues → exactly today's response;
  - issues → nothing saved; store `_load_held[hold] = {"job", "name", "overwrite", "pending", "expires": time.time() + CONVERT_TIMEOUT}` (under `_load_pending_lock`); answer `{"hold": hold, "name": name, "issues": [...]}` (200).
  - failures answer JSON `{"fatal": {"problem", "where", "fix"}}` with the status the plain path uses: `ImportFailed` → its `.issue`, 422; other `ValueError` → `("Import failed", str(e), "Check the file")`, 422; `TypeError` → `("Unknown file type", None, "Use SVG, DXF, G-code or DBA")`, 400; timeout → `("Import timed out", None, "Simplify or split file")`, 504; any other exception → `("Import crashed", f"{type(e).__name__}: {e}", "Report file to admin")`, 500. Set `Content-Type: application/json`.
- Without `review_issues`: unchanged, except DXF failures now arrive as `ImportFailed` → 422 with `str(e)` (previously an unhandled 500).
- Extract today's save tail of `load()` into `_queue_loaded(job, name, overwrite, pending) -> str` (the JSON body `/load` returns now); both `load()` and commit use it. `_unique_name` and the stale-marking of a previous pending entry (today ~840–846, under `_load_pending_lock`) move with it, so they run at commit time.
- `GET /load_commit/<hold>` (auth): pop held → `_queue_loaded(...)`; unknown/expired → 404 `{"fatal": {"problem": "Import expired", "where": null, "fix": "Open the file again"}}`.
- `GET /load_cancel/<hold>` (auth): pop held; if it has a `pending` token, mark that entry `stale` and drop it from `_load_pending`; answer `"{}"`; unknown → `"{}"` too (idempotent).
- `_held_purge()` drops expired holds (and stales their pending entries); called at the start of `load`, commit and cancel.

- [ ] **Step 1: Failing tests** (webtest `app` fixture as in the file):
  - `test_review_clean_import_queues_as_today`
  - `test_review_issues_hold_not_queue` (invalid-colour SVG → body has `hold` + one issue; queue dir unchanged; `max_jobs_in_list` trimming did not run)
  - `test_review_commit_queues_job` (then `/load_commit/<hold>` → name returned, file in queue)
  - `test_review_cancel_leaves_queue_untouched`
  - `test_review_cancel_during_progressive_optimize_never_queues` (progressive load of an issue SVG; cancel; wait on the pending entry's event; queue still empty)
  - `test_review_commit_unknown_or_expired_404` (monkeypatch `time.time` past `expires`)
  - `test_review_outside_work_area_row` (SVG rect at x 5000 mm → row `Outside work area`, `RUN REFUSED`)
  - `test_review_fatal_bodies` (parametrized: non-SVG XML → 422 `Not an SVG file`; random bytes → 400 `Unknown file type`; patched `_convert_job` raising `RuntimeError` → 500 `Import crashed`)
  - `test_plain_load_dxf_garbage_is_422` (no review flag)
  - `test_issues_cross_the_worker_process` (real spawn pool, issue SVG, `_convert_job` returns the row)
- [ ] **Step 2: FAIL.** **Step 3: Implement.** **Step 4: Full suite PASS.**
- [ ] **Step 5: Commit** — `git commit -m "Hold imports with issues for review on /load"`

---

### Task 6: Laser frontend dialogs

**Files:**
- Create: `frontend/issues.js`, `frontend/tests/issues.test.js`
- Modify: `frontend/app.html` (script tag after `import.js`'s dependencies, before `import.js`; two modals), `frontend/import.js` (both `/load` calls ~98 and ~160, `import_open` ~195), `frontend/css/style.css`

**Interfaces:**
- Consumes: Task 5 JSON (`{hold, name, issues}`, `{fatal}`).
- Produces (globals in `issues.js`):
  - `issues_escape(s) -> string` (`& < > " '`)
  - `issues_rows_html(issues) -> string` — one `<tr>` per row: problem (+ ` ×N` when `count > 1`) and `<span class="where">` location; `<span class="issue-tag issue-<kind>">RESULT</span>`; fix. Everything escaped.
  - `issues_show(name, issues, on_load, on_cancel)` — fills `#import_issues_modal` (title `"<name> – N problems"`, N = rows), wires **Cancel** / **Load anyway**, shows it.
  - `issues_show_fatal(name, fatal)` — fills `#import_fatal_modal` (title `"<name> – cannot import"`, bold problem, where, `Fix: <fix>`), **Close** only.
  - `issues_fatal_from(xhr) -> object or null` — parsed `fatal` from `xhr.responseText`, `null` if not JSON / no `fatal`.
- `import.js`: both load requests send `review_issues: true`. Move today's success body into `import_loaded(result)`. On success: `result.issues` → `issues_show(import_name, result.issues, load_anyway, cancel)` where load_anyway = `request_get /load_commit/<hold>` → `import_loaded`, cancel = `request_get /load_cancel/<hold>` + `uxmessage("notice", "Import cancelled.")`; else `import_loaded(result)`. On error: `issues_fatal_from(data)` → `issues_show_fatal`, else the existing toasts. Delete the `job.head.warnings` toast loop in `import_open`.
- Markup/CSS per the mockups (`docs/superpowers/specs/2026-10-28-import-issues-warn.png`, `...-fatal.png`): header backgrounds `#fcf8e3` / `#f2dede`, columns Problem · Result · Fix, `.where` monospace grey 12px, tag colours from Global Constraints.

- [ ] **Step 1: Failing node tests** (`loadFrontend(["issues.js"])` from `harness.js`):
  - escaping: problem `'<img src=x onerror=alert(1)>'` → output contains `&lt;img` and no `<img`
  - count: `count: 2` → contains `×2`; `count: 1` → no `×`
  - kind class: `kind: "dropped"` → `issue-dropped`
  - `issues_fatal_from({responseText: '{"fatal":{"problem":"P","where":null,"fix":"F"}}'})` → object; `{responseText: "Invalid file type."}` → `null`
- [ ] **Step 2: FAIL** — `node --test "frontend/tests/*.test.js"`.
- [ ] **Step 3: Implement** `issues.js`, markup, CSS, `import.js` wiring.
- [ ] **Step 4: PASS** node tests; `node --check frontend/*.js`; full pytest (the `test_frontend.py` asset tests pick up `issues.js` automatically).
- [ ] **Step 5: Manual check** — start the app (`$S/python backend/app.py --no-gui` or the project's run skill), import a file with an invalid colour and a clean one; screenshot both dialogs; compare to the mockups.
- [ ] **Step 6: Commit** — `git commit -m "Show import issues dialog in the laser frontend"`
