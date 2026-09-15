"""What the analyser does with what it cannot read, and the last CLI paths.

"NOT ANALYSED" is a reported state, not an internal one -- the gap report
tells a reviewer that a clause was neither credited nor cleared and that they
have to read it themselves. The paths that produce it were unexercised, which
means the honest-refusal machinery was itself unverified.

The remaining `main()` bodies are covered here too, in-process so that the
exercise is measured rather than merely performed.
"""

import sys

import pytest

from scripts import check_env as env_mod
from scripts import spec_scorer as scorer_mod
from scripts import verify_loop as verify_mod
from scripts.spec_scorer import SpecScorer, strip_comments


def _analyse(tmp_path, source):
    path = tmp_path / "s.dfy"
    path.write_text(source)
    return SpecScorer(str(path)).analyze()["methods"][0]


def _run_main(module, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", argv)
    try:
        module.main()
    except SystemExit as exit_info:
        return exit_info.code
    return 0


# --- the limits of the expression fragment ------------------------------

def test_a_clause_the_fragment_cannot_read_is_reported_not_credited(tmp_path):
    """Quantifiers are outside the supported fragment. The clause must appear
    as unanalysed rather than silently counting as protection."""
    method = _analyse(tmp_path, """
    method M(s: seq<int>) returns (r: int)
      requires |s| > 0
      ensures forall i :: 0 <= i < |s| ==> r >= s[i]
    { r := s[0]; }
    """)
    unreadable = method["unanalysed_clauses"]
    assert unreadable, "an unreadable clause was silently swallowed"
    assert method["vacuous_clauses"] == [], "unreadable is not the same as vacuous"


def test_an_unreadable_precondition_is_named_in_the_report(tmp_path):
    """A precondition outside the fragment did not constrain the satisfiability
    check, and the report names it -- 'something was skipped' is not actionable
    unless the reader is told which thing."""
    method = _analyse(tmp_path, """
    method M(s: seq<int>, x: int) returns (r: int)
      requires Valid(s)
      requires x > 10
      requires x < 100
      ensures r == x
    { r := x; }
    """)
    skipped = [g for g in method["gaps"] if g["status"] == "NOT ANALYSED"]
    assert any("Valid(s)" in g["message"] for g in skipped), \
        "the report did not say which clause went unchecked"


def test_a_partial_satisfiability_check_is_not_claimed_as_a_whole_one(tmp_path):
    """The claim must be no stronger than the check that produced it.

    With one precondition outside the fragment, the solver proved the readable
    SUBSET satisfiable. Reporting that as 'Preconditions are jointly
    satisfiable (no Shape-A vacuity)' overstates it, and overstates it in the
    flattering direction: if the excluded clause is unsatisfiable, the spec is
    vacuous and the summary line a reader skims says the opposite.
    """
    method = _analyse(tmp_path, """
    method M(s: seq<int>, x: int) returns (r: int)
      requires Valid(s)
      requires x > 10
      requires x < 100
      ensures r == x
    { r := x; }
    """)
    claims = " ".join(method["strengths"])
    assert "no Shape-A vacuity)" not in claims, \
        "a partial check was reported as having ruled out Shape-A vacuity"
    assert "not analysed" in claims, "the summary hid that a clause went unchecked"


def test_a_complete_satisfiability_check_still_claims_the_full_result(tmp_path):
    """The qualification above must not fire when nothing was skipped, or the
    scorer stops distinguishing a whole check from a partial one."""
    method = _analyse(tmp_path, """
    method M(x: int) returns (r: int)
      requires x > 10
      requires x < 100
      ensures r == x
    { r := x; }
    """)
    assert any("no Shape-A vacuity" in s for s in method["strengths"])


def test_a_partial_check_scores_below_a_complete_one(tmp_path):
    """The credit follows the claim. Otherwise a spec using constructs the
    analyser cannot read is worth exactly as much as one it fully checked."""
    body = """
    method M(s: seq<int>, x: int) returns (r: int)
    {0}  requires x > 10
      requires x < 100
      ensures r == x
    {{ r := x; }}
    """
    whole = tmp_path / "whole.dfy"
    partial = tmp_path / "partial.dfy"
    whole.write_text(body.format(""))
    partial.write_text(body.format("  requires Valid(s)\n  "))
    assert (SpecScorer(str(partial)).analyze()["overall_score"]
            < SpecScorer(str(whole)).analyze()["overall_score"])


def test_a_boolean_identifier_antecedent_is_within_the_fragment(tmp_path):
    """`success ==> ...` is the most common real shape. It has to be analysed,
    not reported as unreadable, or the report is mostly question marks."""
    method = _analyse(tmp_path, """
    method M(x: int) returns (ok: bool, y: int)
      requires x > 0
      ensures ok ==> y == x
      ensures !ok ==> y == 0
    { ok := true; y := x; }
    """)
    assert len(method["live_ensures"]) == 2
    assert method["unanalysed_clauses"] == []


def test_a_parenthesised_conjunction_is_unwrapped(tmp_path):
    method = _analyse(tmp_path, """
    method M(x: int) returns (y: int)
      requires (x > 0 && x < 100)
      ensures y == x
    { y := x; }
    """)
    assert not any(g["status"] == "NOT ANALYSED" and "x > 0" in g["message"]
                   for g in method["gaps"])


def test_strip_comments_leaves_positions_usable():
    """Newlines inside removed regions are preserved, so anything downstream
    that reasons about lines still lines up."""
    source = "a\n/* two\nlines */\nb\n"
    assert strip_comments(source).count("\n") == source.count("\n")


# --- the last CLI paths -------------------------------------------------

def test_score_cli_prints_the_report_and_warns_below_threshold(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "s.dfy"
    spec.write_text("method M(x: int) returns (y: int)\n  ensures y == x\n{ y := x; }\n")
    _run_main(scorer_mod, ["spec_scorer.py", str(spec)], monkeypatch)
    out = capsys.readouterr().out
    assert "SMT SPEC SCORING" in out
    assert "Spec Confidence" in out


def test_score_cli_emits_json_when_asked(tmp_path, monkeypatch, capsys):
    import json

    spec = tmp_path / "s.dfy"
    spec.write_text("method M(x: int) returns (y: int)\n  requires x > 0\n  ensures y == x\n{ y := x; }\n")
    _run_main(scorer_mod, ["spec_scorer.py", str(spec), "--json"], monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert "overall_score" in payload and "methods" in payload


def test_score_cli_rejects_a_missing_file(monkeypatch, capsys):
    code = _run_main(scorer_mod, ["spec_scorer.py", "/nonexistent/s.dfy"], monkeypatch)
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_verify_cli_exit_code_matches_the_published_table(tmp_path, monkeypatch, capsys):
    """SKILL.md tells an agent to branch on these. A missing Dafny is 2."""
    spec = tmp_path / "s.dfy"
    spec.write_text("method M() {}")
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: None)
    code = _run_main(verify_mod, ["verify_loop.py", str(spec)], monkeypatch)
    assert code == 2, "a missing toolchain is neither PROVED nor PROOF_FAILED"


def test_verify_cli_rejects_a_missing_file(monkeypatch, capsys):
    code = _run_main(verify_mod, ["verify_loop.py", "/nonexistent/s.dfy"], monkeypatch)
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_env_cli_exits_non_zero_when_verification_is_impossible(monkeypatch, capsys):
    monkeypatch.setattr(env_mod, "probe_verification",
                        lambda: {"ok": False, "reason": "no solver"})
    code = _run_main(env_mod, ["check_env.py", "--no-targets"], monkeypatch)
    assert code == 1
    assert "NOT POSSIBLE" in capsys.readouterr().out
