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


def _clauses(src):
    m = DafnySpecParser(src).methods
    assert m, "parser found no methods"
    return m[0]["requires"], m[0]["ensures"]


def test_parser_ignores_commented_out_clauses():
    """A commented-out clause is not a clause.

    This ran the wrong way before: the parser read `// ensures ...` as real,
    so adding two commented-out postconditions to an otherwise identical spec
    moved its score from 70% MODERATE to 85% HIGH. Commenting a clause out
    increased confidence in the specification.
    """
    requires, ensures = _clauses("""
    method Safe(x: int) returns (y: int)
      // ensures y > 9999
      /* requires false */
      requires x > 0
      ensures y == x
    { y := x; }
    """)
    assert requires == ["x > 0"]
    assert ensures == ["y == x"]


def test_parser_handles_nested_block_comments():
    """Dafny block comments nest; a naive scan stops at the first `*/`."""
    requires, ensures = _clauses("""
    method Safe(x: int) returns (y: int)
      /* outer /* inner ensures y > 5 */ still comment */
      requires x > 0
      ensures y == x
    { y := x; }
    """)
    assert requires == ["x > 0"]
    assert ensures == ["y == x"]


def test_parser_keeps_slashes_inside_string_literals():
    """Stripping comments must not eat a `//` that is part of the program."""
    requires, ensures = _clauses("""
    method Safe(x: int) returns (y: int)
      requires x > 0
      ensures y == x
    { print "not // a comment"; y := x; }
    """)
    assert ensures == ["y == x"]


def test_parser_joins_multi_line_clauses():
    """A clause wrapped for readability is one clause, not a truncated one.

    Terminating at the newline yielded the fragment `y == x &&`, which is not
    a weaker version of the clause -- it is broken syntax that the vacuity
    analysis then tried to interpret.
    """
    _, ensures = _clauses("""
    method Safe(x: int) returns (y: int)
      requires x > 0
      ensures y == x &&
              y > 0
    { y := x; }
    """)
    assert ensures == ["y == x && y > 0"]


def test_commenting_out_clauses_cannot_raise_the_score(tmp_path):
    """The regression that motivated all of the above, asserted end to end."""
    body = """
    method Safe(x: int) returns (y: int)
      requires x > 0
      ensures y == x
    {0}
    {{ y := x; }}
    """
    honest = tmp_path / "honest.dfy"
    padded = tmp_path / "padded.dfy"
    honest.write_text(body.format(""))
    padded.write_text(body.format("  // ensures y > 0\n      // ensures y != -1"))

    honest_score = SpecScorer(str(honest)).analyze()["overall_score"]
    padded_score = SpecScorer(str(padded)).analyze()["overall_score"]
    assert honest_score == padded_score, (
        "commenting clauses out changed the score: "
        "{} vs {}".format(honest_score, padded_score)
    )
