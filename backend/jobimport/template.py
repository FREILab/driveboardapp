"""Parametric SVG templates: =param:...= declarations and {{ expr }} placeholders.

The template text comes from any member, so expressions are checked against a
whitelist of AST nodes and never reach eval/exec.
"""

import ast
import math
import operator
import re

from . import issues

LIMIT = 1e6  # no intermediate or final result may exceed this

_PARAM = re.compile(r"=param:([^=<>]*)=")
_NAME = re.compile(r"[A-Za-z_]\w*")
# no braces or tags inside: a run of unclosed "{{" must not rescan to the end of the text
_PLACEHOLDER = re.compile(r"\{\{([^{}<>]*)\}\}")
_PASS_TAG = re.compile(r"=pass\d+:[^=]*=")
_PASS_OK = re.compile(r"=pass\d+:\d*(mm/min)?:\d*%?(:#[0-9a-fA-F]{6}){0,6}=")

_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
}
_FUNCS = {"round": round, "min": min, "max": max, "abs": abs}


class TemplateError(issues.ImportFailed):
    """The SVG template or its values cannot be used."""


def has_params(svg):
    return "=param:" in svg


def _number(text):
    """Parse a finite number, int when integral; None if it is not one."""
    try:
        x = float(text)
    except ValueError:
        return None
    if not math.isfinite(x):
        return None
    return int(x) if x.is_integer() else x


def parse_params(svg):
    params = []
    tags = list(_PARAM.finditer(svg))
    if len(tags) != svg.count("=param:"):
        raise TemplateError("Parameter tag is not closed", "End it with = on the same text, as =param:name:default:min:max:label=")
    for m in tags:
        tag = m.group(0)
        parts = m.group(1).split(":")
        if len(parts) != 5:
            raise TemplateError("Parameter needs 5 fields", "Use =param:name:default:min:max:label=", tag)
        name, label = parts[0].strip(), parts[4]
        if not _NAME.fullmatch(name):
            raise TemplateError("Parameter name is invalid", "Use letters, digits and _", tag)
        default, low, high = (_number(p) for p in parts[1:4])
        if None in (default, low, high):
            raise TemplateError("Parameter value is not a number", "Use numbers for default, min and max", tag)
        if not low <= default <= high:
            raise TemplateError("Parameter default is out of range", "Keep min <= default <= max", tag)
        if any(p["name"] == name for p in params):
            raise TemplateError("Parameter is declared twice", f"Declare {name} once", tag)
        params.append({"name": name, "default": default, "min": low, "max": high, "label": label})
    return params


def resolve_values(params, values):
    by_name = {p["name"]: p for p in params}
    values = values or {}
    if not isinstance(values, dict):
        raise TemplateError("Values must be an object", "Send {name: number}")
    for name, value in values.items():
        if name not in by_name:
            raise TemplateError("Unknown parameter", f"Remove {name}", name)
    out = {}
    for p in params:
        value = values.get(p["name"], p["default"])
        try:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        except OverflowError:  # a JSON integer beyond float range
            ok = False
        if not ok:
            raise TemplateError(f"{p['label']} is not a number", f"Use {p['min']}–{p['max']}", p["name"])
        if not p["min"] <= value <= p["max"]:
            raise TemplateError(f"{p['label']} out of range", f"Use {p['min']}–{p['max']}", p["name"])
        out[p["name"]] = value
    return out


def safe_eval(expr, names):
    def bad(problem, fix):
        return TemplateError(problem, fix, expr)

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.Name) and node.id in names:
            result = names[node.id]
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            result = _UNARY[type(node.op)](walk(node.operand))
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            result = _BINARY[type(node.op)](walk(node.left), walk(node.right))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in _FUNCS and not node.keywords):
            args = [walk(a) for a in node.args]
            # round(x, -1000000) builds a huge bigint, so bound the digits
            if node.func.id == "round" and len(args) == 2 and not (type(args[1]) is int and -6 <= args[1] <= 6):
                raise bad("round() digits not allowed", "Use a whole number from -6 to 6")
            result = _FUNCS[node.func.id](*args)
        else:
            raise bad("Expression not allowed", "Use numbers, parameters, + - * / // and round/min/max/abs")
        if abs(result) > LIMIT:
            raise bad("Number too large", "Keep results below 1000000")
        return result

    try:
        return walk(ast.parse(expr.strip(), mode="eval"))
    except TemplateError:
        raise
    except ZeroDivisionError:
        raise bad("Division by zero", "Check the divisor") from None
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError, MemoryError):
        raise bad("Expression cannot be read", "Check the expression") from None


def format_number(x):
    x = round(x, 3)
    return str(int(x)) if x == int(x) else str(x)


def render(svg, values):
    out = _PLACEHOLDER.sub(lambda m: format_number(safe_eval(m.group(1), values)), svg)
    if "{{" in out or "}}" in out:
        raise TemplateError("Placeholder not closed", "Keep {{ … }} in one text span")
    for m in _PASS_TAG.finditer(out):
        if not _PASS_OK.fullmatch(m.group(0)):
            raise TemplateError("Pass tag is not readable", "Use round() in pass tags", m.group(0))
    return out
