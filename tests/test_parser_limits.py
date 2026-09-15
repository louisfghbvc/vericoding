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


# --- the analyser without a solver --------------------------------------

def _analyse_without_z3(tmp_path, source, monkeypatch):
    monkeypatch.setattr(scorer_mod, "z3", None)
    path = tmp_path / "s.dfy"
    path.write_text(source)
    return scorer_mod.SpecScorer(str(path)).analyze()


VACUOUS_BY_READING = """
method Withdraw(amt: int) returns (res: bool)
  requires amt > 0
  requires amt <= 1000
  ensures res ==> true
  ensures !res ==> true
{ res := true; }
"""


def test_blatant_shape_b_is_caught_without_a_solver(tmp_path, monkeypatch):
    """`X ==> true` is vacuous by reading, not by proving.

    `requires false` has always been caught ahead of the solver for exactly
    this reason. Leaving the mirror shapes behind the z3 guard made the
    verdict depend on whether a package happened to be installed: on a host
    without z3 the textbook Shape-B spec came back "not checked" instead of
    "vacuous" -- honest, but weaker than the available evidence.
    """
    res = _analyse_without_z3(tmp_path, VACUOUS_BY_READING, monkeypatch)
    assert res["methods"][0]["vacuous_clauses"] == ["res ==> true", "!res ==> true"]


def test_the_solverless_verdict_matches_the_solver_one_here(tmp_path, monkeypatch):
    """Same spec, same answer, with and without z3. Where the evidence does
    not depend on the solver, neither should the report."""
    with_z3 = scorer_mod.SpecScorer  # module-level z3 still present
    path = tmp_path / "w.dfy"
    path.write_text(VACUOUS_BY_READING)
    scored = with_z3(str(path)).analyze()["overall_score"]
    without = _analyse_without_z3(tmp_path, VACUOUS_BY_READING, monkeypatch)["overall_score"]
    assert scored == without


def test_a_clause_needing_the_solver_is_still_unanalysed_without_one(tmp_path, monkeypatch):
    """The relaxation must not leak. An unsatisfiable antecedent is not
    decidable by reading, so with no solver it stays unanalysed -- never
    counted as live, which would credit a clause nobody checked."""
    res = _analyse_without_z3(tmp_path, """
    method Check(n: int) returns (ok: bool)
      requires n >= 0
      ensures n < 0 ==> !ok
    { ok := true; }
    """, monkeypatch)
    method = res["methods"][0]
    assert method["unanalysed_clauses"], "the clause was silently resolved without a solver"
    assert method["live_ensures"] == [], "an unchecked clause was credited as live"


def test_a_plain_postcondition_needs_no_solver_to_be_live(tmp_path, monkeypatch):
    """A non-implication has no antecedent to be vacuous, so withholding the
    solver must not demote it -- otherwise every spec on a z3-less host reads
    as unverifiable."""
    res = _analyse_without_z3(tmp_path, """
    method M(x: int) returns (y: int)
      requires x > 0
      ensures y == x
    { y := x; }
    """, monkeypatch)
    assert res["methods"][0]["live_ensures"] == ["y == x"]


# --- parenthesised and negated-literal preconditions --------------------

def _statuses(tmp_path, requires):
    src = ("method M(x: int, y: int) returns (r: int)\n"
           "  requires %s\n  ensures r == x\n{ r := x; }\n" % requires)
    return [g["status"] for g in _analyse(tmp_path, src)["gaps"]]


@pytest.mark.parametrize("requires", [
    "(x > 0) && (x < 5)",
    "(x > 0) && (x < 5) && (y > 1)",
    "((x > 0)) && ((x < 5))",
    "(x > 0 && x < 5)",
])
def test_parenthesised_conjuncts_are_analysed(tmp_path, requires):
    """Idiomatic Dafny, previously unreadable.

    The paren-stripper counted parens instead of matching them. For
    `(x > 0) && (x < 5)` the counts match, the string starts with `(` and ends
    with `)`, and the inner slice `x > 0) && (x < 5` has matching counts too --
    so it stripped, twice, and handed the splitter mangled text. Both conjuncts
    then failed to parse and the clause came back NOT ANALYSED.
    """
    assert "NOT ANALYSED" not in _statuses(tmp_path, requires)


def test_a_contradiction_hidden_behind_parentheses_is_found(tmp_path):
    """What the parser bug actually cost.

    These preconditions have no common solution, so the method is unreachable
    and any body verifies -- textbook Shape A. Because the clause did not
    parse, the scorer reported NOT ANALYSED and scored it 65 instead of 0.
    An unreadable clause is not a neutral outcome; it silently disables the
    check that clause was there to feed.
    """
    assert "CONTRADICTION" in _statuses(tmp_path, "(x > 10) && (x < 5)")


def test_a_negated_true_is_recognised_as_false(tmp_path):
    """`requires !true` is `requires false` wearing a hat.

    Literal handling lived in `_conjunction`, which only ever matched the bare
    forms, so `!true` fell through to `_atom` and came back unparsed: NOT
    ANALYSED, score 65, while the identical `requires false` scored 0.
    """
    assert "CONTRADICTION" in _statuses(tmp_path, "!true")
    assert "CONTRADICTION" in _statuses(tmp_path, "x > 0 && !true")


def test_a_negated_false_does_not_poison_a_good_precondition(tmp_path):
    """The mirror direction. `!false` is simply true, and must not be mistaken
    for a contradiction now that the negation is understood."""
    statuses = _statuses(tmp_path, "!false && x > 0")
    assert "CONTRADICTION" not in statuses
    assert "NOT ANALYSED" not in statuses


def test_unwrapping_still_refuses_what_it_cannot_read(tmp_path):
    """The relaxation must not turn into permissiveness: an uninterpreted
    predicate is still reported, not quietly assumed."""
    assert "NOT ANALYSED" in _statuses(tmp_path, "Valid(x)")


@pytest.mark.parametrize("text,wraps", [
    ("(a && b)", True),
    ("(a) && (b)", False),
    ("((a))", True),
    ("(a) && (b) && (c)", False),
    ("(a && (b)) ", True),
    ("a && b", False),
])
def test_wraps_whole_matches_rather_than_counts(text, wraps):
    from scripts.spec_scorer import _wraps_whole
    assert _wraps_whole(text.strip()) is wraps
