#!/usr/bin/env python3
"""Start DriveboardApp with the FREILab UI overlay. Takes the same arguments as backend/app.py.

Nothing upstream is modified: this wraps web._frontend_file, which serves every UI file, so that
  - files in freilab/web/ win over files of the same name in frontend/ (freilab.js, img/material/...)
  - app.html gets freilab.css/freilab.js and the dialogs of freilab_modals.html injected
  - the job library lists and loads only the jobs in freilab/library/, not upstream's demo jobs in library/
"""

import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
LIBRARY = os.path.join(HERE, "library")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))

import bottle  # noqa: E402
import config  # noqa: E402


def install(web):
    upstream = web._frontend_file

    def frontend_file(filename, *subdirs):
        if web.frontend_path == "frontend":  # not in mill mode
            if os.path.isfile(os.path.join(WEB, *subdirs, filename)):
                response = bottle.static_file(filename, root=os.path.join(WEB, *subdirs))
                response.set_header("Cache-Control", "no-cache")
                return response
            if filename == "app.html" and not subdirs:
                return _app_html(web)
        return upstream(filename, *subdirs)

    web._frontend_file = frontend_file

    # Library: only ours. Upstream's library/ holds demo jobs the machine does not need.
    get_sorted, get_path = web._get_sorted, web._get_path

    def library_sorted(globpattern, library=False, stripext=False):
        if not library:
            return get_sorted(globpattern, library, stripext)
        with web._queue_lock:
            mine = glob.glob(os.path.join(LIBRARY, globpattern))
        names = {os.path.basename(p) for p in mine if os.path.isfile(p)}
        return sorted(n[:-4] if stripext and n.endswith(".dba") else n for n in names)

    def library_path(jobname, library=False):
        if not library:
            return get_path(jobname, library)
        web._job_base(jobname, library)  # refuses names that are not plain file names
        mine = os.path.join(LIBRARY, jobname)
        for ext in (".dba", ".dba.starred"):
            if os.path.exists(mine + ext):
                return mine + ext
        raise bottle.HTTPResponse("No such file.", 400)

    def library_get(jobname, library=False):
        with web._queue_lock, open(library_path(jobname, library)) as fp:
            return fp.read()

    web._get_sorted, web._get_path, web._get = library_sorted, library_path, library_get


def _app_html(web):
    with open(os.path.join(config.conf["rootdir"], "frontend", "app.html"), encoding="utf-8") as fp:
        html = fp.read()
    with open(os.path.join(WEB, "freilab_modals.html"), encoding="utf-8") as fp:
        modals = fp.read()
    head = '<link rel="stylesheet" href="/freilab.css">\n<script src="/freilab.js"></script>\n</head>'
    html = html.replace("</head>", head, 1).replace("</body>", modals + "</body>", 1)
    return bottle.HTTPResponse(
        html, content_type="text/html; charset=UTF-8", Cache_Control="no-cache"
    )


# web must be imported after the config is loaded (see backend/app.py), so hook in right after it.
_load = config.load


def load(configname):
    result = _load(configname)
    import web

    install(web)
    return result


config.load = load

if __name__ == "__main__":
    import app

    app.main()
