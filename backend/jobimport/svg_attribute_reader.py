__author__ = "Stefan Hechenberger <stefan@nortd.com>"

import logging
import math
import re

from PIL import ImageColor

from . import issues
from .utilities import NUMBER, matrixMult, parseFloats

log = logging.getLogger("svg_reader")


def element_label(tag, node_id=None):
    """How an issue names an element: `rect "frame"`, or just `rect` without an id."""
    return f'{tag} "{node_id}"' if node_id else tag


def report_alpha(val, where=None):
    """Report a colour whose alpha is below 1, which cutting ignores."""
    val = val.strip().lower()
    alpha = None
    if val.startswith("rgba"):
        chans = re.findall(rf"({NUMBER})(%?)", val[5:-1])
        if len(chans) == 4:
            num, pct = chans[3]
            alpha = float(num) / (100 if pct else 1)
    elif val.startswith("#") and len(val) in (5, 9):
        digits = 1 if len(val) == 5 else 2
        alpha = int(val[-digits:], 16) / (16**digits - 1)
    if alpha is not None and alpha < 1:
        issues.report(
            "Colour alpha ignored", "CUT AT FULL OPACITY", "changed", "Use opacity", where
        )


def _hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*map(int, rgb[:3]))


def _rgb_channels(args):
    """Channels of an rgb() argument list, percentages scaled and all clamped to 0..255."""
    chans = []
    for num, pct in re.findall(rf"({NUMBER})(%?)", args):
        num = float(num)
        chans.append(min(255, max(0, round(num * 2.55) if pct else num)))
    return chans


def parse_color(val):
    """Parse a color definition.

    Returns a color in hex format, 'inherit', or 'none'; None for a color
    that is not supported. 'none' means that the geometry is not to be rendered.
    Silent: colorAttrib, which has the element, reports what cannot be used.
    See: http://www.w3.org/TR/SVG11/painting.html#SpecifyingPaint
    """
    # http://www.w3.org/TR/SVG11/color.html
    # http://www.w3.org/TR/2008/REC-CSS2-20080411/syndata.html#color-units
    if val[0] == " ":
        val = val.strip()
    val = val.lower()  # CSS color syntax is case insensitive

    if val[0] == "#":
        try:
            return _hex(ImageColor.getrgb(val))
        except ValueError:
            return "inherit"
    elif val.startswith("rgba"):
        floats = _rgb_channels(val[5:-1])
        if len(floats) == 4:
            return _hex(floats)
    elif val.startswith("rgb"):
        floats = _rgb_channels(val[4:-1])
        if len(floats) == 3:
            return _hex(floats)
    elif val == "none":
        # 'none' means the geometry is not to be filled or stroked
        # http://www.w3.org/TR/SVG11/painting.html#SpecifyingPaint
        return "none"
    elif val.startswith("hsl") or val.startswith("url"):
        # same document url(#id) references are taken apart in colorAttrib
        return None
    elif val in ImageColor.colormap:  # named colors
        return _hex(ImageColor.getrgb(val))
    elif val in ["currentcolor", "inherit"]:
        return "inherit"
    else:
        return "inherit"


class SVGAttributeReader:
    def __init__(self, svgreader):
        self.svgreader = svgreader

        self.DEG_TO_RAD = math.pi / 180
        self.RAD_TO_DEG = 180 / math.pi

        self._handlers = {
            "id": self.stringAttrib,
            "transform": self.transformAttrib,
            "style": self.styleAttrib,  # styles
            "opacity": self.opacityAttrib,
            "display": self.stringAttrib,
            "visibility": self.stringAttrib,
            "fill": self.colorAttrib,
            "stroke": self.colorAttrib,
            "color": self.colorAttrib,
            "fill-opacity": self.opacityAttrib,
            "stroke-opacity": self.opacityAttrib,
            "width": self.dimensionAttrib,  # geometry
            "height": self.dimensionAttrib,
            "d": self.dAttrib,
            "points": self.pointsAttrib,
            "x": self.dimensionAttrib,
            "y": self.dimensionAttrib,
            "rx": self.dimensionAttrib,
            "ry": self.dimensionAttrib,
            "x1": self.dimensionAttrib,
            "y1": self.dimensionAttrib,
            "x2": self.dimensionAttrib,
            "y2": self.dimensionAttrib,
            "r": self.dimensionAttrib,
            "cx": self.dimensionAttrib,
            "cy": self.dimensionAttrib,
            "href": self.stringAttrib,
            "preserveAspectRatio": self.stringAttrib,
            "viewBox": self.stringAttrib,
            "clip-path": self.clipPathAttrib,
        }

        self.re_findall_transforms = re.compile(r"(([a-z]+)\s*\(([^)]*)\))", re.IGNORECASE).findall
        self.re_findall_pathelems = re.compile(rf"([A-Za-z]|{NUMBER})").findall
        self.re_findall_unitparts = re.compile(rf"({NUMBER})(cm|mm|pt|pc|in|%|em|ex)?").findall
        self.re_match_painturl = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)").match

    def read_attrib(self, node, attr, value):
        """Read any attribute.

        This function delegates according to the _hahttp://www.w3.org/TR/SVG11/shapes.html#CircleElementndlers map.
        """
        attrName = attr[attr.rfind("}") + 1 :]  # strip prefixes
        if attrName in self._handlers and value.strip() != "":
            # log.debug("reading attrib: " + attrName + ":" + value)
            self._handlers[attrName](node, attrName, value)

    def stringAttrib(self, node, attr, value):
        """Read a string attribute."""
        if value != "inherit":
            node[attr] = value

    def clipPathAttrib(self, node, attr, value):
        """Read a clip-path; store the referenced clipPath id from url(#id)."""
        v = value.strip()
        if v == "none":
            node["clip-path"] = None
            return
        m = re.search(r"url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)", v)
        if m:
            node["clip-path"] = m.group(1)

    def opacityAttrib(self, node, attr, value):
        """Read a opacity attribute."""
        try:
            node[attr] = min(1.0, max(0.0, float(value)))
        except ValueError:
            issues.report(
                f'Invalid opacity "{value.strip()}"',
                "FULL OPACITY",
                "changed",
                "Set a valid opacity",
                node.get("where"),
            )
            node[attr] = 1.0

    def dimensionAttrib(self, node, attr, value):
        """Read a dimension attribute."""
        node[attr] = self._parseUnit(value, node.get("where"))

    def colorAttrib(self, node, attr, value):
        # http://www.w3.org/TR/SVG11/color.html
        # http://www.w3.org/TR/SVG11/painting.html#SpecifyingPaint
        """Read a color attribute, or a url(#id) paint server reference.

        A paint server is kept as '<attr>-ref' for the reader to resolve, and
        clears the plain color so the shape is not also painted flat.
        """
        ref = self.re_match_painturl(value.strip())
        if ref:
            node[attr + "-ref"] = ref.group(1)
            node[attr] = None
            return
        node.pop(attr + "-ref", None)
        col = parse_color(value)
        where = node.get("where")
        if col is None:
            # hsl, a url() to elsewhere or a malformed rgb(): no paint is set
            issues.report(
                f'Unsupported colour "{value.strip()}"',
                "NOT CUT",
                "dropped",
                "Use a hex colour",
                where,
            )
        elif col == "inherit" and value.strip().lower() not in ("inherit", "currentcolor"):
            issues.report(
                f'Invalid colour "{value.strip()}"',
                "DEFAULT COLOUR",
                "changed",
                "Set a valid colour",
                where,
            )
        else:
            report_alpha(value, where)
        if col != "inherit":
            node[attr] = col

    def _skip_transform(self, node, kind):
        issues.report(
            f'Invalid "{kind}" transform',
            "TRANSFORM IGNORED",
            "changed",
            "Fix the transform",
            node.get("where"),
        )

    def transformAttrib(self, node, attr, value):
        # http://www.w3.org/TR/SVG11/coords.html#EstablishingANewUserSpace
        xforms = []
        matches = self.re_findall_transforms(value)
        # this parses  something like "translate(50,50), rotate(56)"" to
        # [('translate(50,50)', 'translate', '50,50'), ('rotate(56)', 'rotate', '56')]
        for match in matches:
            xformKind = match[1]
            params = parseFloats(match[2])

            # translate
            if xformKind == "translate":
                if len(params) == 1:
                    # a translate with no y does not move in y
                    xforms.append([1, 0, 0, 1, params[0], 0])
                elif len(params) == 2:
                    xforms.append([1, 0, 0, 1, params[0], params[1]])
                else:
                    self._skip_transform(node, "translate")
            # rotate
            elif xformKind == "rotate":
                if len(params) == 3:
                    angle = params[0] * self.DEG_TO_RAD
                    xforms.append([1, 0, 0, 1, params[1], params[2]])
                    xforms.append(
                        [
                            math.cos(angle),
                            math.sin(angle),
                            -math.sin(angle),
                            math.cos(angle),
                            0,
                            0,
                        ]
                    )
                    xforms.append([1, 0, 0, 1, -params[1], -params[2]])
                elif len(params) == 1:
                    angle = params[0] * self.DEG_TO_RAD
                    xforms.append(
                        [
                            math.cos(angle),
                            math.sin(angle),
                            -math.sin(angle),
                            math.cos(angle),
                            0,
                            0,
                        ]
                    )
                else:
                    self._skip_transform(node, "rotate")
            # scale
            elif xformKind == "scale":
                if len(params) == 1:
                    xforms.append([params[0], 0, 0, params[0], 0, 0])
                elif len(params) == 2:
                    xforms.append([params[0], 0, 0, params[1], 0, 0])
                else:
                    self._skip_transform(node, "scale")
            # matrix
            elif xformKind == "matrix":
                if len(params) == 6:
                    xforms.append(params)
                else:
                    self._skip_transform(node, "matrix")
            # skewX
            elif xformKind == "skewX":
                if len(params) == 1:
                    angle = params[0] * self.DEG_TO_RAD
                    xforms.append([1, 0, math.tan(angle), 1, 0, 0])
                else:
                    self._skip_transform(node, "skewX")
            # skewY
            elif xformKind == "skewY":
                if len(params) == 1:
                    angle = params[0] * self.DEG_TO_RAD
                    xforms.append([1, math.tan(angle), 0, 1, 0, 0])
                else:
                    self._skip_transform(node, "skewY")

        # calculate combined transformation matrix
        xform_combined = [1, 0, 0, 1, 0, 0]
        for xform in xforms:
            xform_combined = matrixMult(xform_combined, xform)

        # assign
        node["xform"] = xform_combined

    def styleAttrib(self, node, attr, value):
        # style attribute
        # http://www.w3.org/TR/SVG11/styling.html#StyleAttribute
        # http://www.w3.org/TR/SVG11/styling.html#SVGStylingProperties
        # example: <rect x="200" y="100" width="600" height="300"
        #          style="fill: red; stroke: blue; stroke-width: 3"/>
        # relay to parse style attributes the same as Presentation Attributes
        segs = value.split(";")
        for seg in segs:
            kv = seg.split(":")
            if len(kv) == 2:
                k = kv[0].strip()
                v = kv[1].strip()
                if k != "style":  # prevent infinite loop
                    self.read_attrib(node, k, v)
        # Also see: Presentations Attributes
        # http://www.w3.org/TR/SVG11/styling.html#UsingPresentationAttributes
        # example: <rect x="200" y="100" width="600" height="300"
        #          fill="red" stroke="blue" stroke-width="3"/>

    def dAttrib(self, node, attr, value):
        """Read the 'd' attribute, complex path data."""
        # http://www.w3.org/TR/SVG11/paths.html
        d = self.re_findall_pathelems(value)
        # convert num strings to actual nums
        for i in range(len(d)):
            try:
                d[i] = float(d[i])
            except ValueError:
                pass  # ok too, probably a command letter
        node[attr] = d

    def pointsAttrib(self, node, attr, value):
        """Read the 'points' attribute."""
        floats = parseFloats(value)
        if len(floats) % 2 == 0:
            node[attr] = floats
        else:
            raise issues.ImportFailed("Odd number of points", "Fix the points", node.get("where"))

    def _parseUnit(self, val, where=None):
        if val is not None:
            vals = self.re_findall_unitparts(val)
            # [('123', 'em'), ('-10', 'cm')]
            if vals:
                num = float(vals[0][0])
                unit = vals[0][1]
                if unit == "":
                    return num

                # physical units -> px (user units)
                px2mm = self.svgreader.px2mm or (25.4 / 90.0)
                dpi = 25.4 / px2mm
                if unit == "cm":
                    num *= dpi / 2.54
                elif unit == "mm":
                    num *= dpi / 25.4
                elif unit == "pt":
                    num *= dpi / 72.0
                elif unit == "pc":
                    num *= 12 * dpi / 72
                elif unit == "in":
                    num *= dpi
                elif unit == "%" or unit == "em" or unit == "ex":
                    issues.report(
                        f'Unsupported unit "{unit}"',
                        "TREATED AS PX",
                        "changed",
                        "Use px or mm",
                        where,
                    )

                return num
            issues.report(
                f'Invalid dimension "{val.strip()}"',
                "TREATED AS 0",
                "changed",
                "Set a valid size",
                where,
            )
        return None
