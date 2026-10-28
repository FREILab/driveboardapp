"""Per-import issue collector and ImportFailed."""

import pickle
import threading

import jobimport
import pytest
from jobimport import issues
from test_dxf_gcode import _dxf, _line, _pairs


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
    barrier = threading.Barrier(2)
    collectors = {}

    def work(name):
        with issues.collecting() as c:
            barrier.wait()
            for _ in range(500):
                issues.report(f"problem {name}", "R", "changed", "f")
        collectors[name] = c

    threads = [threading.Thread(target=work, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for name in ("a", "b"):
        rows = collectors[name].issues()
        assert [(r["problem"], r["count"]) for r in rows] == [(f"problem {name}", 500)]


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
    assert str(e2) == str(e)


# --- SVG importers --------------------------------------------------------

_SVG = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" %s>%s</svg>'
_MM = 'width="100mm" height="100mm" viewBox="0 0 100 100"'


def _svg(body, size=_MM):
    return _SVG % (size, body)


def _import(svg):
    with issues.collecting() as c:
        job = jobimport.convert(svg, optimize=False)
    return job, c.issues()


def test_svg_invalid_colour_row():
    _, rows = _import(_svg('<rect id="frame" width="5" height="5" stroke="#12" fill="none"/>'))
    (row,) = rows
    assert row["problem"] == 'Invalid colour "#12"'
    assert row["where"] == 'rect "frame"'
    assert (row["result"], row["kind"], row["fix"]) == (
        "DEFAULT COLOUR",
        "changed",
        "Set a valid colour",
    )


def test_svg_unsupported_colour_row():
    _, rows = _import(_svg('<rect width="5" height="5" stroke="hsl(1,2%,3%)" fill="none"/>'))
    (row,) = rows
    assert row["problem"] == 'Unsupported colour "hsl(1,2%,3%)"'
    assert row["kind"] == "dropped"


def test_svg_linked_image_row():
    _, rows = _import(_svg('<image x="0" y="0" width="10" height="10" href="a.png"/>'))
    (row,) = rows
    assert (row["problem"], row["result"], row["kind"], row["fix"]) == (
        "Linked image",
        "NOT CUT",
        "dropped",
        "Embed the image",
    )
    assert row["where"] == "image"


def test_svg_without_size_info_row():
    _, rows = _import(_svg('<rect width="5" height="5" stroke="red" fill="none"/>', size=""))
    (row,) = rows
    assert (row["problem"], row["result"], row["kind"], row["fix"]) == (
        "No size info",
        "SCALED AT 90 DPI",
        "changed",
        "Set units to mm",
    )


def test_svg_not_an_svg_is_fatal():
    with pytest.raises(issues.ImportFailed) as e:
        jobimport.read_svg("<html/>", 0.01)
    assert e.value.issue["problem"] == "Not an SVG file"
    assert e.value.issue["fix"] == "Export as SVG"


def test_svg_malformed_is_fatal():
    with pytest.raises(issues.ImportFailed) as e:
        jobimport.read_svg(_SVG % (_MM, "<path"), 0.01)
    assert e.value.issue["problem"] == "SVG not well-formed"
    assert e.value.issue["where"]


def test_svg_require_unit_is_fatal(monkeypatch):
    monkeypatch.setitem(jobimport.converter.conf, "require_unit", True)
    with pytest.raises(issues.ImportFailed) as e:
        jobimport.read_svg(_svg("", size='width="100" height="100"'), 0.01)
    assert e.value.issue["problem"] == "No unit in SVG"


def test_svg_odd_points_is_fatal():
    with pytest.raises(issues.ImportFailed) as e:
        jobimport.read_svg(_svg('<polygon id="p" points="1 2 3"/>'), 0.01)
    assert e.value.issue["problem"] == "Odd number of points"
    assert e.value.issue["where"] == 'polygon "p"'


def test_svg_head_warnings_kept_for_mill():
    job, _ = _import(_svg('<rect id="frame" width="5" height="5" stroke="#12" fill="none"/>'))
    assert job["head"]["warnings"] == ['Invalid colour "#12" (rect "frame")']


def test_clean_svg_has_no_rows():
    job, rows = _import(_svg('<rect width="5" height="5" stroke="red" fill="none"/>'))
    assert rows == []
    assert "warnings" not in job["head"]


def test_svg_bad_transform_and_unit_rows():
    _, rows = _import(
        _svg(
            '<rect id="a" width="50%" height="5" stroke="red" fill="none"/>'
            '<rect id="b" width="5" height="5" stroke="red" fill="none" transform="scale(1,2,3)"/>'
        )
    )
    by_problem = {r["problem"]: r for r in rows}
    assert by_problem['Unsupported unit "%"']["where"] == 'rect "a"'
    assert by_problem['Invalid "scale" transform']["where"] == 'rect "b"'


def test_svg_unreadable_image_row():
    _, rows = _import(_svg('<image width="10" height="10" href="data:image/png;base64,AAAA"/>'))
    assert [r["problem"] for r in rows] == ["Image not readable"]


def test_svg_text_font_rows(monkeypatch):
    from jobimport import svg_text_converter as stc

    doc = _svg('<text id="t" x="0" y="10" font-family="NoSuchFontQwerty">Hi</text>')
    monkeypatch.setattr(stc.get_font_manager(), "find_font", lambda family: None)
    _, rows = _import(doc)
    (row,) = rows
    assert (row["problem"], row["result"], row["kind"], row["fix"]) == (
        'Font "NoSuchFontQwerty" missing',
        "TEXT NOT CUT",
        "dropped",
        "Convert text to path",
    )
    assert row["where"] == 'text "t"'


def test_svg_use_target_missing_row():
    _, rows = _import(_svg('<use id="u" href="#nope"/>'))
    (row,) = rows
    assert row["problem"] == '"use" target missing'
    assert row["where"] == 'use "u"'
    assert row["kind"] == "dropped"


def test_svg_rgba_alpha_row():
    _, rows = _import(
        _svg('<rect id="r" width="5" height="5" stroke="rgba(1,2,3,0)" fill="none"/>')
    )
    (row,) = rows
    assert (row["problem"], row["result"], row["kind"], row["fix"], row["where"]) == (
        "Colour alpha ignored",
        "CUT AT FULL OPACITY",
        "changed",
        "Use opacity",
        'rect "r"',
    )


@pytest.mark.parametrize("val", ["#f008", "#11223380", "rgba(1,2,3,50%)"])
def test_svg_alpha_forms_report(val):
    _, rows = _import(_svg(f'<rect width="5" height="5" stroke="{val}" fill="none"/>'))
    assert [r["problem"] for r in rows] == ["Colour alpha ignored"]


@pytest.mark.parametrize("val", ["#f00f", "#112233ff", "rgba(1,2,3,1)", "rgba(1,2,3,100%)"])
def test_svg_opaque_alpha_is_silent(val):
    _, rows = _import(_svg(f'<rect width="5" height="5" stroke="{val}" fill="none"/>'))
    assert rows == []


def test_svg_text_invalid_and_alpha_colour_rows():
    from jobimport.svg_text_converter import normalize_color

    with issues.collecting() as c:
        assert normalize_color("#12", 'text "t"') == "#000000"
        assert normalize_color("rgba(1,2,3,0)", 'text "t"') == "#010203"
        assert normalize_color("none", 'text "t"') == "#000000"
        assert normalize_color("currentColor", 'text "t"') == "#000000"
    assert [(r["problem"], r["where"]) for r in c.issues()] == [
        ('Invalid colour "#12"', 'text "t"'),
        ("Colour alpha ignored", 'text "t"'),
    ]


def test_svg_text_fill_invalid_colour_row():
    doc = _svg('<text id="t" x="0" y="10" fill="#12">Hi</text>')
    _, rows = _import(doc)
    assert ('Invalid colour "#12"', 'text "t"') in [(r["problem"], r["where"]) for r in rows]


_BOX = '<rect x="%s" y="10" width="20" height="20" stroke="#000" fill="none"/>'


def test_svg_vector_clip_path_row_once_per_element():
    clip = '<clipPath id="c"><rect width="10" height="10"/></clipPath>'
    group = '<g id="%s" clip-path="url(#c)">%s</g>'
    g1 = group % ("g1", _BOX % 10 + _BOX % 40)  # two paths, one row
    _, rows = _import(_svg(clip + g1 + group % ("g2", _BOX % 70)))
    (row,) = rows
    assert (row["problem"], row["result"], row["kind"], row["fix"]) == (
        "Clip path ignored",
        "CUT UNCLIPPED",
        "changed",
        "Release the clip",
    )
    assert (row["where"], row["count"]) == ('g "g1"', 2)


def test_svg_children_of_a_are_cut():
    job, rows = _import(_svg("<a href='https://x'>" + _BOX % 10 + "</a>"))
    assert rows == []
    assert [d["kind"] for d in job["defs"]] == ["path"]


def test_svg_switch_cuts_its_first_renderable_child():
    body = "<switch><foreignObject/><g>" + _BOX % 10 + "</g><g>" + _BOX % 50 + "</g></switch>"
    job, rows = _import(_svg(body))
    assert rows == []
    ((first, *_),) = job["defs"][0]["data"]
    assert first[0] == 10


def test_svg_unknown_renderable_tag_row():
    _, rows = _import(_svg('<hatch id="h"/>' + _BOX % 10))
    (row,) = rows
    assert (row["problem"], row["result"], row["kind"], row["where"]) == (
        '"hatch" unsupported',
        "NOT CUT",
        "dropped",
        'hatch "h"',
    )


def test_svg_non_rendering_and_editor_tags_are_silent():
    body = (
        "<title>t</title><desc>d</desc><metadata/><linearGradient id='l'/>"
        '<sodipodi:namedview xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/'
        'sodipodi-0.dtd"/>' + _BOX % 10
    )
    assert _import(_svg(body))[1] == []


def test_svg_stacked_duplicate_paths_row():
    job, rows = _import(_svg(_BOX % 10 + _BOX % 10 + _BOX % 10))
    (row,) = rows
    assert (row["problem"], row["result"], row["kind"], row["fix"], row["count"]) == (
        "Duplicate paths",
        "CUT ONCE",
        "changed",
        "Use passes instead",
        2,
    )


# ---- DXF and G-code ----


def _ellipse(layer):
    return _pairs(
        (0, "ELLIPSE"), (8, layer), (10, 0), (20, 0), (30, 0), (11, 10), (21, 0)
    ) + _pairs((31, 0), (40, 0.5), (41, 0), (42, 6.28))


def _dxf_import(entities, layers=(("0", 7),)):
    with issues.collecting() as c:
        job = jobimport.convert(_dxf(entities, layers=layers), optimize=False)
    return job, c.issues()


def test_dxf_ellipse_row():
    _, rows = _dxf_import([_ellipse("Gravur")], layers=(("0", 7), ("Gravur", 7)))
    assert rows == [
        {
            "problem": "ELLIPSE unsupported",
            "where": 'layer "Gravur"',
            "result": "NOT CUT",
            "kind": "dropped",
            "fix": "Convert to polyline",
            "count": 1,
        }
    ]


def test_dxf_two_ellipses_count_2():
    _, rows = _dxf_import(
        [_ellipse("Gravur"), _ellipse("Cut")], layers=(("0", 7), ("Gravur", 7), ("Cut", 1))
    )
    (row,) = rows
    assert (row["problem"], row["count"], row["where"]) == (
        "ELLIPSE unsupported",
        2,
        'layer "Gravur"',
    )


def test_dxf_garbage_is_fatal():
    with pytest.raises(issues.ImportFailed) as err:
        jobimport.read_dxf("garbage", 0.1)
    assert err.value.issue == {
        "problem": "Not a readable DXF",
        "where": None,
        "fix": "Export as DXF R12",
    }


def test_dxf_outside_bed_no_longer_raises():
    job, rows = _dxf_import([_line(5000, 0, 5010, 0)])
    assert rows == []
    assert max(x for d in job["defs"] for p in d["data"] for x, _ in p) > 4000


def test_gcode_arc_row():
    with issues.collecting() as c:
        jobimport.read_gcode("G2 X10 Y10 I5 J0\n", 0.1)
    row = next(r for r in c.issues() if r["problem"] == "Arcs (G2/G3) unsupported")
    assert row["where"] == "line 1"
    assert (row["result"], row["kind"], row["fix"]) == (
        "STRAIGHT LINE",
        "changed",
        "Export arcs as lines",
    )


def test_svg_fill_in_stroke_colour_is_one_path_not_a_duplicate():
    job, rows = _import(_svg('<rect width="20" height="20" stroke="#000" fill="#000"/>'))
    assert rows == []
    assert len(job["defs"][0]["data"]) == 1


def test_gcode_cam_header_cancel_codes_are_silent():
    with issues.collecting() as c:
        jobimport.read_gcode("G21 G90 G17 G40 G49 G80\nG61\nG64\nG15\nG69\n", 0.1)
    assert c.issues() == []
