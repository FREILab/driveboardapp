"""Per-import issue collector: importers report, the web layer collects."""

import contextlib
import contextvars
import logging

log = logging.getLogger("jobimport")

KINDS = ("changed", "replaced", "dropped")

_active = contextvars.ContextVar("import_issues", default=None)


class Collector:
    def __init__(self):
        self._rows = {}  # (problem, result, kind, fix) -> row, insertion ordered

    def add(self, problem, result, kind, fix, where):
        key = (problem, result, kind, fix)
        row = self._rows.get(key)
        if row is None:
            self._rows[key] = {
                "problem": problem,
                "where": where,
                "result": result,
                "kind": kind,
                "fix": fix,
                "count": 1,
            }
        else:
            row["count"] += 1

    def issues(self):
        return [dict(r) for r in self._rows.values()]


@contextlib.contextmanager
def collecting():
    collector = Collector()
    token = _active.set(collector)
    try:
        yield collector
    finally:
        _active.reset(token)


def report(problem, result, kind, fix, where=None):
    if kind not in KINDS:
        raise ValueError(f"unknown issue kind: {kind!r}")
    log.warning("%s%s", problem, f" ({where})" if where else "")
    collector = _active.get()
    if collector is not None:
        collector.add(problem, result, kind, fix, where)


def snapshot():
    collector = _active.get()
    return collector.issues() if collector else []


class ImportFailed(ValueError):
    """Import cannot proceed; carries a user-facing problem and fix."""

    def __init__(self, problem, fix, where=None):
        super().__init__(problem, fix, where)
        self.issue = {"problem": problem, "where": where, "fix": fix}

    def __str__(self):
        problem, _, where = self.args
        return f"{problem} ({where})" if where else problem
