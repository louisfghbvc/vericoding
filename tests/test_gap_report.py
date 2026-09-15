"""The gap report, which is the artifact a human approves.

Stage 4 of the method is a person reading this and deciding whether the
specification says what they meant. Everything upstream -- the vacuity
analysis, the satisfiability checks -- reaches them only through these lines.
A vacuous clause rendered as a tick, or a legend that lets "not analysed" read
as "fine", undoes the analysis at the one point it is meant to be used.

Coverage found the whole renderer and several scoring branches unexercised.
"""

import pytest

from scripts.spec_scorer import SpecScorer, format_cli_report


def _score(tmp_path, source, name="s.dfy"):
    path = tmp_path / name
    path.write_text(source)
    return SpecScorer(str(path)).analyze()


def test_a_vacuous_clause_is_a_cross_not_a_tick(tmp_path):
    res = _score(tmp_path, """
    method Check(n: int) returns (ok: bool)
      requires n >= 0
      ensures n < 0 ==> !ok
    { ok := true; }
    """)
    out = format_cli_report(res)
    # The icon has to be on the VACUOUS line itself. Asserting that "✗" appears
    # anywhere in the report passes even when the vacuous clause is downgraded
    # to a warning, because some other line supplies the cross -- a mutation
    # swapping the VACUOUS icon for ⚠ left this test green until it read the
    # line rather than the file.
    vacuous_lines = [ln for ln in out.splitlines() if "[VACUOUS]" in ln]
    assert vacuous_lines, "no VACUOUS finding rendered"
    for line in vacuous_lines:
        assert line.lstrip().startswith("✗"), (
            "a vacuous clause rendered as {!r}; it is a defect, not a warning"
            .format(line.strip()[:1]))
    assert "n < 0 ==> !ok" in out, "the reader has to be told which clause"
    assert "never fires" in out


def test_the_legend_says_a_question_mark_is_not_a_pass(tmp_path):
    """The failure this whole tool guards against, applied to itself: a clause
    the analyser could not read is neither credited nor cleared, and the
    reader has to know that."""
    res = _score(tmp_path, """
    method M(s: seq<int>) returns (r: int)
      requires |s| > 0
      ensures r == s[0]
    { r := s[0]; }
    """)
    out = format_cli_report(res)
    assert "Legend" in out
    assert "not analysed" in out
    assert "A '?' is not a pass" in out


def test_a_spec_with_no_preconditions_is_marked_underspecified(tmp_path):
    res = _score(tmp_path, """
    method M(x: int) returns (y: int)
      ensures y == x
    { y := x; }
    """)
    out = format_cli_report(res)
    assert "Precondition" in out
    assert "UNDERSPECIFIED" in out


def test_a_spec_with_no_postconditions_is_marked_unaddressed(tmp_path):
    """Nothing constrains the return value, so every implementation satisfies
    it. That is the strongest statement the report can make and it must not be
    softened to a warning."""
    res = _score(tmp_path, """
    method M(x: int) returns (y: int)
      requires x > 0
    { y := x; }
    """)
    out = format_cli_report(res)
    unaddressed = [ln for ln in out.splitlines() if "[UNADDRESSED]" in ln]
    assert unaddressed, "no UNADDRESSED finding rendered"
    assert all(ln.lstrip().startswith("✗") for ln in unaddressed)
    assert res["overall_score"] < 60


def test_the_header_carries_the_score_and_the_confidence_band(tmp_path):
    res = _score(tmp_path, """
    method M(x: int) returns (y: int)
      requires x > 0
      ensures y == x
    { y := x; }
    """)
    out = format_cli_report(res)
    assert "Spec Confidence" in out
    assert str(res["overall_score"]) in out
    assert res["confidence_level"] in out


def test_a_file_with_no_methods_says_so_rather_than_scoring_zero(tmp_path):
    """An empty result and a bad result are different answers. Scoring an
    unparsed file 0% would read as 'this specification is terrible' rather
    than 'nothing was found to score'."""
    res = _score(tmp_path, "// just a comment, no methods here\n")
    assert res["methods_count"] == 0
    assert any("No formal methods" in g for g in res["summary_gaps"])


def test_a_body_vacuity_finding_replaces_the_clause_analysis(tmp_path):
    """`assume false` makes every clause below it meaningless, so reporting
    them individually would invite fixing the wrong thing."""
    res = _score(tmp_path, """
    method M(x: int) returns (y: int)
      requires x > 0
      ensures y == x
      ensures y > 0
    {
      assume false;
      y := -1;
    }
    """)
    out = format_cli_report(res)
    assert res["overall_score"] == 0
    assert "Body Vacuity" in out
    assert "any implementation" in out.lower()
