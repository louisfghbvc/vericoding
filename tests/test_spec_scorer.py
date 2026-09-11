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
    good_dfy = tmp_path / "good.dfy"
    good_dfy.write_text("""
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
    scorer = SpecScorer(str(good_dfy))
    res = scorer.analyze()
    assert res["overall_score"] >= 80
    assert res["confidence_level"] in ["MODERATE", "HIGH"]
