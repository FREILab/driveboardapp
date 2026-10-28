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

def test_has_params():
    assert template.has_params(SVG)
    assert not template.has_params("<svg/>")

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
