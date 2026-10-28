__author__ = "Stefan Hechenberger <stefan@nortd.com>"


import re
import sys

from . import issues

debug = True

# unsupported codes are reported under one label per group
LABELS = {("G", n): "G55-G59" for n in (55, 56, 57, 58, 59)}
LABELS.update({("G", 93): "G93,G95", ("G", 95): "G93,G95", ("M", 0): "M0,M1", ("M", 1): "M0,M1"})
# defaults, no-op cancels (radius/length comp, canned cycle, polar, rotation)
# and path modes, program end: nothing to report
QUIET = {("G", n) for n in (15, 17, 40, 49, 54, 61, 64, 69, 80, 94)} | {("M", 2), ("M", 30)}


class GcodeReader:
    """Parse subset of G-Code.

    GCODE OVERVIEW
    --------------
    See: http://linuxcnc.org/docs/html/gcode.html

    G0 - rapid move
    G1 - linear move
    G2,G3 - arc move
    G4 - dwell

    G17 - select XY-plane (default)
    G18 - select XZ-plane
    G19 - select YZ-plane

    G20 - inch mode
    G21 - mm mode (default)

    G28 - park machine
        - without params, move to machine 0,0,0
        - with params, first go there (in current cs), typ retract Z
        - (unless G28.1 programmed a different origin)
        - "G28 G91 Z0, G90" typically before tool change, same as "G53 Z0, G53 X0 Y0"

    G38.2-G38.5 - probe

    G53 - use machine coorinates for same block (G53 Z15), non-modal
    G54 - use CS 1 from now on (default)
    G55-G59 - more custom CSes

    G90 - absolute mode (default)
    G91 - relative mode

    G92 - move CS (throw error)

    G93 - inverse time mode
    G94 - units/mm mode (default)
    G95 - units/rev mode


    M0, M1 - pause
    M2, M30 - end

    M3 - start spindle CW at whatever S has been set to
    M4 - start spindle CCW at whatever S has been set to
    M5 - stop spindle

    M6 - stop, prompt for tool change, non-modal
       - to whatever the most recent Tx was

    M7 - mist coolant on
    M8 - flood coolant on
    M9 - all coolant off

    M62-M65 - switch digital output


    Sx - spindle speed in RPMs

    Fx - feedrate (default: mm/min)

    Tx - set active tool mode, schedule actual switch with M6


    STRATEGY
    --------
    One pass for every tool. UI to run one pass after the other.

    - tool
      - path
        - params: feedrate, spindle, coolant
        - move
        - move
        - params: feedrate, spindle, coolant
        - move
        - move
        - ...
      - retract
    - tool
      - ...

    One path/tool. Within path, a series of moves and param changes.
    Treat rapid moves like feeds with different rate.


    OUTPUT
    ------
    Output is a job with a 'kind':'mill' marker in the 'head'. The actual
    data is in 'defs':{'data':[]}. Every tool becomes one data entry.
    A data path is a series of moves and param changes.

    A def looks like this:
    {data':[], 'tool':'', 'toolinfo':''}

    The format of a data path is a series of possible action items:
    - ('G0',(x,y,z))      (mapped to move with seekrate)
    - ('G1',(x,y,z))      (mapped to move with feedrate)
    - ('F',rate)          (mapped to feedrate)
    - ('S',freq)          (mapped to intensity)
    - ('MIST', onoff)     (mapped to air_on/off)
    - ('FLOOD', onoff)    (mapped to aux_on/off)

    Path example (job['defs'][0]['data']):
    [('G0',(x,y,z)), ('F', 1000), ('S', 18000), ('FLOOD', True), ('G1', (x,y,z))]


    """

    def __init__(self):
        # flags
        self.bTool = False
        self.where = None  # source line being read
        self.dropped_at = None  # last line reported as "Move before tool set"

        # modal state
        self.G_motion = "G0"
        self.G_absolute = True  # G90/G91
        self.unit_scale = 1  # G21 mm / G20 inch (output is mm)
        self.X_pos = None
        self.Y_pos = None
        self.Z_pos = None
        self.F_rate = 0
        self.S_freq = 0
        self.S_on = False
        self.T_num = 0
        self.M_mist = False
        self.M_flood = False

        # tools table
        self.def_ = None
        self.toolinfo = {}
        self.rates = []
        self.freqs = []
        self.mists = False
        self.floods = False

        # regexes
        self.re_parts = re.compile(r"(X|Y|Z|G|M|T|S|F)(-?[0-9]+\.?[0-9]*(?:E-?[0-9]*)?)").findall
        self.re_toolchange = re.compile(r"(M6)").findall
        self.re_T = re.compile(r"(T)([0-9]+)").findall
        self.re_toolinfo = re.compile(r"\((T[0-9]+) *(.+) *\)").findall

        # output job
        self.job = {"head": {"kind": "mill"}, "defs": []}

    def finalize_pass(self):
        if self.def_:
            self.def_["rates"] = self.rates
            self.def_["freqs"] = self.freqs
            self.def_["mists"] = self.mists
            self.def_["floods"] = self.floods

    def next_pass(self):
        self.rates = []
        self.freqs = []
        self.mists = False
        self.floods = False
        self.job["defs"].append({"data": [], "tool": "", "toolinfo": ""})
        self.def_ = self.job["defs"][-1]
        self.path = self.def_["data"]

    def on_toolchange(self, line):
        """Handle a tool change action (M6).
        Account for T action on same line (nothing more).
        """
        T_code = self.re_T(line)
        if len(T_code) == 1:
            self.T_num = T_code[0][1]
            nParts = 2
        else:
            nParts = 1
        # commit tool change
        if len(self.re_parts(line)) != nParts:
            issues.report(
                "Odd tool change line", "IGNORED", "changed", "Only T on M6 line", self.where
            )
        else:
            self.finalize_pass()
            self.next_pass()
            self.bTool = True
            # add tool, toolinfo to def
            tool = "T" + str(self.T_num)
            self.def_["tool"] = tool
            if tool in self.toolinfo:
                self.def_["toolinfo"] = self.toolinfo[tool]

    def on_action(self, action):
        if not self.bTool:
            if self.where != self.dropped_at:  # once per line, not per action
                self.dropped_at = self.where
                issues.report(
                    "Move before tool set", "NOT CUT", "dropped", "Set tool first", self.where
                )
            return
        self.path.append(action)

    def _scale(self, value):
        return value if self.unit_scale == 1 else round(value * self.unit_scale, 4)

    def _target(self, current, value):
        """New axis position from a coordinate word, honoring G20/G21 and G90/G91."""
        value = self._scale(value)
        return value if self.G_absolute else round((current or 0) + value, 4)

    def parse(self, gcodestring):
        """Convert gcode to a job file."""
        for lineno, rawline in enumerate(gcodestring.splitlines(), 1):
            self.where = f"line {lineno}"
            rawline = rawline.strip()
            # words are case-insensitive, inline (comments) are not code
            line = re.sub(r"\(.*?\)", "", rawline.upper()).strip()
            # reject line condition
            if len(line) == 0 or line[0] not in (
                "X",
                "Y",
                "Z",
                "G",
                "M",
                "T",
                "S",
                "F",
            ):
                # parse tool table
                if rawline.startswith("(T"):
                    toolinfo = self.re_toolinfo(rawline)
                    if toolinfo:
                        self.toolinfo[toolinfo[0][0]] = toolinfo[0][1]
                # reject: comments and program delimiters are fine, anything else is lost
                else:
                    if line and line[0] not in (";", "%"):
                        issues.report(
                            "Line not read", "NOT CUT", "dropped", "Remove from G-code", self.where
                        )
                    continue

            # on tool change action (M6)
            if self.re_toolchange(line):
                self.on_toolchange(line)
                continue

            bMotion = False
            bFeed = False
            bSpindle = False
            bMist = False
            bFlood = False

            # lines with valid start char
            for code_ in self.re_parts(line):
                # convert numeral
                code = [code_[0], float(code_[1])]
                if code[1].is_integer():
                    code[1] = int(code[1])
                # target coordinates
                if code[0] == "X":
                    self.X_pos = self._target(self.X_pos, code[1])
                    bMotion = True
                elif code[0] == "Y":
                    self.Y_pos = self._target(self.Y_pos, code[1])
                    bMotion = True
                elif code[0] == "Z":
                    self.Z_pos = self._target(self.Z_pos, code[1])
                    bMotion = True
                # params: feedrate, freq, tool
                elif code[0] == "F":
                    self.F_rate = self._scale(code[1])
                    bFeed = True
                elif code[0] == "S":
                    self.S_freq = code[1]
                elif code[0] == "T":
                    self.T_num = code[1]
                # spindle frequency change
                elif code[0] == "M" and code[1] in (3, 5):
                    if code[1] == 3:
                        self.S_on = True
                        bSpindle = True
                    elif code[1] == 5:
                        self.S_on = False
                        bSpindle = True
                # coolant valve change
                elif code[0] == "M" and code[1] in (7, 8, 9):
                    if code[1] == 7:
                        if not self.M_mist:
                            self.M_mist = True
                            bMist = True
                    elif code[1] == 8:
                        if not self.M_flood:
                            self.M_flood = True
                            bFlood = True
                    elif code[1] == 9:
                        if self.M_mist:
                            self.M_mist = False
                            bMist = True
                        if self.M_flood:
                            self.M_flood = False
                            bFlood = True
                # motion style change
                elif code[0] == "G" and code[1] in (0, 1):
                    self.G_motion = "G" + str(code[1])
                elif code[0] == "G" and code[1] in (20, 21):
                    self.unit_scale = 25.4 if code[1] == 20 else 1
                elif code[0] == "G" and code[1] in (90, 91):
                    self.G_absolute = code[1] == 90
                # handle reporting of unsupported gcode
                elif code[0] == "G" and code[1] in (2, 3):
                    issues.report(
                        "Arcs (G2/G3) unsupported",
                        "STRAIGHT LINE",
                        "changed",
                        "Export arcs as lines",
                        self.where,
                    )
                elif (code[0], code[1]) not in QUIET:
                    label = LABELS.get((code[0], code[1]), f"{code[0]}{code[1]}")
                    issues.report(
                        f"{label} unsupported",
                        "IGNORED",
                        "changed",
                        "Remove from G-code",
                        self.where,
                    )

            ### commit actions in right order
            # commit coolant
            if bMist:
                self.on_action(("MIST", self.M_mist))
                self.mists = True
            if bFlood:
                self.on_action(("FLOOD", self.M_flood))
                self.floods = True
            # commit spindle
            if bSpindle:
                if self.S_on:
                    self.on_action(("S", self.S_freq))
                    self.freqs.append(self.S_freq)
                else:
                    self.on_action(("S", 0))
            # commit feedrate
            if bFeed:
                self.on_action(("F", self.F_rate))
                self.rates.append(self.F_rate)
            # commit motion
            if bMotion:
                self.on_action((self.G_motion, (self.X_pos, self.Y_pos, self.Z_pos)))

        self.finalize_pass()
        return self.job


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path) as content_file:
        content = content_file.read()
        reader = GcodeReader()
        job = reader.parse(content)
        print(job)
