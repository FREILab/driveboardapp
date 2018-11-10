
import json

from config import conf
from svg_reader import SVGReader
from dxf_parser import DXFParser
from gcode_reader import GcodeReader
import pathoptimizer


__author__ = 'Stefan Hechenberger <stefan@nortd.com>'



def convert(job, optimize=True, tolerance=conf['tolerance']):
    """Convert a job string (dba, svg, dxf, or gcode).

    Args:
        job: Parsed dba or job string (dba, svg, dxf, or gcode).
        optimize: Flag for optimizing path tolerances.
        tolerance: Tolerance used in convert/optimization.

    Returns:
        A parsed .dba job.
    """
    type_ = get_type(job)
    if type_ == 'dba':
        if type(job) in (str, unicode):
            job = json.loads(job)
        if optimize:
            if 'defs' in job:
                for def_ in job['defs']:
                    if def_['kind'] == "path":
                        pathoptimizer.optimize(def_['data'], tolerance)
                if not 'head' in job:
                    job['head'] = {}
                job['head']['optimized'] = tolerance
    elif type_ == 'svg':
        job = read_svg(job, conf['workspace'],
                       tolerance, optimize=optimize)
    elif type_ == 'dxf':
        job = read_dxf(job, tolerance, optimize=optimize)
    elif type_ == 'gcode':
        job = read_gcode(job, tolerance, optimize=optimize)
    else:
        print "ERROR: file type not recognized"
        raise TypeError
    return job


def read_svg(svg_string, workspace, tolerance, forced_dpi=None, optimize=True):
    """Read a svg file string and convert to dba job."""
    svgReader = SVGReader(tolerance, workspace)
    res = svgReader.parse(svg_string, forced_dpi)
    # {'boundarys':b, 'dpi':d, 'lasertags':l, 'rasters':r}

    # create an dba job from res
    # TODO: reader should generate an dba job to begin with
    job = {'head':{}, 'passes':[], 'items':[], 'defs':[]}
    if 'rasters' in res:
        for raster in res['rasters']:
            job['defs'].append({"kind":"image",
                                "data":raster['data'] ,
                                "pos":raster['pos'] ,
                                "size": raster['size']})
            job['items'].append({"def":len(job['defs'])-1})

    if 'boundarys' in res:
        if 'dpi' in res:
            job['head']['dpi'] = res['dpi']
        for color,path in res['boundarys'].iteritems():
            if optimize:
                pathoptimizer.optimize(path, tolerance)
            job['defs'].append({"kind":"path",
                                "data":path})
            job['items'].append({"def":len(job['defs'])-1, "color":color})
        if optimize:
            job['head']['optimized'] = tolerance

    if 'lasertags' in res:
        # format: [('12', '2550', '', '100', '%', ':#fff000', ':#ababab', ':#ccc999', '', '', '')]
        # sort lasertags by pass number
        def _cmp(a, b):
            if a[0] < b[0]: return -1
            elif a[0] > b[0]: return 1
            else: return 0
        res['lasertags'].sort(_cmp)
        # add tags ass passes
        for tag in res['lasertags']:
            if len(tag) == 11:
                idxs = []
                for colidx in range(5,10):
                    color = tag[colidx]
                    i = 0
                    for item in job['items']:
                        if 'color' in item and item['color'] == color:
                            idxs.append(i)
                        i += 1
                if "passes" not in job:
                    job["passes"] = []
                job["passes"].append({
                    "items": idxs,
                    "feedrate": tag[1],
                    "intensity": tag[3]
                })
    return job

def read_dxf(dxf_string, tolerance, optimize=True):
    """Read a dxf file string and optimize returned value."""
    dxfParser = DXFParser(tolerance)
    # second argument is the forced unit, TBI in Driverboard
    job = dxfParser.parse(dxf_string, None)
    if 'vector' in job:
        if optimize:
            vec = job['vector']
            pathoptimizer.dxf_optimize(vec['paths'], tolerance)
            vec['optimized'] = tolerance
    return job

def read_gcode(gcode_string, tolerance, optimize=False):
    """Read a gcode file string and convert to dba job."""
    reader = GcodeReader()
    job = reader.parse(gcode_string)
    if optimize:
        pass
    return job


def get_type(job):
    """Figure out file type from job string."""
    # figure out type
    if type(job) is dict:
        type_ = 'dba'
    elif type(job) is str or type(job) is unicode:
        jobheader = job[:1024].lstrip()
        if jobheader and jobheader[0] == '{':
            type_ = 'dba'
        elif '<?xml' in jobheader and '<svg' in jobheader:
            type_ = 'svg'
        elif 'SECTION' in jobheader and 'HEADER' in jobheader:
            type_ = 'dxf'
        elif 'G0' in jobheader or 'G1' in jobheader or \
             'G00' in jobheader or 'G01' in jobheader or \
             'g0' in jobheader or 'g1' in jobheader or \
             'g00' in jobheader or 'g01' in jobheader:
            type_ = 'gcode'
        else:
            print "ERROR: Cannot figure out file type 1."
            raise TypeError
    else:
        print "ERROR: Cannot figure out file type 2."
        raise TypeError
    return type_
