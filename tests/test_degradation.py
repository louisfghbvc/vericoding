"""How the analysis fails when it fails.

Every check here has a "something went wrong" branch, and all of them were
unexercised. The property that matters is not that they exist but which way
they lean: a crash inside the vacuity analysis must come out as NOT ANALYSED,
never as a clean bill. An analyser that reports "no vacuity found" because it
threw an exception is worse than one that is absent, because the report reads
the same as a real pass.
"""

import io
import sys

import pytest

from scripts import spec_scorer as scorer_mod
from scripts import verify_loop as verify_mod
from scripts.spec_scorer import DafnySpecParser, SpecScorer


def _analyse(tmp_path, source):
    path = tmp_path / "s.dfy"
    path.write_text(source)
    return SpecScorer(str(path)).analyze()


SPEC = """
method Check(n: int) returns (ok: bool)
  requires n >= 0
  ensures n < 0 ==> !ok
{ ok := true; }
"""


class _ExplodingSolver:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("solver blew up mid-analysis")


def test_a_crash_in_the_precondition_check_reports_not_analysed(tmp_path, monkeypatch):
    monkeypatch.setattr(scorer_mod.z3, "Solver", _ExplodingSolver)
    res = _analyse(tmp_path, SPEC)
    statuses = [g["status"] for g in res["methods"][0]["gaps"]]
    assert "NOT ANALYSED" in statuses
    assert not any("no Shape-A vacuity" in s for s in res["methods"][0]["strengths"]), \
        "a crashed check was reported as having ruled out vacuity"


def test_a_crash_in_the_postcondition_check_leaves_the_clause_unanalysed(tmp_path, monkeypatch):
    """Not vacuous and not live. The clause was never decided, and both other
    labels would claim it was."""
    monkeypatch.setattr(scorer_mod.z3, "Solver", _ExplodingSolver)
    method = _analyse(tmp_path, SPEC)["methods"][0]
    assert method["unanalysed_clauses"]
    assert method["vacuous_clauses"] == []
    assert method["live_ensures"] == []


def test_a_crashed_analysis_does_not_outscore_a_working_one(tmp_path, monkeypatch):
    """The spec in SPEC has a genuinely vacuous postcondition. If breaking the
    solver raised its score, every author with a flaky z3 would be rewarded
    for it."""
    working = _analyse(tmp_path, SPEC)["overall_score"]
    monkeypatch.setattr(scorer_mod.z3, "Solver", _ExplodingSolver)
    crashed = _analyse(tmp_path, SPEC)["overall_score"]
    assert crashed <= working, "breaking the analyser improved the score"


# --- parser edges -------------------------------------------------------

def test_an_unbalanced_body_does_not_swallow_the_rest_of_the_file(tmp_path):
    """A method whose braces never close is malformed Dafny. The extractor
    returns what it has rather than raising -- the scorer's job is to report
    on the spec, not to be the compiler."""
    methods = DafnySpecParser("""
    method Broken(x: int) returns (y: int)
      requires x > 0
    { y := x;
    """).methods
    assert len(methods) == 1
    assert methods[0]["requires"] == ["x > 0"]


def test_a_comparison_between_two_variables_is_understood(tmp_path):
    """`x > y` has no integer on the right. Falling back to an unparsed clause
    would drop one of the commonest shapes in real preconditions."""
    res = _analyse(tmp_path, """
    method M(x: int, y: int) returns (r: int)
      requires x > y
      requires y > x
      ensures r == x
    { r := x; }
    """)
    assert any(g["status"] == "CONTRADICTION" for g in res["methods"][0]["gaps"]), \
        "a variable-to-variable contradiction went unnoticed"


# --- output encoding ----------------------------------------------------

def test_output_is_made_encoding_safe_on_interpreters_without_reconfigure(monkeypatch):
    """Python 3.6 has no `reconfigure`. Without the TextIOWrapper fallback the
    report's non-ASCII markers raise UnicodeEncodeError under an ASCII locale
    and the tool dies while printing its own verdict.
    """
    class AsciiStream:
        def __init__(self):
            self.buffer = io.BytesIO()

    replacements = {"stdout": AsciiStream(), "stderr": AsciiStream()}
    for name, stream in replacements.items():
        monkeypatch.setattr(sys, name, stream)

    verify_mod._make_output_encoding_safe()
    try:
        assert hasattr(sys.stdout, "encoding")
        assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
        sys.stdout.write("✓ ✗ ⚠")          # must not raise
    finally:
        pass
