"""How much of real Dafny the expression fragment can read.

This matters more than it looks. A clause the fragment cannot read is not
neutral: it is reported NOT ANALYSED, it earns no credit, and it now costs
the same as a proven-vacuous clause. So every shape that falls outside the
fragment is a correct specification being marked down for being ordinary,
and -- worse -- a Shape-A check that quietly does not happen.

Three shapes were outside it and should not have been: disjunction,
biconditional, and anything with a `&&` inside parentheses.
"""

import pytest

from scripts.spec_scorer import (HIGH_CONFIDENCE_MIN, SpecScorer,
                                 _split_top_level)


def _analyse(tmp_path, spec):
    path = tmp_path / "s.dfy"
    path.write_text(spec)
    res = SpecScorer(str(path)).analyze()
    return res, res["methods"][0]


def _statuses(tmp_path, spec):
    return [g["status"] for g in _analyse(tmp_path, spec)[1]["gaps"]]


PRE = "method M(x: int, y: int, s: seq<int>) returns (r: int)\n  requires %s\n  ensures r == x\n{ r := x; }\n"


# --- disjunction --------------------------------------------------------

@pytest.mark.parametrize("requires", [
    "x > 0 || y > 0",
    "(x > 10 || x < 1) && x == 20",
    "(x > 10 || x < 1) && x > 100",
])
def test_a_satisfiable_disjunction_is_analysed_and_not_flagged(tmp_path, requires):
    statuses = _statuses(tmp_path, PRE % requires)
    assert "NOT ANALYSED" not in statuses, "an ordinary disjunction was unreadable"
    assert "CONTRADICTION" not in statuses, "a satisfiable spec was called vacuous"


@pytest.mark.parametrize("requires", [
    "(x > 10 || x > 20) && x < 5",
    "(x > 10 || x < 1) && x == 5",
])
def test_a_contradiction_across_a_disjunction_is_found(tmp_path, requires):
    """Shape A that only shows up once `||` is understood -- every disjunct
    conflicts with the rest of the precondition, so the method is
    unreachable."""
    assert "CONTRADICTION" in _statuses(tmp_path, PRE % requires)


def test_disjunction_over_booleans_works_both_ways(tmp_path):
    sat = "method M(a: bool, b: bool) returns (r: int)\n  requires a || b\n  ensures r == 0\n{ r := 0; }\n"
    unsat = "method M(a: bool, b: bool) returns (r: int)\n  requires (a || b) && !a && !b\n  ensures r == 0\n{ r := 0; }\n"
    assert "CONTRADICTION" not in _statuses(tmp_path, sat)
    assert "CONTRADICTION" in _statuses(tmp_path, unsat)


def test_a_disjunction_with_one_unreadable_branch_is_all_or_nothing(tmp_path):
    """The soundness rule, and the reason it differs from conjunction.

    Dropping an unreadable CONJUNCT weakens the constraint: the solver may
    find a model the real precondition forbids, so a genuine contradiction
    goes unnoticed. That is a missed finding, already reported as NOT
    ANALYSED.

    Dropping an unreadable DISJUNCT strengthens it. `x > 0 || Valid(s)`
    reduced to `x > 0` rules out states the spec allows, and if what remains
    is unsatisfiable the tool reports CONTRADICTION against a specification
    that has none. A false accusation is the one output a reader cannot audit
    without redoing the work by hand.
    """
    statuses = _statuses(tmp_path, PRE % "x > 0 || Valid(s)")
    assert "NOT ANALYSED" in statuses
    assert "CONTRADICTION" not in statuses, \
        "an unreadable disjunct was dropped, strengthening the constraint"


def test_dropping_a_disjunct_would_manufacture_a_contradiction(tmp_path):
    """The failure the rule above prevents, made concrete: keep only the
    readable disjunct and the precondition becomes unsatisfiable."""
    statuses = _statuses(tmp_path, PRE % "(x < 0 || Valid(s)) && x > 5")
    assert "CONTRADICTION" not in statuses, \
        "reported a vacuous spec on the strength of a clause it could not read"


# --- biconditional ------------------------------------------------------

def test_a_biconditional_is_a_live_postcondition(tmp_path):
    """`<==>` contains `==>`. It used to match as an implication with the
    antecedent `ok <`, which parses as nothing -- so a correct, constraining
    postcondition was reported unanalysed, counted as zero live clauses, and
    told "no postcondition that can ever fire". It scored 15.
    """
    res, method = _analyse(tmp_path, """
    method M(x: int) returns (ok: bool)
      requires x > 0
      ensures ok <==> x > 10
    { ok := x > 10; }
    """)
    assert method["live_ensures"] == ["ok <==> x > 10"]
    assert method["unanalysed_clauses"] == []
    assert res["overall_score"] >= 60, (
        "a well-specified method scored %s" % res["overall_score"])


def test_a_real_implication_still_parses_as_one(tmp_path):
    """The lookbehind must not stop `==>` from matching."""
    _, method = _analyse(tmp_path, """
    method Check(n: int) returns (ok: bool)
      requires n >= 0
      ensures n < 0 ==> !ok
    { ok := true; }
    """)
    assert method["vacuous_clauses"] == ["n < 0 ==> !ok"]


# --- depth-aware splitting ----------------------------------------------

@pytest.mark.parametrize("text,sep,expected", [
    ("a && b", "&&", ["a", "b"]),
    ("f(a && b) > 0", "&&", ["f(a && b) > 0"]),
    ("(a || b) && c", "&&", ["(a || b)", "c"]),
    ("(a && b) || c", "||", ["(a && b)", "c"]),
    ("a", "&&", ["a"]),
])
def test_splitting_respects_parentheses(text, sep, expected):
    assert _split_top_level(text, sep) == expected


def test_an_operator_inside_a_call_does_not_split_the_clause(tmp_path):
    """`f(x && y) > 0` is one uninterpreted term. Splitting it yields two
    fragments that parse as nothing, which reads as two problems instead of
    one unreadable clause."""
    statuses = _statuses(tmp_path, PRE % "f(x && y) > 0")
    assert "NOT ANALYSED" in statuses
    assert "CONTRADICTION" not in statuses


# --- the headline must agree with the notes ------------------------------

def test_an_unread_clause_forbids_a_high_confidence_verdict(tmp_path):
    """The report cannot lead with complete confidence and then admit it did
    not read part of the spec.

    The bundled example printed exactly that:

        Spec Confidence  : 100.0% [HIGH]
        ? [NOT ANALYSED] precondition `Valid()` was not expressible ...

    The per-clause penalties existed and were correct; the clamp at 100
    swallowed them, because the method scored well past the ceiling on its
    other merits. A reader who trusts the headline never reaches the note.

    This fixture is deliberately one that scores 100 with the cap removed --
    an earlier version of this test used a spec that landed at 82 either way,
    so it asserted a true thing without pinning the behaviour, and deleting
    the cap left it green.
    """
    res, _ = _analyse(tmp_path, """
    method Withdraw(amount: int) returns (ok: bool)
      requires Valid()
      requires amount > 0
      modifies this
      ensures Valid()
      ensures ok ==> balance == old(balance) - amount
      ensures !ok ==> balance == old(balance)
      ensures balance >= 0
    { ok := false; }
    """)
    assert any(g["status"] == "NOT ANALYSED" for g in res["methods"][0]["gaps"])
    assert res["confidence_level"] != "HIGH"
    assert res["overall_score"] < HIGH_CONFIDENCE_MIN


def test_a_fully_readable_spec_can_still_reach_high(tmp_path):
    """The cap must not become a ceiling on everything -- otherwise HIGH is
    unreachable and the band carries no information."""
    res, method = _analyse(tmp_path, """
    method Withdraw(amount: int, balance: int) returns (ok: bool, newBalance: int)
      requires amount > 0
      requires balance >= 0
      ensures ok ==> newBalance == balance - amount
      ensures !ok ==> newBalance == balance
      ensures newBalance >= 0
    { ok := false; newBalance := balance; }
    """)
    assert not any(g["status"] == "NOT ANALYSED" for g in method["gaps"])
    assert res["confidence_level"] == "HIGH"


def test_the_bundled_example_reports_what_it_actually_established():
    """End to end on a file that ships with the tool, so the number a new
    reader sees first is one the tool can defend."""
    res = SpecScorer("examples/bank_account/bank.dfy").analyze()
    unread = [g for m in res["methods"] for g in m["gaps"]
              if g["status"] == "NOT ANALYSED"]
    assert unread, "fixture drifted: this example is meant to have an unread clause"
    assert res["confidence_level"] != "HIGH"
