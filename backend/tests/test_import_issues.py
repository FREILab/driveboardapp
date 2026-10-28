"""Per-import issue collector and ImportFailed."""

import pickle
import threading

import pytest
from jobimport import issues


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
