"""Unit tests for the SVG import helpers.

Covers the attribute reader (colors, transforms, units, style), the tag reader
(basic shapes), the path reader (every path command) and the text-to-path
converter. Geometry is checked through ``jobimport.convert`` so the real reader
is exercised end to end; helpers that need no document are called directly.

Tests marked ``xfail(strict=True)`` assert the correct behavior of a bug found
while writing them; remove the marker when the bug is fixed.
"""

import logging
import math
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import jobimport
import pytest
from jobimport import issues, svg_reader, svg_text_converter
from jobimport.svg_attribute_reader import SVGAttributeReader, parse_color
from jobimport.svg_text_converter import (
    FontManager,
    convert_text_to_paths,
    get_font_manager,
    get_style_attr,
    get_text_content,
    get_text_width,
    normalize_color,
    parse_font_size,
    text_to_path,
    transform_command,
    transform_path,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _svg(body, size="100mm", viewbox="0 0 100 100"):
    """A square document, one user unit to the mm, holding `body`."""
    return (
        '<?xml version="1.0"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{size}" height="{size}" viewBox="{viewbox}">{body}</svg>'
    )


def _job(body, **kw):
    return jobimport.convert(_svg(body, **kw))


def _flat(job, color=None):
    """Every path coordinate of the job flat (approx cannot compare tuples)."""
    colors = {it["def"]: it.get("color") for it in job["items"]}
    return [
        v
        for i, d in enumerate(job["defs"])
        if d["kind"] == "path" and (color is None or colors.get(i) == color)
        for sub in d["data"]
        for pt in sub
        for v in pt
    ]


def _colors(job):
    return [it["color"] for it in job["items"]]


def _stroke(d, attrs=""):
    """Flat coordinates of an unfilled red path `d`."""
    return _flat(_job(f'<path d="{d}" stroke="#ff0000" fill="none" {attrs}/>'))


def _subpaths(d):
    """The polylines of an unfilled path, as read (no optimizer reordering)."""
    svg = _svg(f'<path d="{d}" stroke="#ff0000" fill="none"/>')
    return jobimport.convert(svg, optimize=False)["defs"][0]["data"]


def _shape(tag, attrs, fill="none"):
    return _job(f'<{tag} {attrs} stroke="#ff0000" fill="{fill}"/>')


def _bbox(flat):
    xs, ys = flat[0::2], flat[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]


def _pairs(flat):
    return list(zip(flat[0::2], flat[1::2]))


def _read(attr, value, px2mm=None, node=None):
    node = {} if node is None else node
    SVGAttributeReader(SimpleNamespace(px2mm=px2mm)).read_attrib(node, attr, value)
    return node


# ---------------------------------------------------------------------------
# parse_color (PIL.ImageColor backed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "val,expected",
    [
        ("#f00", "#ff0000"),
        ("#FfA", "#ffffaa"),
        ("#ABCDEF", "#abcdef"),
        ("#abcdef", "#abcdef"),
        ("#f008", "#ff0000"),  # alpha dropped
        ("#11223344", "#112233"),
        ("  #00ff00", "#00ff00"),  # leading blanks stripped
    ],
)
def test_parse_color_hex(val, expected):
    assert parse_color(val) == expected


@pytest.mark.parametrize("val", ["#12", "#12345", "#ggg", "#"])
def test_parse_color_invalid_hex_is_skipped(val):
    assert parse_color(val) == "inherit"


@pytest.mark.parametrize(
    "val,expected",
    [("rgb(255, 0, 0)", "#ff0000"), ("rgb(10,20,30)", "#0a141e"), ("rgb( 1 2 3 )", "#010203")],
)
def test_parse_color_rgb(val, expected):
    assert parse_color(val) == expected


def test_parse_color_rgba_drops_alpha():
    assert parse_color("rgba(1, 2, 3, 0.5)") == "#010203"


@pytest.mark.parametrize(
    "val,expected",
    [
        ("red", "#ff0000"),
        ("black", "#000000"),
        ("mediumpurple", "#9370db"),
        ("palevioletred", "#db7093"),
        ("rebeccapurple", "#663399"),
    ],
)
def test_parse_color_named(val, expected):
    assert parse_color(val) == expected


def test_parse_color_keywords():
    assert parse_color("none") == "none"
    assert parse_color("currentColor") == "inherit"
    assert parse_color("inherit") == "inherit"


@pytest.mark.parametrize(
    "val,expected",
    [
        ("hsl(120, 100%, 50%)", None),
        ("url(#grad)", None),
        ("rgb(1, 2)", None),
        ("notacolor", "inherit"),
    ],
)
def test_parse_color_unsupported(val, expected):
    assert parse_color(val) == expected


def test_parse_color_rgb_clamps_channels():
    assert parse_color("rgb(300, 0, 0)") == "#ff0000"


def test_parse_color_rgb_percentages():
    assert parse_color("rgb(100%, 0%, 0%)") == "#ff0000"


def test_parse_color_named_is_case_insensitive():
    assert parse_color("Red") == "#ff0000"


def test_malformed_hex_paint_does_not_abort_import():
    # skipped like a garbage name: the attribute is ignored, the default stroke stays
    job = _job('<rect width="10" height="10" stroke="#12" fill="none"/>')
    assert (
        job["defs"] == _job('<rect width="10" height="10" stroke="nonsense" fill="none"/>')["defs"]
    )
    assert len(job["defs"]) == 1


# ---------------------------------------------------------------------------
# normalize_color (text converter)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "val,expected",
    [
        ("  RED ", "#ff0000"),
        ("#ABC", "#aabbcc"),
        ("rgb(0, 128, 255)", "#0080ff"),
        ("MediumPurple", "#9370db"),
        (None, "#000000"),
        ("", "#000000"),
        ("   ", "#000000"),
        ("none", "#000000"),
        ("inherit", "#000000"),
        ("currentColor", "#000000"),
        ("#12", "#000000"),
        ("nonsense", "#000000"),
        ("url(#g)", "#000000"),
    ],
)
def test_normalize_color(val, expected):
    assert normalize_color(val) == expected


# ---------------------------------------------------------------------------
# attribute reader: colors, opacity, clip-path, style, strings
# ---------------------------------------------------------------------------


def test_color_attrib_sets_hex_and_ignores_inherit():
    assert _read("stroke", "red") == {"stroke": "#ff0000"}
    assert _read("fill", "currentColor", node={"fill": "#00ff00"}) == {"fill": "#00ff00"}
    assert _read("fill", "none") == {"fill": "none"}


def test_color_attrib_paint_server_reference():
    node = _read("fill", "url( '#pat' )", node={"fill": "#000000"})
    assert node == {"fill": None, "fill-ref": "pat"}
    # a plain color afterwards drops the stale reference
    node = _read("fill", "#00ff00", node=node)
    assert node["fill"] == "#00ff00"
    assert "fill-ref" not in node


@pytest.mark.parametrize(
    "value,expected",
    [("0.5", 0.5), ("2", 1.0), ("-3", 0.0), ("0", 0.0), ("abc", 1.0)],
)
def test_opacity_is_clamped(value, expected):
    assert _read("opacity", value)["opacity"] == expected
    assert _read("fill-opacity", value)["fill-opacity"] == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("url(#clip1)", "clip1"),
        ("url('#clip1')", "clip1"),
        ('url( "#a-b" )', "a-b"),
        ("none", None),
        ("garbage", "unset"),  # left as it was
    ],
)
def test_clip_path_attrib(value, expected):
    assert _read("clip-path", value, node={"clip-path": "unset"})["clip-path"] == expected


def test_string_attribs_keep_value_unless_inherit():
    assert _read("display", "none") == {"display": "none"}
    assert _read("display", "inherit", node={"display": "inline"}) == {"display": "inline"}
    assert _read("id", "a1") == {"id": "a1"}
    assert _read("{http://www.w3.org/1999/xlink}href", "#x") == {"href": "#x"}
    assert _read("unknown-attr", "x") == {}
    assert _read("id", "   ") == {}


def test_style_attrib_feeds_the_presentation_handlers():
    node = _read("style", "stroke: #00f; fill:none ;opacity:.5;bogus:1; broken; a:b:c")
    assert node == {"stroke": "#0000ff", "fill": "none", "opacity": 0.5}


def test_style_attrib_cannot_recurse():
    assert _read("style", "style: stroke:red") == {}


def test_d_and_points_attribs():
    assert _read("d", "M10-5.5L1e1 2z")["d"] == ["M", 10.0, -5.5, "L", 10.0, 2.0, "z"]
    assert _read("points", "0,0 1,2 3,4")["points"] == [0.0, 0.0, 1.0, 2.0, 3.0, 4.0]
    with pytest.raises(issues.ImportFailed):  # odd count is rejected
        _read("points", "0,0 1")


# ---------------------------------------------------------------------------
# attribute reader: dimension units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10", 10.0),
        ("-2.5", -2.5),
        ("1in", 90.0),  # default 90 dpi
        ("2.54cm", 90.0),
        ("25.4mm", 90.0),
        ("72pt", 90.0),
        ("6pc", 90.0),
        ("50%", 50.0),  # unsupported units come back as the bare number
        ("2em", 2.0),
    ],
)
def test_dimension_units_default_dpi(value, expected):
    assert _read("width", value)["width"] == pytest.approx(expected)


def test_dimension_units_follow_px2mm():
    # one user unit = 0.5mm -> 50.8 dpi
    assert _read("width", "10mm", px2mm=0.5)["width"] == pytest.approx(20.0)
    assert _read("width", "1in", px2mm=0.5)["width"] == pytest.approx(50.8)


def test_dimension_invalid_is_none():
    assert _read("width", "abc")["width"] is None
    assert SVGAttributeReader(SimpleNamespace(px2mm=None))._parseUnit(None) is None


@pytest.mark.parametrize(
    "value,expected",
    [("10mm", 10.0), ("1cm", 10.0), ("1in", 25.4), ("72pt", 25.4), ("6pc", 25.4), ("7", 7.0)],
)
def test_dimension_units_in_a_document(value, expected):
    b = _bbox(_flat(_job(f'<rect width="{value}" height="{value}" stroke="#ff0000" fill="none"/>')))
    assert b[2] - b[0] == pytest.approx(expected)
    assert b[3] - b[1] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# attribute reader: transforms
# ---------------------------------------------------------------------------

_LINE = "M 0 0 L 10 0"
_DIAG = "M 0 0 L 10 10"


@pytest.mark.parametrize(
    "d,transform,expected",
    [
        (_LINE, "translate(5,7)", [5, 7, 15, 7]),
        (_LINE, "translate(5)", [5, 0, 15, 0]),
        (_DIAG, "scale(2)", [0, 0, 20, 20]),
        (_DIAG, "scale(2,3)", [0, 0, 20, 30]),
        (_LINE, "rotate(90)", [0, 0, 0, 10]),
        (_LINE, "rotate(90,10,10)", [20, 0, 20, 10]),
        (_LINE, "matrix(1 0 0 1 3 4)", [3, 4, 13, 4]),
        (_LINE, "matrix(0,1,-1,0,0,0)", [0, 0, 0, 10]),
        ("M 0 0 L 0 10", "skewX(45)", [0, 0, 10, 10]),
        (_LINE, "skewY(45)", [0, 0, 10, 10]),
        (_LINE, "translate(10,0) scale(2)", [10, 0, 30, 0]),  # applied right to left
        (_LINE, "scale(2) translate(10,0)", [20, 0, 40, 0]),
        (_LINE, "translate(1,2,3)", [0, 0, 10, 0]),  # bad param counts are skipped
        (_LINE, "rotate(1,2)", [0, 0, 10, 0]),
        (_LINE, "scale()", [0, 0, 10, 0]),
        (_LINE, "matrix(1,2,3)", [0, 0, 10, 0]),
        (_LINE, "skewX(1,2)", [0, 0, 10, 0]),
        (_LINE, "skewY()", [0, 0, 10, 0]),
    ],
)
def test_transforms(d, transform, expected):
    assert _stroke(d, f'transform="{transform}"') == pytest.approx(expected, abs=1e-9)


def test_transforms_accumulate_through_groups():
    job = _job(
        '<g transform="translate(10,0)"><g transform="scale(2)">'
        '<path d="M 0 0 L 5 0" stroke="#ff0000" fill="none" transform="translate(1,0)"/>'
        "</g></g>"
    )
    assert _flat(job) == pytest.approx([12, 0, 22, 0])


# ---------------------------------------------------------------------------
# tag reader: shapes
# ---------------------------------------------------------------------------


def test_rect_plain():
    assert _flat(_shape("rect", 'x="1" y="2" width="10" height="20"')) == pytest.approx(
        [1, 2, 11, 2, 11, 22, 1, 22, 1, 2]
    )


@pytest.mark.parametrize(
    "attrs,rx,ry",
    [
        ('rx="3" ry="2"', 3, 2),
        ('rx="3"', 3, 3),  # ry defaults to rx
        ('ry="2"', 2, 2),  # and the other way around
        ('rx="-3"', 3, 3),  # sign is dropped
        ('rx="50" ry="2"', 10, 2),  # clamped to half the width
        ('rx="2" ry="50"', 2, 10),
    ],
)
def test_rect_rounded_corners(attrs, rx, ry):
    flat = _flat(_shape("rect", f'x="5" y="5" width="20" height="20" {attrs}'))
    pts = _pairs(flat)
    # the optimizer may rotate where the loop starts, so look at what is on it
    assert any(x == pytest.approx(5 + rx) and y == pytest.approx(5) for x, y in pts)
    assert _bbox(flat) == pytest.approx([5, 5, 25, 25], abs=0.01)
    # the corner is cut off ...
    assert not any(x == pytest.approx(5) and y == pytest.approx(5) for x, y in pts)
    # ... with the arc running through the cut corner
    assert any(5 < x < 5 + rx and 5 < y < 5 + ry for x, y in pts)


def test_rect_stroke_and_fill_make_two_items():
    job = _shape("rect", 'width="10" height="10"', fill="#00ff00")
    assert _colors(job) == ["#ff0000", "#00ff00"]
    assert _flat(job, "#ff0000") == pytest.approx(_flat(job, "#00ff00"))


@pytest.mark.parametrize(
    "attrs",
    [
        'display="none"',
        'visibility="hidden"',
        'visibility="collapse"',
        'opacity="0"',
        'stroke-opacity="0"',
    ],
)
def test_invisible_shapes_are_dropped(attrs):
    assert (
        _job(f'<rect width="10" height="10" fill="none" stroke="#ff0000" {attrs}/>')["defs"] == []
    )


def test_stroke_none_is_not_drawn():
    assert _job('<rect width="10" height="10" fill="none" stroke="none"/>')["defs"] == []


def test_fill_opacity_zero_drops_only_the_fill():
    job = _job('<rect width="10" height="10" stroke="#ff0000" fill="#00ff00" fill-opacity="0"/>')
    assert _colors(job) == ["#ff0000"]


def test_style_overrides_presentation_attribute():
    job = _job('<rect width="10" height="10" fill="none" stroke="#ff0000" style="stroke:#0000ff"/>')
    assert _colors(job) == ["#0000ff"]


def test_line():
    assert _flat(_shape("line", 'x1="1" y1="2" x2="3" y2="4"')) == pytest.approx([1, 2, 3, 4])


def test_line_is_not_filled():
    job = _job('<line x1="1" y1="2" x2="3" y2="4" stroke="#ff0000"/>')
    assert _colors(job) == ["#ff0000"]


def test_polyline_and_polygon():
    assert _flat(_shape("polyline", 'points="0,0 5,5 10,0"')) == pytest.approx([0, 0, 5, 5, 10, 0])
    # a polygon closes itself
    assert _flat(_shape("polygon", 'points="0,0 5,5 10,0"')) == pytest.approx(
        [0, 0, 5, 5, 10, 0, 0, 0]
    )


@pytest.mark.parametrize("tag", ["polyline", "polygon"])
def test_polyline_and_polygon_fill(tag):
    job = _shape(tag, 'points="0,0 10,0 10,10"', fill="#00ff00")
    assert sorted(_colors(job)) == ["#00ff00", "#ff0000"]


def test_circle():
    flat = _flat(_shape("circle", 'cx="50" cy="40" r="10"'))
    assert _bbox(flat) == pytest.approx([40, 30, 60, 50], abs=1e-6)
    radii = [math.hypot(x - 50, y - 40) for x, y in _pairs(flat)]
    assert radii == pytest.approx([10.0] * len(radii), abs=0.1)
    assert flat[:2] == pytest.approx(flat[-2:])  # closed


def test_circle_with_fill_and_stroke():
    job = _shape("circle", 'cx="50" cy="50" r="5"', fill="#00ff00")
    assert _colors(job) == ["#ff0000", "#00ff00"]


@pytest.mark.parametrize("attrs", ['cx="5" cy="5" r="0"', 'cx="5" cy="5"', 'cx="5" cy="5" r="-3"'])
def test_circle_without_radius_is_dropped(attrs):
    assert _shape("circle", attrs)["defs"] == []


def test_ellipse():
    flat = _flat(_shape("ellipse", 'cx="50" cy="40" rx="20" ry="5"'))
    assert _bbox(flat) == pytest.approx([30, 35, 70, 45], abs=1e-6)
    on = [((x - 50) / 20) ** 2 + ((y - 40) / 5) ** 2 for x, y in _pairs(flat)]
    assert on == pytest.approx([1.0] * len(on), abs=0.02)  # every point is on the ellipse


def test_ellipse_fill_and_degenerate():
    job = _shape("ellipse", 'cx="50" cy="50" rx="5" ry="3"', fill="#00ff00")
    assert _colors(job) == ["#ff0000", "#00ff00"]
    assert _shape("ellipse", 'cx="5" cy="5" rx="5" ry="0"')["defs"] == []


@pytest.mark.parametrize(
    "image",
    [
        '<image width="0" height="10" href="data:image/png;base64,AAAA"/>',
        '<image width="10" height="10"/>',
        '<image width="10" height="10" href="photo.png"/>',
    ],
)
def test_image_that_cannot_be_placed_is_skipped(image):
    assert _job(image)["defs"] == []


def test_defs_symbol_and_style_tags_draw_nothing():
    job = _job(
        "<style>rect { stroke: #0000ff }</style>"
        '<defs><rect id="r" width="5" height="5" stroke="#00ff00" fill="none"/></defs>'
        '<symbol id="s"><rect width="5" height="5" stroke="#00ff00" fill="none"/></symbol>'
        '<rect width="10" height="10" stroke="#ff0000" fill="none"/>'
    )
    assert _colors(job) == ["#ff0000"]


# ---------------------------------------------------------------------------
# tag reader: cut settings in text
# ---------------------------------------------------------------------------


def _first_font():
    return get_font_manager().find_font("sans-serif")


def _tags_job(monkeypatch, text):
    monkeypatch.setattr(svg_reader, "convert_text_to_paths", lambda s: s)
    return _job(
        '<rect width="10" height="10" stroke="#ff0000" fill="none"/>'
        '<rect x="20" width="10" height="10" stroke="#00ff00" fill="none"/>' + text
    )


def test_cut_settings_text_becomes_passes(monkeypatch):
    job = _tags_job(
        monkeypatch,
        '<text x="0" y="50">=pass2:1000:50:#ff0000:#00ff00=<tspan>=pass1:550mm/min:90%:#00ff00=</tspan></text>',
    )
    assert job["passes"] == [
        {"items": [1], "feedrate": 550, "intensity": 90},
        {"items": [0, 1], "feedrate": 1000, "intensity": 50},
    ]


def test_cut_settings_without_colors_or_feedrate(monkeypatch):
    job = _tags_job(monkeypatch, '<text x="0" y="50">=pass1::80%=</text>')
    assert job["passes"] == [{"items": [], "feedrate": "", "intensity": 80}]


def test_cut_settings_survive_text_conversion():
    if not _first_font():
        pytest.skip("no system font")
    job = _job(
        '<rect width="10" height="10" stroke="#ff0000" fill="none"/>'
        '<text x="0" y="50">=pass1:550mm/min:90%:#ff0000=</text>'
    )
    assert job["passes"] == [{"items": [0], "feedrate": 550, "intensity": 90}]


def test_normal_text_is_still_converted_with_a_font():
    if not _first_font():
        pytest.skip("no system font")
    job = _job('<text x="0" y="50">Hi</text><text x="0" y="80">=pass1::80%=</text>')
    assert len(job["defs"]) == 1  # only "Hi" became paths
    assert job["passes"] == [{"items": [], "feedrate": "", "intensity": 80}]


# ---------------------------------------------------------------------------
# path reader
# ---------------------------------------------------------------------------


def test_path_line_commands_absolute_and_relative():
    assert _stroke("M 0 0 L 10 0 10 10 H 0 V 20") == pytest.approx(
        [0, 0, 10, 0, 10, 10, 0, 10, 0, 20]
    )
    assert _stroke("M 1 1 l 10 0 0 10 h -5 v 5") == pytest.approx(
        [1, 1, 11, 1, 11, 11, 6, 11, 6, 16]
    )


def test_path_numbers_without_separators():
    assert _stroke("M0,0L10-5-3.5.5") == pytest.approx([0, 0, 10, -5, -3.5, 0.5])


def test_path_moveto_starts_new_subpaths():
    data = _subpaths("M 0 0 L 10 0 M 20 20 l 5 5")
    assert data == [[[0, 0], [10, 0]], [[20, 20], [25, 25]]]


def test_path_relative_moveto():
    # a leading m is absolute, later ones relative, extra pairs are linetos
    data = _subpaths("m 5 5 l 5 0 m 10 10 2 2")
    assert data == [[[5, 5], [10, 5]], [[20, 15], [22, 17]]]


def test_path_closepath():
    data = _subpaths("M 0 0 h 10 v 10 z m 3 3 l 1 1")
    # z closes onto the start, a following relative m is relative to that start
    assert data == [[[0, 0], [10, 0], [10, 10], [0, 0]], [[3, 3], [4, 4]]]


def test_path_lineto_after_closepath_starts_at_subpath_start():
    data = _subpaths("M 0 0 L 5 5 Z L 10 0")
    assert data[-1] == [[0, 0], [10, 0]]


@pytest.mark.parametrize(
    "explicit,shorthand",
    [
        ("M 1 2 C 1 12 11 12 11 2", "M 1 2 c 0 10 10 10 10 0"),
        ("M 1 2 Q 6 12 11 2", "M 1 2 q 5 10 10 0"),
        # smooth curves reflect the previous control point
        ("M 0 0 C 0 10 10 10 10 0 C 10 -10 20 -10 20 0", "M 0 0 C 0 10 10 10 10 0 S 20 -10 20 0"),
        ("M 0 0 C 0 10 10 10 10 0 C 10 -10 20 -10 20 0", "M 0 0 c 0 10 10 10 10 0 s 10 -10 10 0"),
        # with no previous curve the control point is the current point
        ("M 0 0 C 0 0 10 10 20 0", "M 0 0 S 10 10 20 0"),
        ("M 0 0 C 0 0 10 10 20 0", "M 0 0 s 10 10 20 0"),
        ("M 2 3 T 12 3", "M 2 3 t 10 0"),
        ("M 0 0 T 10 10 T 20 0", "M 0 0 t 10 10 t 10 -10"),
    ],
)
def test_path_curve_shorthands_match_explicit_curves(explicit, shorthand):
    assert _stroke(shorthand) == pytest.approx(_stroke(explicit))


def test_path_cubic_shape():
    flat = _stroke("M 0 0 C 0 10 10 10 10 0")
    assert flat[:2] == pytest.approx([0, 0])
    assert flat[-2:] == pytest.approx([10, 0])
    assert _bbox(flat)[3] == pytest.approx(7.5, abs=0.05)  # peak of this bezier


def test_path_quadratic_shape():
    flat = _stroke("M 0 0 Q 10 20 20 0")
    assert flat[-2:] == pytest.approx([20, 0])
    assert _bbox(flat)[3] == pytest.approx(10.0, abs=0.05)


def test_path_smooth_quadratic_without_previous_curve_is_straight():
    assert _stroke("M 0 0 T 40 0") == pytest.approx([0, 0, 40, 0])


def test_path_smooth_quadratic_after_quadratic():
    explicit = _stroke("M 0 0 Q 10 20 20 0 Q 30 -20 40 0")
    assert _stroke("M 0 0 Q 10 20 20 0 T 40 0") == pytest.approx(explicit)


def test_path_degenerate_cubic_is_a_straight_line():
    flat = _stroke("M 0 0 C 10 10 10 10 10 10")
    assert _bbox(flat) == pytest.approx([0, 0, 10, 10])
    assert set(_pairs(flat)) <= {(0.0, 0.0), (10.0, 10.0)}


def test_path_arc_semicircle():
    pts = _pairs(_stroke("M 0 0 A 5 5 0 0 1 10 0"))
    assert pts[0] == pytest.approx((0, 0))
    assert pts[-1] == pytest.approx((10, 0))
    assert [math.hypot(x - 5, y) for x, y in pts] == pytest.approx([5.0] * len(pts), abs=0.05)
    assert min(y for _, y in pts) == pytest.approx(-5, abs=0.05)


def test_path_arc_sweep_flag_mirrors_the_arc():
    up = _stroke("M 0 0 A 5 5 0 0 1 10 0")
    down = _stroke("M 0 0 A 5 5 0 0 0 10 0")
    assert min(up[1::2]) == pytest.approx(-5, abs=0.05)
    assert max(down[1::2]) == pytest.approx(5, abs=0.05)


def test_path_arc_large_flag_takes_the_long_way():
    small = _bbox(_stroke("M 0 0 A 10 10 0 0 1 10 0"))
    large = _bbox(_stroke("M 0 0 A 10 10 0 1 1 10 0"))
    assert large[1] < small[1]  # the large arc reaches farther up
    assert large[2] - large[0] > 10  # and bulges out past the chord


def test_path_relative_arc_matches_absolute():
    assert _stroke("M 2 3 a 5 5 0 0 1 10 0") == pytest.approx(_stroke("M 2 3 A 5 5 0 0 1 12 3"))


def test_path_rotated_elliptical_arc_ends_on_target():
    flat = _stroke("M 0 0 A 10 4 30 0 1 12 6")
    assert flat[:2] == pytest.approx([0, 0])
    assert flat[-2:] == pytest.approx([12, 6])


def test_path_arc_with_small_radii_is_scaled_up():
    pts = _pairs(_stroke("M 0 0 A 1 1 0 0 1 10 0"))
    assert [math.hypot(x - 5, y) for x, y in pts] == pytest.approx([5.0] * len(pts), abs=0.05)


def test_path_arc_with_zero_radius_is_a_line():
    assert _stroke("M 0 0 A 0 5 0 0 1 10 0") == pytest.approx([0, 0, 10, 0])


def test_path_ignores_unknown_commands():
    assert _stroke("M 0 0 L 10 0 foo 5") == pytest.approx([0, 0, 10, 0])


def test_path_curve_detail_follows_the_transform_scale():
    d = "M 0 0 C 0 10 10 10 10 0"
    small = _stroke(d, 'transform="scale(0.1)"')
    large = _stroke(d, 'transform="scale(10)"')
    assert len(large) > len(small)


# ---------------------------------------------------------------------------
# text converter: pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size,expected",
    [
        ("12", 12.0),
        ("12px", 12.0),
        (" 12PX ", 12.0),
        ("12pt", 12 * 1.333),
        ("2em", 32.0),
        ("50%", 8.0),
        ("large", 16.0),  # unknown keywords fall back to the default
    ],
)
def test_parse_font_size(size, expected):
    assert parse_font_size(size) == pytest.approx(expected)


def test_get_style_attr_prefers_style_over_attribute():
    el = ET.fromstring('<text font-size="10" fill="red" style="font-size: 20px; fill :blue"/>')
    assert get_style_attr(el, "font-size") == "20px"
    assert get_style_attr(el, "fill") == "blue"
    assert get_style_attr(el, "text-anchor", "start") == "start"
    assert get_style_attr(el, "text-anchor") is None
    assert get_style_attr(ET.fromstring('<text font-size="10"/>'), "font-size", "16") == "10"


def test_get_text_content_walks_children_and_tails():
    assert (
        get_text_content(ET.fromstring("<text>a<tspan>b<tspan>c</tspan>d</tspan>e</text>"))
        == "abcde"
    )


# scale (2, -3), offset (10, 100)
_TX = (2, -3, 10, 100)


@pytest.mark.parametrize(
    "cmd,coords,expected",
    [
        ("M", [1, 1], "M12.00 97.00"),
        ("m", [1, 1], "m2.00 -3.00"),  # relative: scaled, not moved
        ("L", [1, 1, 2, 2], "L12.00 97.00 14.00 94.00"),
        ("l", [1, 1], "l2.00 -3.00"),
        ("H", [1, 2], "H12.00 14.00"),
        ("h", [1], "h2.00"),
        ("V", [1], "V97.00"),
        ("v", [1], "v-3.00"),
        ("C", [1, 1, 2, 2, 3, 3], "C12.00 97.00 14.00 94.00 16.00 91.00"),
        ("c", [1, 1, 2, 2, 3, 3], "c2.00 -3.00 4.00 -6.00 6.00 -9.00"),
        ("S", [1, 1, 2, 2], "S12.00 97.00 14.00 94.00"),
        ("s", [1, 1, 2, 2], "s2.00 -3.00 4.00 -6.00"),
        ("Q", [1, 1, 2, 2], "Q12.00 97.00 14.00 94.00"),
        ("q", [1, 1, 2, 2], "q2.00 -3.00 4.00 -6.00"),
        ("T", [1, 1], "T12.00 97.00"),
        ("t", [1, 1], "t2.00 -3.00"),
        ("A", [5, 5, 30, 1, 0, 1, 1], "A10.00 15.00 30.00 1 0 12.00 97.00"),
        ("a", [5, 5, 30, 1, 0, 1, 1], "a10.00 15.00 30.00 1 0 2.00 -3.00"),
        ("Z", [], "Z"),
    ],
)
def test_transform_command(cmd, coords, expected):
    assert transform_command(cmd, coords, *_TX).strip() == expected


def test_transform_path_flips_and_translates():
    assert transform_path("M0 0L1 1Z", 2, -2, 10, 10).split() == [
        "M10.00", "10.00", "L12.00", "8.00", "Z",
    ]  # fmt: skip
    assert transform_path("", 1, 1, 0, 0) == ""
    assert transform_path("M0 0 L1 1 Z M2 2 L3 3 Z", 1, 1, 0, 0).count("Z") == 2


# ---------------------------------------------------------------------------
# text converter: font manager on a fake font directory
# ---------------------------------------------------------------------------


def _fake_fonts(monkeypatch, tmp_path, names):
    for n in names:
        (tmp_path / n).write_bytes(b"not a font")
    fm = FontManager()
    monkeypatch.setattr(fm, "_get_font_directories", lambda: [str(tmp_path)])
    return fm


def test_font_manager_prefers_the_regular_variant(monkeypatch, tmp_path):
    fm = _fake_fonts(
        monkeypatch, tmp_path, ["FooSans-Bold.ttf", "FooSans-Regular.ttf", "readme.txt"]
    )
    assert fm.find_font("Foo Sans").endswith("FooSans-Regular.ttf")
    assert not any("readme" in name for name in fm._scan_fonts())


def test_font_manager_generic_family_falls_back_quietly(monkeypatch, tmp_path):
    fm = _fake_fonts(monkeypatch, tmp_path, ["Zzz.ttf", "DejaVuSans.ttf", "DejaVuSerif.otf"])
    with issues.collecting() as c:
        assert fm.find_font("sans-serif").endswith("DejaVuSans.ttf")
        assert fm.find_font("serif").endswith("DejaVuSerif.otf")
    assert c.issues() == []


def test_font_manager_unknown_family_warns_and_falls_back(monkeypatch, tmp_path):
    fm = _fake_fonts(monkeypatch, tmp_path, ["Only.ttf"])
    with issues.collecting() as c:
        assert fm.find_font("Comic Nonexistent").endswith("Only.ttf")
    (row,) = c.issues()
    assert row["problem"] == 'Font "Comic Nonexistent" missing'
    assert (row["result"], row["kind"]) == ("ONLY USED", "replaced")


def test_font_manager_without_fonts(monkeypatch, tmp_path):
    assert _fake_fonts(monkeypatch, tmp_path, []).find_font("Arial") is None


def test_font_manager_unloadable_font(monkeypatch, tmp_path, caplog):
    fm = _fake_fonts(monkeypatch, tmp_path, ["Broken.ttf"])
    with caplog.at_level(logging.WARNING, logger="svg_text_converter"):
        assert fm.load_font(str(tmp_path / "Broken.ttf")) is None
    assert "Failed to load font" in caplog.text


def test_font_directories_per_platform(monkeypatch, tmp_path):
    (tmp_path / "Fonts").mkdir()
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.setattr(svg_text_converter.platform, "system", lambda: "Windows")
    assert FontManager()._get_font_directories() == [str(tmp_path / "Fonts")]
    monkeypatch.setattr(svg_text_converter.platform, "system", lambda: "Darwin")
    assert all(d.endswith("Fonts") for d in FontManager()._get_font_directories())
    monkeypatch.setattr(svg_text_converter.platform, "system", lambda: "Plan9")
    assert FontManager()._get_font_directories() == []


# ---------------------------------------------------------------------------
# text converter: real glyphs (needs a system font)
# ---------------------------------------------------------------------------


@pytest.fixture
def font():
    path = _first_font()
    f = get_font_manager().load_font(path) if path else None
    if f is None:
        pytest.skip("no usable system font")
    return f


def test_text_width_scales_with_size_and_text(font):
    w16 = get_text_width("Hi", font, 16)
    assert w16 > 0
    assert get_text_width("Hi", font, 32) == pytest.approx(2 * w16)
    assert get_text_width("", font, 16) == 0
    assert get_text_width("Hi Hi", font, 16) > 2 * w16  # the space has width too
    assert get_text_width("\U0010ffff", font, 16) == 0  # no glyph, no advance


@pytest.mark.parametrize("anchor,shift", [("start", 0.0), ("middle", 0.5), ("end", 1.0)])
def test_text_anchor_shifts_by_the_text_width(font, anchor, shift):
    width = get_text_width("Hello", font, 20)
    anchored = text_to_path("Hello", font, 20, x=100, y=50, text_anchor=anchor)
    assert anchored == text_to_path("Hello", font, 20, x=100 - shift * width, y=50)


def test_text_to_path_space_only_has_no_outline(font):
    assert text_to_path("   ", font, 20) == ""


def test_simplify_path_without_pathops(monkeypatch):
    monkeypatch.setattr(svg_text_converter, "PATHOPS_AVAILABLE", False)
    assert svg_text_converter.simplify_path("M0 0 L1 1") == "M0 0 L1 1"


def test_simplify_path_with_pathops():
    pytest.importorskip("pathops")
    out = svg_text_converter.simplify_path("M0 0 L10 0 L10 10 L0 10 Z M5 5 L15 5 L15 15 L5 15 Z")
    assert out.startswith("M")


# ---------------------------------------------------------------------------
# text converter: whole documents
# ---------------------------------------------------------------------------


def _text_doc(body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
        f'viewBox="0 0 100 100">{body}</svg>'
    )


def _paths(svg):
    return [e for e in ET.fromstring(svg).iter() if e.tag.endswith("}path")]


def test_convert_text_replaces_text_with_a_path(font):
    out = convert_text_to_paths(
        _text_doc(
            '<text id="t" x="10" y="50" font-size="20" fill="#ff0000" transform="translate(1,2)">Hi</text>'
        )
    )
    assert "<text" not in out
    (path,) = _paths(out)
    assert path.get("id") == "t_path"
    assert path.get("stroke") == path.get("fill") == "#ff0000"
    assert path.get("transform") == "translate(1,2)"
    assert path.get("d") == text_to_path("Hi", font, 20, 10, 50)


def test_convert_text_bytes_in_bytes_out(font):
    out = convert_text_to_paths(_text_doc('<text x="0" y="10">Hi</text>').encode())
    assert isinstance(out, bytes)
    assert b"<text" not in out


def test_convert_text_tspans_group_by_color(font):
    out = convert_text_to_paths(
        _text_doc(
            '<text id="t" x="10" y="50" fill="red" font-size="20">A'
            '<tspan fill="#0000ff" font-size="10pt">B</tspan>'
            '<tspan style="fill:red">C</tspan> tail</text>'
        )
    )
    paths = _paths(out)
    # the red text, the red tspan and the tail share one path; blue gets its own
    assert [p.get("fill") for p in paths] == ["#ff0000", "#0000ff"]
    assert [p.get("id") for p in paths] == ["t_path_0", "t_path_1"]


def test_convert_text_stroke_only_is_cut_in_its_stroke_colour(font):
    body = (
        '<text x="0" y="10" style="fill:none;stroke:#ff0000">A</text>'
        '<text x="0" y="30" stroke="#00ff00">B</text>'
        '<text x="0" y="50" fill="#0000ff" stroke="#00ff00">C'
        '<tspan fill="none" stroke="#ffff00">D</tspan></text>'
    )
    with issues.collecting() as c:
        out = convert_text_to_paths(_text_doc(body))
    fills = [p.get("fill") for p in _paths(out)]
    assert fills == ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]
    assert c.issues() == []


def test_convert_text_tspan_uses_its_own_position(font):
    out = convert_text_to_paths(
        _text_doc('<text x="10" y="50" font-size="20"><tspan x="60" y="70">B</tspan></text>')
    )
    (path,) = _paths(out)
    assert path.get("d") == text_to_path("B", font, 20, 60, 70)


def test_convert_text_anchor_and_font_size_units(font):
    out = convert_text_to_paths(
        _text_doc('<text x="50" y="50" style="font-size:2em; text-anchor:middle">Hi</text>')
    )
    (path,) = _paths(out)
    assert path.get("d") == text_to_path("Hi", font, 32, 50, 50, "middle")


def test_convert_text_missing_font_warns_and_still_converts(font):
    with issues.collecting() as c:
        out = convert_text_to_paths(
            _text_doc('<text x="0" y="10" font-family="NoSuchFontQwerty">Hi</text>')
        )
    assert "<text" not in out
    assert [r["problem"] for r in c.issues()] == ['Font "NoSuchFontQwerty" missing']
    with issues.collecting() as c:
        convert_text_to_paths(_text_doc("<g/>"))  # issues are per collector
    assert c.issues() == []


def test_convert_text_without_any_font_leaves_the_document(monkeypatch):
    monkeypatch.setattr(get_font_manager(), "find_font", lambda family: None)
    svg = _text_doc('<text x="0" y="10">Hi</text>')
    assert convert_text_to_paths(svg) == svg


@pytest.mark.parametrize(
    "svg",
    [
        _text_doc('<text x="0" y="10">   </text>'),  # nothing to draw
        _text_doc('<text x="abc" y="10">Hi</text>'),  # bad number: element skipped
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Hi</svg>',  # not well formed
    ],
)
def test_convert_text_leaves_what_it_cannot_convert(svg, font):
    assert convert_text_to_paths(svg) == svg


def test_convert_text_end_to_end_makes_cuttable_geometry(font):
    job = _job('<text x="10" y="50" font-size="20" fill="#ff0000">HI</text>')
    assert _colors(job) == ["#ff0000"]
    b = _bbox(_flat(job))
    assert b[0] >= 10 and b[2] < 40 and b[3] <= 50.5 and b[1] < 50
