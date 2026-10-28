"""Tests for the DXF parser, the G-code reader and the job-conversion glue.

DXFs are built in-test as minimal R12 text so every entity can be asserted on
exactly; there is no hardware or file fixture involved except one smoke test.
"""

import base64
import io
import json
import math
import os

import jobimport
import pytest
from config import conf
from jobimport import converter, dedupe, imagedata
from jobimport.dxf_parser import DXFParser
from jobimport.gcode_reader import GcodeReader
from PIL import Image

BED = (1220, 610)


# ---------------------------------------------------------------------------
# DXF building blocks
# ---------------------------------------------------------------------------


def _pairs(*codes):
    return "".join(f"{c}\n{v}\n" for c, v in codes)


def _dxf(entities, insunits=4, layers=(("0", 7),)):
    s = _pairs((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1009"))
    if insunits is not None:
        s += _pairs((9, "$INSUNITS"), (70, insunits))
    s += _pairs((0, "ENDSEC"), (0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LAYER"))
    s += _pairs((70, len(layers)))
    for name, color in layers:
        s += _pairs((0, "LAYER"), (2, name), (70, 0), (62, color), (6, "CONTINUOUS"))
    s += _pairs((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "ENTITIES"))
    return s + "".join(entities) + _pairs((0, "ENDSEC"), (0, "EOF"))


def _line(x1, y1, x2, y2, layer="0", color=None):
    codes = [(0, "LINE"), (8, layer)]
    if color is not None:
        codes.append((62, color))
    return _pairs(*codes, (10, x1), (20, y1), (30, 0), (11, x2), (21, y2), (31, 0))


def _circle(cx, cy, r):
    return _pairs((0, "CIRCLE"), (8, "0"), (10, cx), (20, cy), (30, 0), (40, r))


def _arc(cx, cy, r, a0, a1):
    return _pairs((0, "ARC"), (8, "0"), (10, cx), (20, cy), (30, 0), (40, r), (50, a0), (51, a1))


def _lwpolyline(points, closed=False, bulges=None):
    codes = [(0, "LWPOLYLINE"), (8, "0"), (90, len(points)), (70, int(closed))]
    for i, (x, y) in enumerate(points):
        codes += [(10, x), (20, y)]
        if bulges:
            codes.append((42, bulges[i]))
    return _pairs(*codes)


def _parse(entities, forced_unit=None, **kw):
    parser = DXFParser(0.1, BED)
    parser.verbose = False
    return parser.parse(_dxf(entities, **kw), forced_unit)


def _paths(job):
    """All paths of a parsed job, flattened across its defs."""
    return [p for d in job["defs"] for p in d["data"]]


def _flat(path):
    return [c for pt in path for c in pt]


# ---------------------------------------------------------------------------
# DXF: lines, units, colors
# ---------------------------------------------------------------------------


def test_line_becomes_one_black_path():
    job = _parse([_line(10, 0, 20, 0)])
    assert job["items"] == [{"def": 0, "color": "#000000"}]
    assert job["defs"][0]["kind"] == "path"
    assert _flat(_paths(job)[0]) == pytest.approx([10, 0, 20, 0])


def test_y_axis_is_flipped_within_the_drawing():
    """CAD has y up, the bed has y down: a drawing is mirrored about its own extent."""
    path = _paths(_parse([_line(0, 10, 0, 30)]))[0]
    assert _flat(path) == pytest.approx([0, 30, 0, 10])


def test_line_direction_does_not_change_where_it_lands():
    forward = _paths(_parse([_line(0, 10, 0, 30)]))[0]
    backward = _paths(_parse([_line(0, 30, 0, 10)]))[0]
    assert sorted(map(tuple, backward)) == sorted(map(tuple, forward))


@pytest.mark.parametrize(
    "units,value,mm",
    [
        (None, 1, 25.4),  # no $INSUNITS at all: read as unitless
        (0, 1, 25.4),  # unitless is treated as inches
        (1, 2, 50.8),
        (2, 1, 304.8),
        (4, 7.5, 7.5),
        (5, 3, 30.0),
        (6, 1.2, 1200.0),
        (8, 78740.1574, 2.0),
        (9, 78.74, 2.0),
        (10, 1, 914.4),
    ],
)
def test_units_are_converted_to_mm(units, value, mm):
    job = _parse([_line(0, 0, value, 0)], insunits=units)
    assert _paths(job)[0][1][0] == pytest.approx(mm, abs=1e-3)


def test_forced_unit_overrides_the_header(capsys):
    job = _parse([_line(0, 0, 3, 0)], forced_unit=5, insunits=4)
    assert _paths(job)[0][1][0] == pytest.approx(30.0)
    assert "forced_unit" in capsys.readouterr().out


@pytest.mark.parametrize("forced", [0, None])
def test_forced_unit_zero_or_none_falls_back_to_header(forced):
    job = _parse([_line(0, 0, 3, 0)], forced_unit=forced, insunits=5)
    assert _paths(job)[0][1][0] == pytest.approx(30.0)


@pytest.mark.parametrize("units", [3, 7, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
def test_units_with_no_conversion_are_refused(units):
    with pytest.raises(RuntimeError, match="convert INSUNIT"):
        _parse([_line(0, 0, 1, 0)], insunits=units)


def test_unknown_unit_code_is_refused():
    with pytest.raises(RuntimeError):
        _parse([_line(0, 0, 1, 0)], insunits=99)


def test_layer_color_and_entity_color_decide_the_pass():
    layers = (("0", 7), ("RED", 1), ("BLUE", 5))
    job = _parse(
        [
            _line(0, 0, 10, 0, layer="RED"),
            _line(0, 5, 10, 5, layer="BLUE"),
            _line(0, 9, 10, 9, layer="RED", color=3),  # own color beats the layer's
            _line(0, 20, 10, 20),
        ],
        layers=layers,
    )
    colors = {
        job["items"][i]["color"]: len(job["defs"][i]["data"]) for i in range(len(job["items"]))
    }
    assert colors == {"#FF0000": 1, "#00FF00": 1, "#0000FF": 1, "#000000": 1}


def test_unrecognized_color_falls_back_to_cyan_alongside_real_cyan():
    job = _parse(
        [_line(0, 0, 5, 0, color=4), _line(0, 5, 5, 5, color=30)],
    )
    assert [i["color"] for i in job["items"]] == ["#00FFFF"]
    assert len(job["defs"][0]["data"]) == 2


def test_drawing_without_entities_is_an_empty_job():
    job = _parse([])
    assert job["items"] == [] and job["defs"] == []


def test_crlf_input_is_accepted():
    parser = DXFParser(0.1, BED)
    parser.verbose = False
    job = parser.parse(_dxf([_line(0, 0, 10, 0)]).replace("\n", "\r\n"), None)
    assert _flat(_paths(job)[0]) == pytest.approx([0, 0, 10, 0])


def test_debug_mode_does_not_change_the_result(capsys):
    parser = DXFParser(0.1, BED)
    parser.debug = True
    job = parser.parse(_dxf([_line(0, 0, 10, 0)], layers=(("0", 3),)), None)
    assert job["items"][0]["color"] == "#00FF00"
    assert "set entity color to layer color" in capsys.readouterr().out


def test_negative_coordinates_are_shifted_onto_the_bed():
    path = _paths(_parse([_line(-5, 0, -3, 5)]))[0]
    assert _flat(path) == pytest.approx([0, 5, 2, 0])


@pytest.mark.parametrize(
    "line,where",
    [
        ((2000, 0, 0, 0), "x0"),
        ((0, 0, 2000, 0), "x1"),
        ((0, 700, 0, 0), "y0"),
        ((0, 0, 0, 700), "y1"),
    ],
)
def test_geometry_outside_the_bed_is_refused(line, where):
    with pytest.raises(RuntimeError) as err:
        _parse([_line(*line)])
    assert f"bounds {where}" in str(err.value)


# ---------------------------------------------------------------------------
# DXF: curves and polylines
# ---------------------------------------------------------------------------


def test_circle_is_a_closed_ring_within_tolerance():
    path = _paths(_parse([_circle(50, 50, 10)]))[0]
    assert path[0] == pytest.approx(path[-1])
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    assert (min(xs), max(xs)) == pytest.approx((40, 60), abs=1e-3)
    assert (max(ys) - min(ys)) == pytest.approx(20, abs=1e-3)
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    radii = [math.hypot(x - cx, y - cy) for x, y in path]
    assert radii == pytest.approx([10.0] * len(radii), abs=0.01)
    assert len(path) > 8  # more than a handful of facets


def test_finer_tolerance_gives_more_circle_segments():
    coarse = DXFParser(1.0, BED)
    fine = DXFParser(0.01, BED)
    for parser in (coarse, fine):
        parser.verbose = False
    n_coarse = len(_paths(coarse.parse(_dxf([_circle(50, 50, 10)]), None))[0])
    n_fine = len(_paths(fine.parse(_dxf([_circle(50, 50, 10)]), None))[0])
    assert n_fine > n_coarse


def _circumcenter(p, q, r):
    ax, ay = p
    bx, by = q
    cx, cy = r
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = (
        (ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)
    ) / d
    uy = (
        (ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)
    ) / d
    return ux, uy


@pytest.mark.parametrize(
    "a0,a1,sweep_deg",
    [(0, 90, 90), (270, 90, 180), (350, 10, 20)],  # last one wraps through 0 degrees
)
def test_arc_is_a_circular_segment_of_the_right_sweep(a0, a1, sweep_deg):
    parser = DXFParser(0.1, BED)
    parser.verbose = False
    path = _paths(parser.parse(_dxf([_arc(50, 50, 10, a0, a1)]), None))[0]
    center = _circumcenter(path[0], path[len(path) // 2], path[-1])
    radii = [math.dist(p, center) for p in path]
    assert radii == pytest.approx([10.0] * len(radii), abs=0.02)
    assert math.dist(path[0], path[-1]) == pytest.approx(
        2 * 10 * math.sin(math.radians(sweep_deg) / 2), abs=0.02
    )
    # the importer mirrors y, but x of the end points is untouched
    x_start = 50 + 10 * math.cos(math.radians(a0))
    x_end = 50 + 10 * math.cos(math.radians(a1))
    assert (path[0][0], path[-1][0]) == pytest.approx((x_start, x_end), abs=0.02)


def test_arc_imports_with_the_installed_dxfgrabber():
    job = _parse([_arc(50, 50, 10, 0, 90)])
    path = _paths(job)[0]
    assert math.dist(path[0], path[-1]) == pytest.approx(10 * math.sqrt(2), abs=0.02)


def test_lwpolyline_keeps_its_vertices_in_order():
    path = _paths(_parse([_lwpolyline([(0, 0), (10, 0), (10, 10)])]))[0]
    assert _flat(path) == pytest.approx([0, 10, 10, 10, 10, 0])


def test_closed_lwpolyline_returns_to_its_start():
    path = _paths(_parse([_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], closed=True)]))[0]
    assert path[0] == pytest.approx(path[-1])


def test_lwpolyline_bulge_becomes_an_arc():
    path = _paths(_parse([_lwpolyline([(0, 20), (10, 20)], bulges=[1.0, 0.0])]))[0]
    assert len(path) > 2  # a half circle, not a straight chord
    assert path[0] == pytest.approx([0, 15]) and path[-1] == pytest.approx([10, 15])
    # bulge 1 = counter-clockwise half circle of radius 5 around (5, 20): it dips
    # below the chord in CAD, which the importer's y mirror turns into a larger y
    assert [math.dist(p, (5, 15)) for p in path] == pytest.approx([5.0] * len(path), abs=0.02)
    assert max(p[1] for p in path) == pytest.approx(20, abs=0.02)


def test_lwpolyline_negative_bulge_curves_the_other_way():
    path = _paths(_parse([_lwpolyline([(0, 20), (10, 20)], bulges=[-1.0, 0.0])]))[0]
    assert min(p[1] for p in path) == pytest.approx(20, abs=0.02)
    assert path[0] == pytest.approx([0, 25])


def test_closed_lwpolyline_bulge_on_the_closing_edge():
    pts = [(0, 20), (10, 20), (10, 30)]
    path = _paths(_parse([_lwpolyline(pts, closed=True, bulges=[0, 0, 1.0])]))[0]
    assert path[0] == pytest.approx(path[-1])
    assert len(path) > 4  # the closing edge is an arc, not a chord


def test_entity_on_a_layer_missing_from_the_table_is_still_imported():
    job = _parse([_line(0, 0, 10, 0, layer="GHOST")])
    assert [i["color"] for i in job["items"]] == ["#000000"]
    assert _flat(_paths(job)[0]) == pytest.approx([0, 0, 10, 0])


@pytest.mark.parametrize(
    "entity,message",
    [
        (_pairs((0, "SPLINE"), (8, "0")), "TODO ADD"),
        (_pairs((0, "POINT"), (8, "0"), (10, 1), (20, 1), (30, 0)), "unknown entity"),
        (
            _pairs((0, "ELLIPSE"), (8, "0"), (10, 0), (20, 0), (30, 0), (11, 10), (21, 0), (31, 0))
            + _pairs((40, 0.5), (41, 0), (42, 6.28)),
            "unknown entity",
        ),
        (
            _pairs((0, "POLYLINE"), (8, "0"), (66, 1), (70, 0))
            + _pairs((0, "VERTEX"), (8, "0"), (10, 0), (20, 0))
            + _pairs((0, "VERTEX"), (8, "0"), (10, 5), (20, 5))
            + _pairs((0, "SEQEND"), (8, "0")),
            "unknown entity",
        ),
    ],
)
def test_unsupported_entities_are_reported_and_skipped(entity, message, capsys):
    parser = DXFParser(0.1, BED)  # verbose by default
    job = parser.parse(_dxf([entity, _line(0, 0, 5, 5)]), None)
    out = capsys.readouterr().out
    assert message in out
    assert len(_paths(job)) == 1  # only the LINE survived
    assert _flat(_paths(job)[0]) == pytest.approx([0, 5, 5, 0])


def test_verbose_run_reports_the_drawing(capsys):
    DXFParser(0.1, BED).parse(_dxf([_line(0, 0, 5, 5)]), None)
    out = capsys.readouterr().out
    assert "units" in out and "mm" in out and "entitiy count" in out


# ---------------------------------------------------------------------------
# DXF: helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "axis,expected",
    [("X", [3, -4]), ("Y", [-3, 4]), ("Z", [-3, -4])],
)
def test_flip_path_axis(axis, expected):
    parser = DXFParser(0.1, BED)
    assert _flat(parser.flipPathAxis([[3.0, 4.0]], axis)) == pytest.approx(expected)


def test_flip_path_axis_tracks_the_extent():
    parser = DXFParser(0.1, BED)
    parser.flipPathAxis([[3.0, 4.0], [9.0, 1.0]], "X")
    assert (parser.x_min, parser.x_max) == (3.0, 9.0)


def test_complain_spline_raises(capsys):
    with pytest.raises(RuntimeError):
        DXFParser(0.1, BED).complain_spline()
    assert "SPLINE" in capsys.readouterr().out


def test_complain_invalid_names_the_line(capsys):
    parser = DXFParser(0.1, BED)
    parser.line, parser.linecount = "WHAT", 12
    parser.complain_invalid()
    assert "WHAT" in capsys.readouterr().out


def test_unitize_before_parse_fails_cleanly():
    parser = DXFParser(0.1, BED)
    parser.units = 3
    parser.unitsString = "miles"
    with pytest.raises(RuntimeError, match="miles"):
        parser.unitize(1)


def test_dxf_fixture_through_the_converter(testjobs_dir):
    with open(os.path.join(testjobs_dir, "circlesquare.dxf")) as fp:
        job = jobimport.convert(fp.read())
    assert job["head"]["optimized"] == conf["tolerance"]
    assert job["items"] and all(d["kind"] == "path" for d in job["defs"])
    for path in _paths(job):
        for x, y in path:
            assert 0 <= x <= BED[0] and 0 <= y <= BED[1]


def test_read_dxf_dedupes_stacked_lines():
    dxf = _dxf([_line(0, 0, 10, 0), _line(10, 0, 0, 0)])
    job = jobimport.read_dxf(dxf, 0.1, optimize=False)
    assert len(_paths(job)) == 1
    assert "optimized" not in job["head"]


# ---------------------------------------------------------------------------
# G-code reader
# ---------------------------------------------------------------------------


def _gcode(text):
    return GcodeReader().parse(text)


def _data(job, i=0):
    return job["defs"][i]["data"]


def test_motion_modes_and_modal_coordinates():
    job = _gcode("T1 M6\nG0 X1 Y2\nG1 X5\nY7 Z-1.5\nG00 X0\n")
    assert _data(job) == [
        ("G0", (1, 2, None)),
        ("G1", (5, 2, None)),
        ("G1", (5, 7, -1.5)),
        ("G0", (0, 7, -1.5)),
    ]


def test_numbers_are_ints_when_whole_else_floats():
    data = _data(_gcode("T1 M6\nG1 X10 Y-2.50 Z1e-3\n"))
    ((_, (x, y, z)),) = data
    assert (x, type(x)) == (10, int)
    assert (y, type(y)) == (-2.5, float)
    assert z == pytest.approx(0.001)


def test_feed_rate_is_emitted_before_its_move_and_collected():
    job = _gcode("T1 M6\nG1 X1 F500\nG1 X2 F800\nG1 X3\n")
    assert _data(job) == [
        ("F", 500),
        ("G1", (1, None, None)),
        ("F", 800),
        ("G1", (2, None, None)),
        ("G1", (3, None, None)),
    ]
    assert job["defs"][0]["rates"] == [500, 800]


def test_spindle_on_off_maps_to_intensity():
    job = _gcode("T1 M6\nS1200\nM3\nG1 X1\nM5\nG0 X2\nM3\n")
    assert [a for a in _data(job) if a[0] == "S"] == [("S", 1200), ("S", 0), ("S", 1200)]
    assert job["defs"][0]["freqs"] == [1200, 1200]


def test_s_on_the_m3_line_counts():
    job = _gcode("T1 M6\nM3 S18000\n")
    assert _data(job) == [("S", 18000)]


def test_coolant_changes_are_only_reported_on_change():
    job = _gcode("T1 M6\nM7\nM7\nM8\nM9\nM9\n")
    assert _data(job) == [("MIST", True), ("FLOOD", True), ("MIST", False), ("FLOOD", False)]
    assert job["defs"][0]["mists"] is True and job["defs"][0]["floods"] is True


def test_actions_on_one_line_are_ordered_coolant_spindle_feed_motion():
    job = _gcode("T1 M6\nG1 X1 F300 M3 S100 M7 M8\n")
    assert [a[0] for a in _data(job)] == ["MIST", "FLOOD", "S", "F", "G1"]


def test_pass_flags_default_to_off():
    job = _gcode("T1 M6\nG1 X1\n")
    d = job["defs"][0]
    assert (d["rates"], d["freqs"], d["mists"], d["floods"]) == ([], [], False, False)


def test_each_tool_change_starts_a_new_pass_with_fresh_settings():
    job = _gcode("(T1 6mm endmill)\n(T2 laser)\nT1 M6\nG1 X1 F100\nT2 M6\nG1 X2\nM6\n")
    assert [d["tool"] for d in job["defs"]] == ["T1", "T2", "T2"]
    assert [d["toolinfo"] for d in job["defs"]] == ["6mm endmill", "laser", "laser"]
    assert job["defs"][0]["rates"] == [100]
    assert job["defs"][1]["rates"] == []
    assert _data(job, 1) == [("G1", (2, None, None))]


def test_tool_selected_earlier_is_used_by_a_bare_m6():
    job = _gcode("T3\nM6\nG1 X1\n")
    assert job["defs"][0]["tool"] == "T3"
    assert job["defs"][0]["toolinfo"] == ""


def test_toolchange_line_with_other_words_is_rejected(capsys):
    job = _gcode("G1 X5 M6\n")
    assert job["defs"] == []
    assert "cannot handle anything but T on M6" in capsys.readouterr().out


def test_motion_before_any_tool_is_dropped(capsys):
    job = _gcode("G1 X1 Y1\n")
    assert job["defs"] == []
    assert "no tool defined" in capsys.readouterr().out


def test_comments_blank_and_foreign_lines_are_ignored():
    text = "; header\n\n(just a comment)\n%\nN10 G1 X99\nT1 M6\n(move) \nG1 X1 (inline note)\n"
    job = _gcode(text)
    assert _data(job) == [("G1", (1, None, None))]


def test_head_marks_the_job_as_mill():
    assert _gcode("")["head"] == {"kind": "mill"}
    assert _gcode("")["defs"] == []


@pytest.mark.parametrize(
    "line,message",
    [
        ("G2 X1 Y1", "G2,G3 arc"),
        ("G3 X1 Y1", "G2,G3 arc"),
        ("G4 P1", "G4 dwell"),
        ("G53 Z0", "G53"),
        ("G57", "G55-G59"),
        ("G92 X0", "G92"),
        ("G93", "G93,G95"),
        ("G95", "G93,G95"),
        ("M0", "M0,M1"),
        ("M1", "M0,M1"),
        ("M4", "M4"),
    ],
)
def test_unsupported_codes_are_reported(line, message, capsys):
    _gcode(f"T1 M6\n{line}\n")
    assert message in capsys.readouterr().out


def test_unsupported_arc_is_flattened_to_a_straight_move(capsys):
    job = _gcode("T1 M6\nG1 X0 Y0\nG2 X5 Y5 I2 J2\n")
    assert _data(job)[-1] == ("G1", (5, 5, None))
    assert "arc motions not supported" in capsys.readouterr().out


def test_lowercase_gcode_is_read():
    job = _gcode("t1 m6\ng1 x5 y5\n")
    assert _data(job) == [("G1", (5, 5, None))]


def test_lowercase_inline_comment_is_not_code():
    job = _gcode("t1 m6\ng1 x5 (then go to y9)\n")
    assert _data(job) == [("G1", (5, None, None))]


def test_g20_inch_mode_is_scaled_to_mm_until_g21():
    job = _gcode("T1 M6\nG20\nG1 X1 Y2 F10\nG21\nG1 X3\n")
    assert _data(job) == [
        ("F", 254),
        ("G1", (25.4, 50.8, None)),
        ("G1", (3, 50.8, None)),
    ]
    assert job["defs"][0]["rates"] == [254]


def test_g91_relative_moves_accumulate_until_g90():
    job = _gcode("T1 M6\nG0 X10 Y10\nG91\nG1 X5\nG1 X5 Y-2\nG90\nG1 X1\n")
    assert _data(job) == [
        ("G0", (10, 10, None)),
        ("G1", (15, 10, None)),
        ("G1", (20, 8, None)),
        ("G1", (1, 8, None)),
    ]


def test_g91_relative_moves_in_inches():
    job = _gcode("T1 M6\nG20 G91\nG1 X1\nG1 X1\n")
    assert _data(job) == [("G1", (25.4, None, None)), ("G1", (50.8, None, None))]


def test_gcode_dispatch_returns_mill_job_without_optimizing():
    job = jobimport.convert("T1 M6\nG0 X0 Y0\nG1 X5 Y5 F800\n")
    assert job["head"] == {"kind": "mill"}
    assert _data(job) == [("G0", (0, 0, None)), ("F", 800), ("G1", (5, 5, None))]


# ---------------------------------------------------------------------------
# converter gaps
# ---------------------------------------------------------------------------


def _dba(*paths_by_color):
    job = {"head": {}, "passes": [], "items": [], "defs": []}
    for color, paths in paths_by_color:
        job["defs"].append({"kind": "path", "data": paths})
        job["items"].append({"def": len(job["defs"]) - 1, "color": color})
    return job


def test_convert_accepts_dba_as_bytes_and_str():
    job = _dba(("#ff0000", [[[0.0, 0.0], [10.0, 0.0]]]))
    raw = json.dumps(job)
    for payload in (raw.encode("utf-8"), raw):
        out = jobimport.convert(payload, optimize=False)
        assert out["defs"][0]["data"] == [[[0.0, 0.0], [10.0, 0.0]]]


@pytest.mark.parametrize("junk", [42, None, ["x"], "just some text", b"\xff\xfe nothing"])
def test_convert_rejects_unrecognized_input(junk, capsys):
    with pytest.raises(TypeError):
        jobimport.convert(junk)
    assert "ERROR" in capsys.readouterr().out


def test_convert_applies_the_alignment_matrix_to_paths_and_skips_images():
    job = _dba(("#ff0000", [[[0.0, 0.0], [10.0, 0.0]]]))
    job["defs"].append({"kind": "image", "data": "", "pos": [1.0, 1.0], "size": [2.0, 2.0]})
    job["items"].append({"def": 1})
    matrix = [[1, 0, 5], [0, 2, 7], [0, 0, 1]]  # x+5, y*2+7
    out = jobimport.convert(job, optimize=False, matrix=matrix)
    assert _flat(out["defs"][0]["data"][0]) == pytest.approx([5, 7, 15, 7])
    assert out["defs"][1]["pos"] == [1.0, 1.0]  # rasters are left alone


def test_apply_alignment_matrix_rotation():
    job = _dba(("#ff0000", [[[1.0, 0.0], [0.0, 1.0]]]))
    jobimport.apply_alignment_matrix(job, [[0, -1, 0], [1, 0, 0], [0, 0, 1]])  # 90 deg
    assert _flat(job["defs"][0]["data"][0]) == pytest.approx([0, 1, -1, 0])


def _fill_job():
    fill = [
        [[0.0, 0.0], [10.0, 0.0]],
        [[0.0, 1.0], [10.0, 1.0]],
        [[0.0, 2.0], [10.0, 2.0]],
    ]
    return {
        "head": {},
        "passes": [],
        "items": [{"def": 0, "color": "#000000"}],
        "defs": [{"kind": "fill", "data": fill}],
    }


@pytest.mark.parametrize(
    "mode,second_row",
    [
        ("Forward", [[0.0, 1.0], [10.0, 1.0]]),
        ("Reverse", [[10.0, 1.0], [0.0, 1.0]]),
        ("Bidirectional", [[10.0, 1.0], [0.0, 1.0]]),
        ("NearestNeighbor", None),
        ("nonsense", [[10.0, 1.0], [0.0, 1.0]]),  # falls back to Bidirectional with a warning
    ],
)
def test_fill_mode_picks_the_engraving_order(mode, second_row, monkeypatch, capsys):
    monkeypatch.setitem(conf, "fill_mode", mode)
    job = _fill_job()
    converter.optimize_job(job, 0.01)
    rows = job["defs"][0]["data"]
    if second_row is not None:
        assert rows[1] == second_row
    if mode == "Reverse":
        assert rows[0] == [[10.0, 0.0], [0.0, 0.0]]
    if mode == "Forward":
        assert rows[0] == [[0.0, 0.0], [10.0, 0.0]]
    if mode == "NearestNeighbor":
        assert len(rows) >= 1  # joined by the path optimizer
    assert job["head"]["optimized"] == 0.01
    assert ("fill_mode not recognized" in capsys.readouterr().out) == (mode == "nonsense")


def test_optimize_job_creates_a_missing_head():
    job = {"items": [], "defs": []}
    converter.optimize_job(job, 0.5)
    assert job["head"] == {"optimized": 0.5}


class _FakeSVGReader:
    def __init__(self, result):
        self.result = result

    def __call__(self, tolerance, workspace):
        return self

    def parse(self, svg, dpi, require_unit):
        return self.result


_LASERTAG_RES = {
    "dpi": 90,
    "boundarys": {
        "#ff0000": [[[0.0, 0.0], [10.0, 0.0]]],
        "#0000ff": [[[0.0, 5.0], [10.0, 5.0]]],
        "#00ff00": [[[0.0, 9.0], [10.0, 9.0]]],
    },
}


def _lasertag_job(monkeypatch, tags):
    monkeypatch.setattr(
        converter, "SVGReader", _FakeSVGReader({**_LASERTAG_RES, "lasertags": tags})
    )
    return converter.read_svg("<svg/>", 0.01, optimize=False)


def test_svg_lasertags_become_passes(monkeypatch):
    # the format svg_tag_reader emits: [pass, feedrate, unit, intensity, unit, color x6]
    job = _lasertag_job(
        monkeypatch,
        [
            [2, 1500, "", 50, "%", "#0000ff", "", "", "", "", ""],
            [1, 3000, "", 80, "%", "#ff0000", "#00ff00", "", "", "", ""],
            [3, 1, "", 1, "%"],  # malformed: wrong arity, ignored
        ],
    )
    assert job["head"]["dpi"] == 90
    assert "warnings" not in job["head"]
    colors = {i: it["color"] for i, it in enumerate(job["items"])}
    first, second = job["passes"]  # sorted by pass number
    assert (first["feedrate"], first["intensity"]) == (3000, 80)
    assert {colors[i] for i in first["items"]} == {"#ff0000", "#00ff00"}
    assert (second["feedrate"], second["intensity"]) == (1500, 50)
    assert {colors[i] for i in second["items"]} == {"#0000ff"}


def test_svg_lasertag_color_without_items_gives_an_empty_pass(monkeypatch):
    job = _lasertag_job(monkeypatch, [[1, 100, "", 5, "%", "#123456", "", "", "", "", ""]])
    assert job["passes"] == [{"items": [], "feedrate": 100, "intensity": 5}]


def test_svg_lasertag_sixth_color_is_honored(monkeypatch):
    job = _lasertag_job(
        monkeypatch,
        [[1, 100, "", 5, "%", "#ff0000", "#0000ff", "", "", "", "#00ff00"]],
    )
    colors = {job["items"][i]["color"] for i in job["passes"][0]["items"]}
    assert "#00ff00" in colors


def test_svg_with_stale_source_keeps_it_for_preview(monkeypatch):
    big = Image.new("RGB", (300, 20), (10, 20, 30))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    res = {
        "rasters": [
            {
                "data": "data:cropped",
                "source_data": uri,
                "source": "s1",
                "pos": [0, 0],
                "size": [1, 1],
            },
            {"data": uri, "source_data": uri, "source": "s2", "pos": [5, 5], "size": [1, 1]},
        ]
    }
    monkeypatch.setattr(converter, "SVGReader", _FakeSVGReader(res))
    job = converter.read_svg("<svg/>", 0.01, optimize=False)
    # only the clipped copy needs its original kept
    assert list(job["sources"]) == ["s1"]


def test_convert_downscales_sources_once_per_distinct_image():
    big = Image.new("RGB", (600, 300), (1, 2, 3))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    job = _dba()
    job["sources"] = {"a": uri, "b": uri}
    out = jobimport.convert(job, optimize=False)
    assert out["sources"]["a"] == out["sources"]["b"] != uri
    with Image.open(io.BytesIO(base64.b64decode(out["sources"]["a"].split(",", 1)[1]))) as img:
        assert img.size == (256, 128)


# ---------------------------------------------------------------------------
# dedupe gaps
# ---------------------------------------------------------------------------


def _items_job(defs, items, **extra):
    return {"head": {}, "passes": [], "items": items, "defs": defs, **extra}


def test_dedupe_ignores_items_pointing_nowhere_and_unknown_kinds():
    defs = [{"kind": "mystery", "data": [1]}, {"kind": "path", "data": [[[0, 0], [1, 1]]]}]
    items = [{"def": "x"}, {"def": 9}, {"def": -1}, {"def": 0}, {"def": 1, "color": "#000"}]
    job = dedupe.dedupe_job(_items_job(defs, items))
    # unknown kind and bad indices are left exactly as they were
    assert len(job["items"]) == 5
    assert job["defs"][1]["data"] == [[[0, 0], [1, 1]]]


def test_dedupe_drops_the_same_def_drawn_twice_in_one_color(capsys):
    defs = [{"kind": "path", "data": [[[0, 0], [5, 0]]]}]
    items = [{"def": 0, "color": "#f00"}, {"def": 0, "color": "#f00"}, {"def": 0, "color": "#00f"}]
    job = _items_job(defs, items, passes=[{"items": [0, 1, 2]}])
    dedupe.dedupe_job(job)
    assert [i["color"] for i in job["items"]] == ["#f00", "#00f"]
    assert job["passes"][0]["items"] == [0, 1]
    assert job["defs"][0]["data"] == [[[0, 0], [5, 0]]]  # shared data untouched
    assert "1 duplicate item" in capsys.readouterr().out


def test_dedupe_log_can_be_silenced(capsys):
    defs = [{"kind": "path", "data": [[[0, 0], [5, 0]], [[5, 0], [0, 0]]]}]
    job = dedupe.dedupe_job(_items_job(defs, [{"def": 0, "color": "#f00"}]), log_dropped=False)
    assert len(job["defs"][0]["data"]) == 1
    assert capsys.readouterr().out == ""


def test_dedupe_skips_images_without_data_and_paths_that_are_not_lists():
    defs = [
        {"kind": "image", "pos": [0, 0], "size": [1, 1]},
        {"kind": "image", "pos": [0, 0], "size": [1, 1]},
        {"kind": "path", "data": "oops"},
    ]
    job = dedupe.dedupe_job(_items_job(defs, [{"def": 0}, {"def": 1}, {"def": 2, "color": "#000"}]))
    assert len(job["items"]) == 3 and len(job["defs"]) == 3


def test_dedupe_placement_key_passes_non_lists_through():
    assert dedupe._placement_key(None) is None
    assert dedupe._placement_key("fit") == "fit"
    assert dedupe._placement_key([1.00001, "a", 2]) == (1.0, "a", 2)


def test_dedupe_drops_fully_duplicated_path_def_and_prunes_unused_sources():
    sq = [[0, 0], [4, 0], [4, 4]]
    defs = [
        {"kind": "path", "data": [sq]},
        {"kind": "path", "data": [sq[::-1]]},
        {"kind": "image", "data": "A", "pos": [0, 0], "size": [1, 1], "source": "keep"},
        {"kind": "image", "data": "A", "pos": [0, 0], "size": [1, 1], "source": "gone"},
    ]
    items = [{"def": i, "color": "#000"} for i in range(4)]
    job = _items_job(defs, items, sources={"keep": "K", "gone": "G"})
    dedupe.dedupe_job(job)
    assert [d["kind"] for d in job["defs"]] == ["path", "image"]
    assert job["items"] == [{"def": 0, "color": "#000"}, {"def": 1, "color": "#000"}]
    assert job["sources"] == {"keep": "K"}


def test_dedupe_empty_job_is_fine():
    assert dedupe.dedupe_job({}) == {}


# ---------------------------------------------------------------------------
# imagedata gaps
# ---------------------------------------------------------------------------


def _uri(img, fmt, mime):
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def _open(uri):
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))


@pytest.mark.parametrize("fn", [imagedata.normalize_image_data, imagedata.preview_image_data])
@pytest.mark.parametrize("empty", ["", None])
def test_empty_image_data_passes_through(fn, empty):
    assert fn(empty) is empty


@pytest.mark.parametrize("fn", [imagedata.normalize_image_data, imagedata.preview_image_data])
def test_unreadable_image_data_is_returned_unchanged(fn, caplog):
    bad = "data:image/png;base64,bm90IGFuIGltYWdl"
    with caplog.at_level("WARNING", logger="svg_reader"):
        assert fn(bad) == bad
    assert "Failed to" in caplog.text


def test_cmyk_tiff_is_flattened_to_a_png():
    uri = _uri(Image.new("CMYK", (4, 4), (0, 255, 255, 0)), "TIFF", "tiff")
    out = imagedata.normalize_image_data(uri)
    assert out.startswith("data:image/png;base64,")
    with _open(out) as img:
        assert img.format == "PNG" and img.mode == "RGB"
        assert img.getpixel((0, 0)) == (255, 0, 0)  # CMYK red survives the flattening


def test_cmyk_tiff_preview_is_flattened_even_when_small():
    uri = _uri(Image.new("CMYK", (8, 4), (255, 0, 255, 0)), "TIFF", "tiff")
    out = imagedata.preview_image_data(uri)
    with _open(out) as img:
        assert img.format == "PNG" and img.mode == "RGB" and img.size == (8, 4)


def test_displayable_formats_are_untouched_by_normalize():
    uri = _uri(Image.new("RGB", (3, 3), (9, 9, 9)), "JPEG", "jpeg")
    assert imagedata.normalize_image_data(uri) == uri


def test_preview_downscales_keeping_aspect_ratio():
    uri = _uri(Image.new("RGB", (400, 100), (50, 60, 70)), "PNG", "png")
    with _open(imagedata.preview_image_data(uri, max_dim=100)) as img:
        assert img.size == (100, 25)
    assert imagedata.preview_image_data(uri, max_dim=400) == uri
