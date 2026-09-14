import os
import pytest
from scripts.spec_scorer import SpecScorer, DafnySpecParser


def test_parser_clauses():
    code = """
    method Test(x: int) returns (y: int)
        requires x > 0
        requires x < 100
        ensures y == x + 1
        ensures y > 0
        modifies this
    {
        y := x + 1;
    }
    """
    parser = DafnySpecParser(code)
    assert len(parser.methods) == 1
    m = parser.methods[0]
    assert m["name"] == "Test"
    assert len(m["requires"]) == 2
    assert len(m["ensures"]) == 2
    assert len(m["modifies"]) == 1


def test_scorer_vacuity_contradiction(tmp_path):
    bad_dfy = tmp_path / "bad.dfy"
    bad_dfy.write_text("""
    method Impossible(x: int)
        requires x > 10
        requires x < 5
        ensures true
    {
    }
    """)
    scorer = SpecScorer(str(bad_dfy))
    res = scorer.analyze()
    assert res["overall_score"] <= 30
    assert any(g["status"] == "CONTRADICTION" for g in res["methods"][0]["gaps"])


def test_scorer_high_confidence(tmp_path):
    """A spec whose postconditions actually constrain the implementation.

    The previous fixture here used `ensures res ==> true` / `ensures !res ==>
    true` and asserted a score >= 80. Both of those clauses are vacuous -- the
    consequent is literally `true`, so they hold for every implementation
    including one that ignores `amt` entirely. Asserting a high score on that
    fixture encoded the defect as the expected behaviour, which is why the
    scorer shipped unable to see it. The vacuous version now has its own test
    below, asserting the opposite.
    """
    good_dfy = tmp_path / "good.dfy"
    good_dfy.write_text("""
    method Withdraw(amt: int) returns (res: bool)
        requires amt > 0
        requires amt <= 1000
        modifies this
        ensures res ==> balance == old(balance) - amt
        ensures !res ==> balance == old(balance)
        ensures balance >= 0
    {
        res := true;
    }
    """)
    scorer = SpecScorer(str(good_dfy))
    res = scorer.analyze()
    assert res["overall_score"] >= 80
    assert res["confidence_level"] in ["MODERATE", "HIGH"]
    assert not res["methods"][0]["vacuous_clauses"]


def test_scorer_rejects_vacuous_ensures(tmp_path):
    """Shape B: postconditions that can never constrain anything.

    `X ==> true` is true for every X, so a spec built from these verifies any
    implementation while looking rigorous -- two preconditions, two
    postconditions, a clean proof. No Dafny flag reports it: not
    --analyze-proofs, not --warn-contradictory-assumptions, not
    --warn-redundant-assumptions (measured on 4.6.0 and 4.11.0). Auditing the
    spec is the only thing that catches it, which is what this scorer is for.
    """
    vacuous_dfy = tmp_path / "vacuous.dfy"
    vacuous_dfy.write_text("""
    method Withdraw(amt: int) returns (res: bool)
        requires amt > 0
        requires amt <= 1000
        modifies this
        ensures res ==> true
        ensures !res ==> true
    {
        res := true;
    }
    """)
    scorer = SpecScorer(str(vacuous_dfy))
    res = scorer.analyze()
    assert res["overall_score"] <= 30, "a spec that constrains nothing must not score well"
    assert len(res["methods"][0]["vacuous_clauses"]) == 2
    assert any(g["status"] == "VACUOUS" for g in res["methods"][0]["gaps"])


def test_scorer_rejects_unsatisfiable_antecedent(tmp_path):
    """Shape B, the subtler form: the antecedent is unreachable.

    Nothing here is contradictory and the consequent is meaningful, so the
    clause reads exactly like a real guard. It simply never fires, because the
    precondition already rules its antecedent out.
    """
    dead_dfy = tmp_path / "dead.dfy"
    dead_dfy.write_text("""
    method Check(n: int) returns (ok: bool)
        requires n >= 0
        ensures n < 0 ==> !ok
    {
        ok := true;
    }
    """)
    scorer = SpecScorer(str(dead_dfy))
    res = scorer.analyze()
    assert res["methods"][0]["vacuous_clauses"] == ["n < 0 ==> !ok"]
